import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Range
from std_msgs.msg import String
import socket
import struct
import threading
import time
import urllib.request

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

CAN_FRAME_FMT = "<IB8s" # little-endian, uint32, uint8, 8 bytes
TOF_DATA_FMT = "<HB" # uint16, uint8
LIFT_CMD_FMT = "<B" # uint8
ROBOT_STATE_FMT = "<B" # uint8
PAN_TILT_CMD_FMT = "<hh" # int16, int16

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '../kiosk-dashboard/backend'))
import stamplc

def send_relay(ch, state):
    try:
        stamplc.reader.send(f"RELAY {ch} {state}")
    except Exception:
        pass

class Tab5BridgeNode(Node):
    def __init__(self):
        super().__init__('tab5_bridge_node')
        self.tof_pub = self.create_publisher(Range, '/sensors/tof_front', 10)
        self.state_sub = self.create_subscription(String, '/robot/state', self.state_callback, 10)
        
        self.udp_ip = "0.0.0.0"
        self.udp_port = 5005
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.udp_ip, self.udp_port))
        
        self.last_tab5_ip = None
        
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
                        dist, status = struct.unpack(TOF_DATA_FMT, payload[:3])
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
                        self.get_logger().info(f"Lift command received: {cmd}")
                        if cmd == 1:
                            send_relay(2, 1)
                            send_relay(3, 0)
                        elif cmd == 2:
                            send_relay(2, 0)
                            send_relay(3, 1)
                        else:
                            send_relay(2, 0)
                            send_relay(3, 0)
                        
            except Exception as e:
                self.get_logger().error(f"UDP Error: {e}")

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
