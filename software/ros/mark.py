# mark.py "label": append wall-clock time, label, /odom pose, SLAM map pose and head joints to <current capture>/marks.txt
import sys, os, glob, time, math, datetime, rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState
from tf2_ros import Buffer, TransformListener
B = "/var/lib/opendubs/bags"
dirs = [d for d in glob.glob(B + "/*/") if not d.rstrip("/").endswith("-zaxis") and glob.glob(d + "*.mcap")]
cap = max(dirs, key=lambda d: max(os.path.getmtime(f) for f in glob.glob(d + "*.mcap"))).rstrip("/")
label = " ".join(sys.argv[1:]).strip() or "mark"
rclpy.init(); n = Node("mark_ro"); got = {}
n.create_subscription(Odometry, "/odom", lambda m: got.__setitem__("odom", m), qos_profile_sensor_data)
n.create_subscription(JointState, "/head/joint_states", lambda m: got.__setitem__("head", m), 10)
buf = Buffer(); TransformListener(buf, n)
t0 = time.time()
while time.time() - t0 < 4 and not ("odom" in got and "head" in got): rclpy.spin_once(n, timeout_sec=0.1)
while time.time() - t0 < 6 and not buf.can_transform("map", "base_link", rclpy.time.Time()):   # let TF fill (map=NONE before)
    rclpy.spin_once(n, timeout_sec=0.1)
yaw = lambda q: math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
o = got.get("odom"); h = got.get("head")
odom = f"odom x={o.pose.pose.position.x:.3f} y={o.pose.pose.position.y:.3f} yaw={yaw(o.pose.pose.orientation):.4f} stamp={o.header.stamp.sec}.{o.header.stamp.nanosec:09d}" if o else "odom=NONE"
try:
    tr = buf.lookup_transform("map", "base_link", rclpy.time.Time()).transform
    slam = f"map x={tr.translation.x:.3f} y={tr.translation.y:.3f} yaw={yaw(tr.rotation):.4f}"
except Exception:
    slam = "map=NONE"
head = " ".join(f"{a}={p:.4f}" for a, p in zip(h.name, h.position)) if h else "head=NONE"
line = f"{datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')}\t{label}\t{odom}\t{slam}\t{head}\n"
open(cap + "/marks.txt", "a").write(line)
print(cap + "/marks.txt:", line, end="")
