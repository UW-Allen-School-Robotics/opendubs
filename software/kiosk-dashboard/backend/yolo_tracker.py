"""
YOLO-based face tracking pipeline for OpenDubs Tab5 using Intel RealSense.

Connects to a RealSense stream, runs YOLOv8 face detection,
calculates pan/tilt offsets for the closest face to keep it centered,
and sends UDP pan/tilt commands to the Tab5.
"""

import argparse
import socket
import struct
import time
import math

import cv2
import torch
import os
import urllib.request
from ultralytics import YOLO
import numpy as np
import pyrealsense2 as rs
import csv
import os

# Configuration
UDP_IP = "opendubs-tab5.local"
UDP_PORT = 4242

# Data log path — written to /tmp so it survives across tracker restarts within a session
LOG_PATH = "/tmp/tracker_log.csv"

# CAN IDs
CAN_ID_PAN_TILT_CMD = 0x104
CAN_ID_TAB5_STATUS = 0x106

# Maximum servo command rate. Sending faster than this:
#   (a) causes current spikes (absolute-angle jumps) that brownout the Tab5's ESP32
#   (b) floods the LAN, starving the StamPLC telemetry heartbeat which makes the
#       robot display "INIT booting" whenever the AI is running.
_CMD_INTERVAL_S = 0.10   # 10 Hz — fast enough to track a walking person smoothly

# Maximum angle change allowed per UDP send. At 10 Hz this limits slew to
# 37.5 deg/s — extremely responsive tracking. Keep an eye out for brownouts.
_MAX_DEG_PER_SEND = 3.75

class Tab5Controller:
    def __init__(self, ip=UDP_IP, port=UDP_PORT, pan_min=0, pan_max=180, tilt_min=0, tilt_max=180):
        self.ip = ip
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Resolve IP once to avoid DNS delays in the loop
        try:
            self.resolved_ip = socket.gethostbyname(self.ip)
        except socket.gaierror:
            print(f"Warning: Could not resolve {self.ip}. Will try on the fly.")
            self.resolved_ip = self.ip

        # Listen for Tab5 status to know when manual controller is active
        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock_rx.bind(("", 5006))
            self.sock_rx.setblocking(False)
        except:
            print("Warning: Could not bind to port 5006 for listening.")

        self.is_manual = False

        # Head position as last commanded to the servo.
        # Starts centred. This is the ONLY value we send; we never jump.
        self.pan_angle  = 90.0
        self.tilt_angle = 90.0

        # EMA-smoothed pixel error — the only state the P controller needs.
        # (Smoothing the error, not the angle, means no integrator windup and
        # a natural stable equilibrium when the face is centred.)
        self._smooth_err_x = 0.0
        self._smooth_err_y = 0.0
        self._alpha = 0.10   # smoothing factor: lower = filters out jitter

        # Deadzone (pixels): ignore small errors to prevent jitter when centred.
        self._deadzone_px = 45

        # Pure-P gain: pixels of error → degrees of correction.
        # RealSense at 640 px wide / ~87° FOV ≈ 7.4 px/°.  We use 0.15 °/px
        # so a 100 px error → 15° correction. Extremely high responsiveness.
        self._kp = 0.15

        # Constraints
        self.pan_min,  self.pan_max  = pan_min,  pan_max
        self.tilt_min, self.tilt_max = tilt_min, tilt_max

        # Rate-limiting: track when we last sent a command
        self._last_send_t = 0.0

        # CSV data log
        log_exists = os.path.exists(LOG_PATH)
        self._log_f  = open(LOG_PATH, "a", newline="", buffering=1)
        self._log_csv = csv.writer(self._log_f)
        if not log_exists:
            self._log_csv.writerow([
                "timestamp", "face_cx", "face_cy", "err_x", "err_y",
                "smooth_err_x", "smooth_err_y",
                "desired_pan", "desired_tilt",
                "sent_pan", "sent_tilt", "is_manual",
            ])
        print(f"[tracker] logging to {LOG_PATH}")

    def _desired_pan(self):
        """Pure-P target: centre + proportional correction from smoothed error."""
        raw = 90.0 - self._smooth_err_x * self._kp
        return max(self.pan_min, min(self.pan_max, raw))

    def _desired_tilt(self):
        """Pure-P target: centre + proportional correction from smoothed error."""
        raw = 90.0 - self._smooth_err_y * self._kp
        return max(self.tilt_min, min(self.tilt_max, raw))

    def update_angles(self, face_cx, face_cy, error_x, error_y):
        """Update EMA-smoothed pixel error from one YOLO detection.

        Pure-P design: the desired angle is always computed fresh from the
        current smoothed error, so there is no integrator and no windup.
        The stable equilibrium is simply error = 0 (face centred).
        Called every camera frame (up to 30 Hz).
        """
        # Deadzone: zero out small errors to prevent jitter
        if abs(error_x) < self._deadzone_px: error_x = 0
        if abs(error_y) < self._deadzone_px: error_y = 0

        # EMA on the raw pixel error
        self._smooth_err_x += self._alpha * (error_x - self._smooth_err_x)
        self._smooth_err_y += self._alpha * (error_y - self._smooth_err_y)

        # Log every frame for debugging
        self._log_csv.writerow([
            f"{time.time():.3f}",
            face_cx, face_cy, error_x, error_y,
            f"{self._smooth_err_x:.2f}", f"{self._smooth_err_y:.2f}",
            f"{self._desired_pan():.2f}", f"{self._desired_tilt():.2f}",
            f"{self.pan_angle:.2f}", f"{self.tilt_angle:.2f}",
            int(self.is_manual),
        ])

    def send_command_if_due(self):
        """Send a servo command if the rate-limit window has elapsed.

        Steps pan_angle / tilt_angle toward the pure-P desired angle by at most
        _MAX_DEG_PER_SEND degrees per send, then sends one UDP packet.
        """
        now = time.time()
        if now - self._last_send_t < _CMD_INTERVAL_S:
            return
        self._last_send_t = now

        target_pan  = self._desired_pan()
        target_tilt = self._desired_tilt()

        def _step(current, target, limit):
            delta = target - current
            delta = max(-limit, min(limit, delta))
            return current + delta

        self.pan_angle  = _step(self.pan_angle,  target_pan,  _MAX_DEG_PER_SEND)
        self.tilt_angle = _step(self.tilt_angle, target_tilt, _MAX_DEG_PER_SEND)

        self.pan_angle  = max(self.pan_min,  min(self.pan_max,  self.pan_angle))
        self.tilt_angle = max(self.tilt_min, min(self.tilt_max, self.tilt_angle))

        self._send_raw()

    def _send_raw(self):
        """Send the CAN_Pan_Tilt_Command over UDP."""
        # Struct format: 
        # uint32_t can_id (4 bytes, little endian: '<I')
        # uint8_t can_dlc (1 byte: 'B')
        # uint8_t data[8] (8 bytes: 2 bytes pan, 2 bytes tilt, 4 bytes pad: '<hh4x')
        pan = int(self.pan_angle)
        tilt = int(self.tilt_angle)
        
        can_id = CAN_ID_PAN_TILT_CMD
        can_dlc = 4  # 2 + 2 bytes
        
        # Pack into 13 bytes
        # <I B h h 4x
        packet = struct.pack('<IBhh4x', can_id, can_dlc, pan, tilt)
        
        # Resolve IP on the fly if it failed initially
        target_ip = self.resolved_ip
        if target_ip == self.ip:
            try:
                target_ip = socket.gethostbyname(self.ip)
                self.resolved_ip = target_ip # cache it
            except socket.gaierror:
                pass # print error below if send fails

        try:
            self.sock.sendto(packet, (target_ip, self.port))
        except Exception as e:
            # Only print occasionally to avoid spam
            if not hasattr(self, 'last_err') or time.time() - self.last_err > 2:
                print(f"UDP Send Error (Is {self.ip} online?): {e}")
                self.last_err = time.time()

    def check_manual_override(self):
        """Listen to Tab5 status to see if the user is driving manually."""
        # Drain the socket looking for the MOST RECENT status packet
        while True:
            try:
                data, _ = self.sock_rx.recvfrom(64)
                if len(data) >= 13:
                    can_id, = struct.unpack('<I', data[:4])
                    if can_id == CAN_ID_TAB5_STATUS:
                        # Data layout: [5]=robot_state, [6]=link_online, [7]=tracking, [8]=ui_mode
                        # [9:11]=current_pan, [11:13]=current_tilt
                        tracking = data[7]
                        ui_mode = data[8]
                        
                        # Sync is_manual flag only — do NOT sync pan_angle/tilt_angle
                        # from the Tab5 status. g_targetPan=0 when AI has never commanded
                        # it, so blindly syncing corrupts our reference position and causes
                        # an 89° servo jump when AI next resumes.
                        prev_is_manual = self.is_manual
                        self.is_manual = (ui_mode == 1) or (tracking == 0)

                        # On manual → AI transition: reset to safe center position.
                        # This prevents sending a jump from the corrupted 0° position.
                        if prev_is_manual and not self.is_manual:
                            self.pan_angle  = 90.0
                            self.tilt_angle = 90.0
                            self._smooth_err_x = 0.0
                            self._smooth_err_y = 0.0
                            print(f"[tracker] manual→AI transition: reset to center")
            except BlockingIOError:
                break
            except Exception:
                break

def main():
    parser = argparse.ArgumentParser(description="THOR YOLO Face Tracker (Intel RealSense)")
    parser.add_argument("--show", action="store_true", help="Show GUI window")
    parser.add_argument("--ip", type=str, default=UDP_IP, help="Tab5 IP Address")
    parser.add_argument("--pan-min", type=int, default=0, help="Minimum pan angle limit")
    parser.add_argument("--pan-max", type=int, default=180, help="Maximum pan angle limit")
    parser.add_argument("--tilt-min", type=int, default=0, help="Minimum tilt angle limit")
    parser.add_argument("--tilt-max", type=int, default=180, help="Maximum tilt angle limit")
    args = parser.parse_args()

    print("Initializing YOLOv8 face model...")
    model_path = "/tmp/face_yolov8n.pt"
    if not os.path.exists(model_path):
        print("Downloading dedicated face model (face_yolov8n.pt)...")
        urllib.request.urlretrieve("https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8n.pt", model_path)
    
    model = YOLO(model_path) 

    # Setup Intel RealSense pipeline
    print("Connecting to Intel RealSense camera...")
    pipeline = rs.pipeline()
    
    use_infrared = False
    try:
        config = rs.config()
        config.enable_stream(rs.stream.depth)
        config.enable_stream(rs.stream.color)
        pipeline_profile = pipeline.start(config)
        align_to = rs.stream.color
    except Exception as e:
        print(f"Color stream unavailable ({e}), falling back to Infrared (USB 2.0?)")
        try:
            config = rs.config()
            config.enable_stream(rs.stream.depth)
            config.enable_stream(rs.stream.infrared, 1)
            pipeline_profile = pipeline.start(config)
            align_to = rs.stream.infrared
            use_infrared = True
        except Exception as e2:
            print(f"Error starting RealSense pipeline with Infrared: {e2}")
            print("Please ensure an Intel RealSense camera is connected.")
            return

    # Align depth frames to chosen stream
    align = rs.align(align_to)

    controller = Tab5Controller(
        ip=args.ip,
        pan_min=args.pan_min, pan_max=args.pan_max,
        tilt_min=args.tilt_min, tilt_max=args.tilt_max
    )

    
    print(f"Tracking started. Sending UDP to {args.ip}:{UDP_PORT}")
    print(f"Servo limits: Pan [{args.pan_min}, {args.pan_max}], Tilt [{args.tilt_min}, {args.tilt_max}]")
    
    device = 0 if torch.cuda.is_available() else 'cpu'
    if device == 0:
        print("✅ NVIDIA GPU Detected! YOLO will run on GPU.")
    else:
        print("⚠️ No NVIDIA GPU detected by PyTorch. Falling back to CPU.")

    # For framerate calculation
    prev_time = time.time()
    
    try:
        while True:
            try:
                frames = pipeline.wait_for_frames(timeout_ms=5000)
            except RuntimeError as e:
                print(f"Timeout waiting for frames: {e}", flush=True)
                continue

            # Align the depth frame to color/infrared frame
            aligned_frames = align.process(frames)

            aligned_depth_frame = aligned_frames.get_depth_frame()
            if use_infrared:
                color_frame = aligned_frames.get_infrared_frame(1)
            else:
                color_frame = aligned_frames.get_color_frame()

            if not aligned_depth_frame or not color_frame:
                continue

            # Convert images to numpy arrays
            frame = np.asanyarray(color_frame.get_data())
            if use_infrared:
                # YOLO expects 3 channels (RGB)
                frame = np.stack((frame,)*3, axis=-1)

            h, w = frame.shape[:2]
            center_x, center_y = w // 2, h // 2

            curr_time = time.time()
            
            # Check if a manual controller is currently active
            controller.check_manual_override()

            # Run inference at lower resolution for massive CPU speedup, using GPU if available
            results = model(frame, verbose=False, imgsz=160, device=device)

            best_box = None
            min_distance = float('inf')

            # Find the closest person
            for r in results:
                boxes = r.boxes
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0]
                    face_cx = int((x1 + x2) / 2)
                    face_cy = int((y1 + y2) / 2)
                    
                    # Constrain to image bounds to safely sample depth
                    face_cx_clamped = max(0, min(w - 1, face_cx))
                    face_cy_clamped = max(0, min(h - 1, face_cy))

                    # Get distance in meters directly from the realsense depth frame
                    distance = aligned_depth_frame.get_distance(face_cx_clamped, face_cy_clamped)

                    # Ignore 0 distance (invalid depth mapping)
                    if distance > 0 and distance < min_distance:
                        min_distance = distance
                        best_box = (int(x1), int(y1), int(x2), int(y2), distance)

            if not hasattr(controller, 'frame_count'):
                controller.frame_count = 0
            controller.frame_count += 1
            if controller.frame_count % 30 == 0:
                print(f"Processed 30 frames. Found face? {'Yes' if best_box else 'No'}. Is manual? {controller.is_manual}")

            if best_box and not controller.is_manual:
                x1, y1, x2, y2, dist = best_box
                face_cx = (x1 + x2) // 2
                face_cy = (y1 + y2) // 2

                # Calculate error
                err_x = face_cx - center_x
                err_y = face_cy - center_y

                # Update smoothed error and send command (only in AI mode)
                controller.update_angles(face_cx, face_cy, err_x, err_y)
                controller.send_command_if_due()

                if args.show:
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.circle(frame, (face_cx, face_cy), 5, (0, 0, 255), -1)
                    cv2.putText(frame, f"Dist: {dist:.2f}m", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    cv2.putText(frame, f"Pan: {int(controller.pan_angle)} Tilt: {int(controller.tilt_angle)}",
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

            if args.show:
                # Show status if manual
                if controller.is_manual:
                    cv2.putText(frame, "MANUAL OVERRIDE", (w//2 - 100, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

                # Draw center crosshair
                cv2.line(frame, (center_x - 10, center_y), (center_x + 10, center_y), (255, 0, 0), 1)
                cv2.line(frame, (center_x, center_y - 10), (center_x, center_y + 10), (255, 0, 0), 1)
                
                # FPS
                fps = 1 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
                prev_time = curr_time
                cv2.putText(frame, f"FPS: {fps:.1f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
                
                cv2.imshow("THOR YOLO Tracker (RealSense)", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
