# mark.py "label": append wall-clock time, label, odometry pose, SLAM map pose and head joints to <current capture>/marks.txt.
# A new line also makes that mark's camera burst (capture_camera.BurstTrigger, in the recorder's process).
# Robot-agnostic: the topics, frames and data root come from capture_camera.yaml's `robot:` section
# (--config FILE, or OPENDUBS_CAPTURE_CAMERA); OpenDubs's are the defaults. --root (or OPENDUBS_BAG_DIR)
# overrides the data root; a robot with no head (robot.head "") writes head=NONE.
#   mark.py [--config FILE] [--root DIR] [--capture DIR] label words...
import argparse, os, sys, glob, time, math, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_camera as cc

ap = argparse.ArgumentParser(description="one line in the current capture's marks.txt")
ap.add_argument("--config", default=cc.CONFIG)
ap.add_argument("--root", help="the data root (default: OPENDUBS_BAG_DIR, else robot.data_root)")
ap.add_argument("--capture", help="the capture directory (default: the one written last under the root)")
ap.add_argument("label", nargs="*")
args = ap.parse_args()
R = cc.load_config(args.config)["robot"]
B = args.root or os.environ.get("OPENDUBS_BAG_DIR") or R["data_root"]
if args.capture:
    cap = args.capture.rstrip("/")
else:
    # the capture being recorded: the one with a bag (DIR/*.mcap, or one level down: light/, heavy/) written last
    mcaps = lambda d: glob.glob(d + "*.mcap") + glob.glob(d + "*/*.mcap")
    dirs = [d for d in glob.glob(B + "/*/") if not d.rstrip("/").endswith("-zaxis") and mcaps(d)]
    if not dirs:
        sys.exit(f"no capture with a bag under {B}")
    cap = max(dirs, key=lambda d: max(os.path.getmtime(f) for f in mcaps(d))).rstrip("/")
label = " ".join(args.label).strip() or "mark"

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from tf2_ros import Buffer, TransformListener
rclpy.init(); n = Node("mark_ro"); got = {}
if R["odom"]:
    from nav_msgs.msg import Odometry
    n.create_subscription(Odometry, R["odom"], lambda m: got.__setitem__("odom", m), qos_profile_sensor_data)
if R["head"]:
    from sensor_msgs.msg import JointState
    n.create_subscription(JointState, R["head"], lambda m: got.__setitem__("head", m), 10)
want = [k for k, t in (("odom", R["odom"]), ("head", R["head"])) if t]
buf = Buffer(); TransformListener(buf, n)
t0 = time.time()
while time.time() - t0 < 4 and not all(k in got for k in want): rclpy.spin_once(n, timeout_sec=0.1)
while time.time() - t0 < 6 and not buf.can_transform(R["map_frame"], R["base_frame"], rclpy.time.Time()):   # let TF fill (map=NONE before)
    rclpy.spin_once(n, timeout_sec=0.1)
yaw = lambda q: math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
o = got.get("odom"); h = got.get("head")
odom = f"odom x={o.pose.pose.position.x:.3f} y={o.pose.pose.position.y:.3f} yaw={yaw(o.pose.pose.orientation):.4f} stamp={o.header.stamp.sec}.{o.header.stamp.nanosec:09d}" if o else "odom=NONE"
try:
    tr = buf.lookup_transform(R["map_frame"], R["base_frame"], rclpy.time.Time()).transform
    slam = f"map x={tr.translation.x:.3f} y={tr.translation.y:.3f} yaw={yaw(tr.rotation):.4f}"
except Exception:
    slam = "map=NONE"
head = " ".join(f"{a}={p:.4f}" for a, p in zip(h.name, h.position)) if h else "head=NONE"
line = f"{datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')}\t{label}\t{odom}\t{slam}\t{head}\n"
open(cap + "/marks.txt", "a").write(line)
print(cap + "/marks.txt:", line, end="")
