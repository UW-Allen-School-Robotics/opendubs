"""Draw /perception/grasp_object's grasps on the colour frame they came from, to check the axes by eye.

    sudo docker cp software/architect/tools/grasp_overlay.py archit-perception-grasp:/tmp/
    sudo docker exec archit-perception-grasp bash -lc 'source /opt/ros/jazzy/setup.bash;
        source /opt/perception_ws/install/setup.bash;
        python3 /tmp/grasp_overlay.py mug /tmp/grasp.png'
    sudo docker cp archit-perception-grasp:/tmp/grasp.png .     # outside the repo

In the perception container (archit/perception-grasp), which has the camera's frames, cv2 and
robot_api_interfaces. Red: the approach (GraspNet +x: the gripper moves along it into the object;
the tool's +z in camera_frames.GRASP_TO_TOOL). Green: the closing direction (GraspNet +y, the tool's
y), drawn between the two jaw tips at +-width/2. Blue: GraspNet +z. The best grasp is thick, the
other candidates thin, the detection's box yellow. The frame drawn on is the one nearest the
grasp's stamp (the gap is printed). Only reads.

First used 2026-10-27 on opendubs2: a tumbler 0.52 m away, jaws across it, approach straight in.
"""
import collections
import json
import sys
import time

import cv2
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from robot_api_interfaces.msg import ResultCode
from robot_api_interfaces.srv import RobotCommand
from sensor_msgs.msg import Image

label, out = sys.argv[1], sys.argv[2]
rclpy.init()
node = rclpy.create_node("grasp_overlay")
frames = collections.deque(maxlen=60)
node.create_subscription(Image, "/camera/color/image_raw", lambda m: frames.append(m), qos_profile_sensor_data)
cli = node.create_client(RobotCommand, "/perception/grasp_object")
cli.wait_for_service(timeout_sec=5)
end = time.monotonic() + 3
while time.monotonic() < end:
    rclpy.spin_once(node, timeout_sec=0.05)
time.sleep(0.5)
fut = cli.call_async(RobotCommand.Request(req=json.dumps({"label": label})))
rclpy.spin_until_future_complete(node, fut, timeout_sec=15)
for _ in range(10):
    rclpy.spin_once(node, timeout_sec=0.05)
res = fut.result()
if res is None or res.result_code.result_code != ResultCode.SUCCESS:
    sys.exit(f"grasp_object: {res.result_code.message if res else 'no reply'}")
data = json.loads(res.data)
if not frames:
    sys.exit("no colour frames on /camera/color/image_raw")

stamp = data["stamp"]
msg = min(frames, key=lambda m: abs(m.header.stamp.sec + m.header.stamp.nanosec * 1e-9 - stamp))
dt = abs(msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9 - stamp)
buf = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step)[:, : msg.width * 3].reshape(msg.height, msg.width, 3)
img = buf.copy() if msg.encoding.lower() == "bgr8" else cv2.cvtColor(buf, cv2.COLOR_RGB2BGR)
K = data["intrinsics"]


def px(p):
    return int(round(K["fx"] * p[0] / p[2] + K["cx"])), int(round(K["fy"] * p[1] / p[2] + K["cy"]))


def rot(q):
    x, y, z, w = q["x"], q["y"], q["z"], q["w"]
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def draw(g, thick):
    c = np.array([g["position"][k] for k in "xyz"])
    R = rot(g["orientation"])
    L = 0.06
    cv2.arrowedLine(img, px(c - R[:, 0] * L), px(c), (0, 0, 255), thick, tipLength=0.25)   # approach, into c
    cv2.line(img, px(c), px(c + R[:, 2] * L * 0.6), (255, 0, 0), max(1, thick - 1))
    w = g["width"] / 2
    a, b = c + R[:, 1] * w, c - R[:, 1] * w
    cv2.line(img, px(a), px(b), (0, 200, 0), thick)
    for tip in (a, b):
        cv2.circle(img, px(tip), 2 + thick, (0, 200, 0), -1)
    cv2.circle(img, px(c), 3, (255, 255, 255), -1)


x0, y0, x1, y1 = data["box"]
cv2.rectangle(img, (x0, y0), (x1, y1), (0, 255, 255), 1)
for g in data.get("candidates", []):
    draw(g, 1)
draw(data, 3)
txt = (f"{label}: grasp {data['grasp_score']:.3f}  width {data['width']*100:.1f} cm  at {data['position']['z']:.2f} m"
       f"  frame dt {dt*1000:.0f} ms   red=approach  green=jaws  blue=GraspNet z")
cv2.putText(img, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
cv2.putText(img, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
cv2.imwrite(out, img)
print(json.dumps({k: data[k] for k in ("position", "orientation", "width", "grasp_score", "mode")}), f"dt={dt*1000:.0f}ms")
