"""
YOLO-based face tracking pipeline for OpenDubs Tab5.

Connects to a video stream (local webcam or MJPEG stream), runs YOLOv8 face detection,
calculates pan/tilt offsets to keep the face centered, and sends UDP pan/tilt commands
to the Tab5.
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

# Configuration
UDP_IP = "opendubs-tab5.local"
UDP_PORT = 4242

# CAN IDs
CAN_ID_PAN_TILT_CMD = 0x104
CAN_ID_TAB5_STATUS = 0x106

class Tab5Controller:
    def __init__(self, ip=UDP_IP, port=UDP_PORT):
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

        # Create listener socket to detect when Tab5 drops the real Thor's connection
        self.sock_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock_rx.bind(("", 5006))
            self.sock_rx.setblocking(False)
        except:
            print("Warning: Could not bind to port 5006 for listening.")

        self.pan_angle = 90.0
        self.tilt_angle = 90.0

        # Simple P-controller gains (increased for faster response)
        self.k_p = 0.1
        
        # Constraints
        self.pan_min, self.pan_max = 0, 180
        self.tilt_min, self.tilt_max = 0, 180

    def update_angles(self, error_x, error_y):
        """Update pan/tilt angles based on pixel errors."""
        # error_x is positive if face is to the right of center -> we need to pan right
        # Assuming increasing pan moves right, decreasing moves left
        self.pan_angle -= error_x * self.k_p
        self.tilt_angle -= error_y * self.k_p

        # Constrain
        self.pan_angle = max(self.pan_min, min(self.pan_max, self.pan_angle))
        self.tilt_angle = max(self.tilt_min, min(self.tilt_max, self.tilt_angle))

    def send_command(self):
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

    def send_auth(self):
        """Send a dummy STATE packet so Tab5 accepts us as the Thor controller."""
        try:
            self.sock.sendto(b"STATE PING\n", (self.resolved_ip, self.port))
        except:
            pass

    def hijack_if_needed(self):
        timed_out = False
        # Drain the rx socket looking for the MOST RECENT status packet
        while True:
            try:
                data, _ = self.sock_rx.recvfrom(64)
                if len(data) >= 13:
                    can_id, = struct.unpack('<I', data[:4])
                    if can_id == CAN_ID_TAB5_STATUS:  # CAN_ID_TAB5_STATUS
                        # Data layout: [5]=robot_state, [6]=link_online, [7]=tracking, [8]=ui_mode
                        if data[6] == 0:
                            timed_out = True
                        else:
                            timed_out = False
            except BlockingIOError:
                break
            except Exception:
                break
                
        # If the Tab5 is currently timed out, fire the auth packet to claim it!
        # If the packet drops, we will just try again on the next frame because timed_out will still be True!
        if timed_out:
            print("🎯 Tab5 link timed out! Hijacking connection...")
            self.send_auth()

def main():
    parser = argparse.ArgumentParser(description="THOR YOLO Face Tracker")
    parser.add_argument("--source", type=str, default="0", help="Video source (0 for webcam, or URL)")
    parser.add_argument("--show", action="store_true", help="Show GUI window")
    parser.add_argument("--ip", type=str, default=UDP_IP, help="Tab5 IP Address")
    args = parser.parse_args()

    print("Initializing YOLOv8 face model...")
    model_path = "face_yolov8n.pt"
    if not os.path.exists(model_path):
        print("Downloading dedicated face model (face_yolov8n.pt)...")
        urllib.request.urlretrieve("https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8n.pt", model_path)
    
    model = YOLO(model_path) 

    # Parse source as int if it's a number
    src = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(src)
    
    if not cap.isOpened():
        print(f"Error: Could not open video source {src}")
        return

    controller = Tab5Controller(ip=args.ip)
    # Ping the Tab5 to authenticate
    controller.send_auth()
    
    print(f"Tracking started. Sending UDP to {args.ip}:{UDP_PORT}")
    
    device = 0 if torch.cuda.is_available() else 'cpu'
    if device == 0:
        print("✅ NVIDIA GPU Detected! YOLO will run on GPU.")
    else:
        print("⚠️ No NVIDIA GPU detected by PyTorch. Falling back to CPU.")

    # For framerate calculation
    prev_time = time.time()
    
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to read frame or end of stream.")
            time.sleep(1)
            # Try to reconnect if it's a stream
            cap = cv2.VideoCapture(src)
            continue

        h, w = frame.shape[:2]
        center_x, center_y = w // 2, h // 2

        curr_time = time.time()
        
        # Listen for the exact millisecond the real Thor times out, then hijack it!
        controller.hijack_if_needed()

        # Run inference at lower resolution for massive CPU speedup, using GPU if available
        # The face model only has class 0 (face), so we don't need to filter by class
        results = model(frame, verbose=False, imgsz=160, device=device)

        best_box = None
        max_area = 0

        # Find the largest person bounding box
        for r in results:
            boxes = r.boxes
            for box in boxes:
                x1, y1, x2, y2 = box.xyxy[0]
                area = (x2 - x1) * (y2 - y1)
                if area > max_area:
                    max_area = area
                    best_box = (int(x1), int(y1), int(x2), int(y2))

        if best_box:
            x1, y1, x2, y2 = best_box
            face_cx = (x1 + x2) // 2
            face_cy = (y1 + y2) // 2
            
            # Calculate error
            err_x = face_cx - center_x
            err_y = face_cy - center_y
            
            # Update angles and send
            controller.update_angles(err_x, err_y)
            controller.send_command()
            
            if args.show:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.circle(frame, (face_cx, face_cy), 5, (0, 0, 255), -1)
                cv2.putText(frame, f"Pan: {int(controller.pan_angle)} Tilt: {int(controller.tilt_angle)}", 
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            # If no face, we can optionally slowly return to center or just hold
            pass

        if args.show:
            # Draw center crosshair
            cv2.line(frame, (center_x - 10, center_y), (center_x + 10, center_y), (255, 0, 0), 1)
            cv2.line(frame, (center_x, center_y - 10), (center_x, center_y + 10), (255, 0, 0), 1)
            
            # FPS
            fps = 1 / (curr_time - prev_time) if (curr_time - prev_time) > 0 else 0
            prev_time = curr_time
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
            
            cv2.imshow("THOR YOLO Tracker", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
