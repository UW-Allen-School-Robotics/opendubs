import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Range
from std_msgs.msg import String
import socket
import struct
import json
import threading
import time
import urllib.request
import os

try:
    import cv2
    from ultralytics import YOLO
    HAS_CV = True
except ImportError:
    HAS_CV = False

CAN_ID_TOF_FRONT = 0x101
CAN_ID_LIFT_CMD = 0x102
CAN_ID_ROBOT_STATE = 0x103
CAN_ID_PAN_TILT_CMD = 0x104
CAN_ID_LIFT_STATE = 0x105

LIFT_STATE_FMT = "<HBB"   # height_mm, motion, flags
LIFT_HEIGHT_UNKNOWN = 0xFFFF
LIFT_FLAG_AT_TOP, LIFT_FLAG_AT_BOTTOM, LIFT_FLAG_GUARD = 0x01, 0x02, 0x04

CAN_FRAME_FMT = "<IB8s" # little-endian, uint32, uint8, 8 bytes
TOF_DATA_FMT = "<HBB"  # distance_mm, status, part (0=none 1=VL53L0X 2=VL53L1X)
TOF_PART_NAME = {0: "none-detected", 1: "VL53L0X", 2: "VL53L1X"}
LIFT_CMD_FMT = "<B" # uint8
ROBOT_STATE_FMT = "<B" # uint8
PAN_TILT_CMD_FMT = "<hh" # int16, int16

# Talk to the StampPLC through the dashboard's HTTP API, NOT by importing its reader.
#
# Importing `stamplc` spawns a second reader thread that binds UDP:5005 with SO_REUSEPORT —
# and Linux LOAD-BALANCES datagrams across sockets bound that way rather than duplicating
# them. Running this node alongside opendubs-dash would therefore have silently stolen
# roughly half the robot's telemetry from the dashboard. One reader, one owner.
DASH = os.environ.get("OPENDUBS_DASH", "http://127.0.0.1:8080")

# Z-axis commands go through the StampPLC's mast driver, NOT to its relays.
#
# This used to write `RELAY 2 1` / `RELAY 3 0` straight to the relay expander. Three things
# were wrong with that, and the third is the one that matters:
#
#   * the relay numbers were wrong -- the bench-verified map is code R2 (silk R3) UP and
#     code R1 (silk R2) DOWN, so "down" was driving R3, which is not the lift;
#   * it set relay LEVELS, but the lift controller is pulse-toggle, so a held closure is a
#     held button rather than a press;
#   * and it bypassed ScissorRelayMast entirely -- no pinch guard, no limit detection, no
#     belief correction. A network packet could lower the lift through the pinch zone.
#
# `MAST CMD <-1..1>` feeds the same setIntent() the RC sticks do. It is heartbeat-gated at
# the firmware end, so a bridge that dies stops the lift rather than leaving it running.
MAST_HEARTBEAT_S = 0.1          # firmware drops intent after 250 ms of silence


def send_mast(v):
    """v in -1..1; positive is UP. Returns False if the link is not usable."""
    cmd = "up" if v > 0.25 else ("down" if v < -0.25 else "stop")
    try:
        req = urllib.request.Request(f"{DASH}/api/mast/{cmd}", method="POST")
        urllib.request.urlopen(req, timeout=0.5).read()
        return True
    except Exception:
        return False


def read_lift_state():
    """Lift state as the StampPLC measures it. {} when unavailable."""
    try:
        with urllib.request.urlopen(f"{DASH}/api/stamplc", timeout=0.5) as r:
            return (json.loads(r.read()).get("drive") or {})
    except Exception:
        return {}

class Tab5BridgeNode(Node):
    def __init__(self):
        super().__init__('tab5_bridge_node')
        self.tof_pub = self.create_publisher(Range, '/sensors/tof_front', 10)
        self.state_sub = self.create_subscription(String, '/robot/state', self.state_callback, 10)
        
        self.udp_ip = "0.0.0.0"
        # 5006, not 5005. Port 5005 is the StampPLC telemetry port that the Thor's recorder
        # listens on; the Tab5 was broadcasting binary CAN frames into it and corrupting the
        # robot's telemetry log.
        self.udp_port = 5006
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        
        self.last_tab5_ip = None

        # Held Z-axis intent, re-asserted at MAST_HEARTBEAT_S. The firmware drops to stop
        # after 250 ms of silence, so a crashed or wedged bridge stops the lift instead of
        # leaving it travelling — the same discipline as the drive command.
        self.mast_intent = 0.0
        self.create_timer(MAST_HEARTBEAT_S, self._mast_heartbeat)
        # Tell the Tab5 what the lift is actually doing, so its HMI shows measured state
        # instead of a local model.
        self.create_timer(0.2, self._publish_lift_state)
        
        if HAS_CV:
            self.yolo_model = YOLO('yolov8n.pt')
            self.video_thread = threading.Thread(target=self.video_loop, daemon=True)
            self.video_thread.start()
        else:
            self.get_logger().warn("OpenCV or Ultralytics not installed. Tracking disabled.")
        
        # Start UDP listener thread
        self.listen_thread = threading.Thread(target=self.udp_listener, daemon=True)
        self.listen_thread.start()
        self.get_logger().info(f"Tab5 Bridge started on UDP {self.udp_port}")

    def video_loop(self):
        while self.last_tab5_ip is None:
            time.sleep(1)
            
        stream_url = f"http://{self.last_tab5_ip}/"
        self.get_logger().info(f"Starting tracking from {stream_url}")
        cap = cv2.VideoCapture(stream_url)
        
        pan_angle = 90
        tilt_angle = 90
        kp = 0.05
        
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
                
            results = self.yolo_model(frame, classes=[0], verbose=False)
            if len(results) > 0 and len(results[0].boxes) > 0:
                box = results[0].boxes[0].xyxy[0].cpu().numpy()
                cx = (box[0] + box[2]) / 2.0
                cy = (box[1] + box[3]) / 2.0
                
                h, w = frame.shape[:2]
                err_x = cx - (w / 2.0)
                err_y = cy - (h / 2.0)
                
                pan_angle -= int(err_x * kp)
                tilt_angle += int(err_y * kp)
                
                pan_angle = max(0, min(180, pan_angle))
                tilt_angle = max(0, min(180, tilt_angle))
                
                payload = struct.pack(PAN_TILT_CMD_FMT, pan_angle, tilt_angle)
                padded_payload = payload.ljust(8, b'\x00')
                frame_data = struct.pack(CAN_FRAME_FMT, CAN_ID_PAN_TILT_CMD, len(payload), padded_payload)
                try:
                    self.sock.sendto(frame_data, (self.last_tab5_ip, 4242))
                except Exception as e:
                    self.get_logger().error(f"Failed to send pan/tilt UDP packet: {e}")

    def udp_listener(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
                if len(data) >= struct.calcsize(CAN_FRAME_FMT):
                    can_id, dlc, payload = struct.unpack(CAN_FRAME_FMT, data[:13])
                    self.last_tab5_ip = addr[0]
                    
                    if can_id == CAN_ID_TOF_FRONT:
                        dist, status, part = struct.unpack(TOF_DATA_FMT, payload[:4])
                        # Say which sensor answered, once, and again whenever it changes —
                        # "no reading" from a missing sensor and from a mis-aimed one look
                        # identical otherwise, and they need different fixes.
                        if part != getattr(self, "_tof_part", None):
                            self._tof_part = part
                            self.get_logger().info(
                                f"Front ToF: {TOF_PART_NAME.get(part, part)}")
                        msg = Range()
                        msg.header.stamp = self.get_clock().now().to_msg()
                        msg.header.frame_id = "tof_front_link"
                        msg.radiation_type = Range.INFRARED
                        msg.field_of_view = 0.1
                        msg.min_range = 0.0
                        msg.max_range = 2.0
                        msg.range = dist / 1000.0 # Convert mm to m
                        if status == 1:
                            self.tof_pub.publish(msg)
                            
                    elif can_id == CAN_ID_LIFT_CMD:
                        cmd, = struct.unpack(LIFT_CMD_FMT, payload[:1])
                        # 1 = up, 2 = down, anything else = stop. Held as an intent and
                        # re-asserted by _mast_heartbeat below, because the firmware treats
                        # silence as "stop" -- which is what we want if this node dies.
                        self.mast_intent = 1.0 if cmd == 1 else (-1.0 if cmd == 2 else 0.0)
                        self.get_logger().info(
                            f"Lift command {cmd} -> MAST CMD {self.mast_intent:+.1f}")
                        send_mast(self.mast_intent)
                        
            except Exception as e:
                self.get_logger().error(f"UDP Error: {e}")

    def _publish_lift_state(self):
        if not self.last_tab5_ip:
            return
        d = read_lift_state()
        if not d:
            return
        h = d.get("mast_h_mm")
        height = LIFT_HEIGHT_UNKNOWN if (h is None or h < 0) else min(int(h), 0xFFFE)
        # "scissor:2" -> 2
        motion = 0
        mast = d.get("mast") or ""
        if ":" in mast:
            try: motion = int(mast.split(":")[1])
            except ValueError: motion = 0
        flags = LIFT_FLAG_GUARD if d.get("pinch") == "HOLD" else 0
        payload = struct.pack(LIFT_STATE_FMT, height, motion, flags)
        frame = struct.pack(CAN_FRAME_FMT, CAN_ID_LIFT_STATE, len(payload),
                            payload.ljust(8, b"\x00"))
        try:
            self.sock.sendto(frame, (self.last_tab5_ip, 4242))
        except Exception:
            pass

    def _mast_heartbeat(self):
        # Only speak while there is something to say. Sending a stream of zeros would keep
        # the firmware's autonomous heartbeat alive for no reason.
        if self.mast_intent != 0.0:
            send_mast(self.mast_intent)

    def state_callback(self, msg):
        state_map = {"idle": 0, "moving": 1, "error": 2}
        state_id = state_map.get(msg.data.lower(), 0)
        
        payload = struct.pack(ROBOT_STATE_FMT, state_id)
        padded_payload = payload.ljust(8, b'\x00')
        frame = struct.pack(CAN_FRAME_FMT, CAN_ID_ROBOT_STATE, len(payload), padded_payload)
        
        if self.last_tab5_ip:
            try:
                self.sock.sendto(frame, (self.last_tab5_ip, 4242))
            except Exception as e:
                self.get_logger().error(f"Failed to send state UDP packet: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = Tab5BridgeNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
