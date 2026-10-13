"""Left arm: from stow up to the high-limit pose and back, in ONE FollowJointTrajectory goal.

Up 12 s (minimum-jerk), hold 4 s, down 12 s. Target J1 -165, J2 -15, J4 +10 deg, others 0: hand_tcp
at z 1.291 m, 9 mm under the 1.30 m box (FK on the ROS V1 description; the path stays within
x 0.62, z 0.08..1.291). Refuses unless every left joint is within 10 deg of 0 (at stow). Ctrl-C
cancels the goal, and the controller then holds where the arm is. Run in the openarm container
with RMW_IMPLEMENTATION=rmw_fastrtps_cpp.
"""
import math
import sys
import time

import rclpy
from rclpy.action import ActionClient
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectoryPoint
from builtin_interfaces.msg import Duration
from sensor_msgs.msg import JointState

NAMES = [f"openarm_left_joint{i}" for i in range(1, 8)]
TARGET = [math.radians(a) for a in (-165, -15, 0, 10, 0, 0, 0)]
UP, HOLD, DT = 12.0, 4.0, 0.25

rclpy.init()
node = rclpy.create_node("arms_high_demo_left")
js = {}
node.create_subscription(JointState, "/joint_states", lambda m: js.update(zip(m.name, m.position)), 10)
end = time.monotonic() + 6.0
while time.monotonic() < end and not all(n in js for n in NAMES):
    rclpy.spin_once(node, timeout_sec=0.1)
if not all(n in js for n in NAMES):
    sys.exit("no fresh /joint_states for the left arm -- nothing sent")
start = [js[n] for n in NAMES]
print("start (deg):", " ".join(f"{math.degrees(v):+.1f}" for v in start))
if max(abs(v) for v in start) > math.radians(10):
    sys.exit("the left arm is not at stow (a joint is more than 10 deg off 0) -- nothing sent")


def mj(s):  # minimum-jerk 0..1
    return 10 * s**3 - 15 * s**4 + 6 * s**5


pts, t = [], DT
while t <= UP + 1e-9:
    k = mj(t / UP)
    pts.append((t, [a + (b - a) * k for a, b in zip(start, TARGET)]))
    t += DT
pts.append((UP + HOLD, TARGET))
t = DT
while t <= UP + 1e-9:
    k = mj(t / UP)
    pts.append((UP + HOLD + t, [b + (a - b) * k for a, b in zip(start, TARGET)]))
    t += DT

goal = FollowJointTrajectory.Goal()
goal.trajectory.joint_names = NAMES
for tt, q in pts:
    sec = int(tt)
    goal.trajectory.points.append(JointTrajectoryPoint(
        positions=q, time_from_start=Duration(sec=sec, nanosec=int(round((tt - sec) * 1e9)))))
client = ActionClient(node, FollowJointTrajectory, "/left_controller/follow_joint_trajectory")
if not client.wait_for_server(timeout_sec=3.0):
    sys.exit("left_controller action server not available -- nothing sent")
print(f"sending {len(pts)} waypoints: up {UP:.0f} s, hold {HOLD:.0f} s, down {UP:.0f} s")
fut = client.send_goal_async(goal)
rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
h = fut.result()
if h is None or not h.accepted:
    sys.exit("REJECTED -- see the controller's log line (openarm-up logs)")
print("accepted -- moving")
res = h.get_result_async()
try:
    rclpy.spin_until_future_complete(node, res, timeout_sec=UP * 2 + HOLD + 10)
except KeyboardInterrupt:
    print("cancelling -- the arm holds where it is")
    rclpy.spin_until_future_complete(node, h.cancel_goal_async(), timeout_sec=3.0)
    sys.exit(1)
print("result status:", res.result().status if res.result() else "timeout")
