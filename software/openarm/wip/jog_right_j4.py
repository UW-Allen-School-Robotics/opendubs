"""Jog right J4 to +1.0 deg over 3 s; every other right joint is held at its measured value.

One FollowJointTrajectory goal, one waypoint, to /right_controller/follow_joint_trajectory: the
same interface and validation as a show. Refuses unless right J4 is within 2 deg of the target
and every other joint is fresh. Prints before, the goal, the result and after. Run in the openarm
container with RMW_IMPLEMENTATION=rmw_fastrtps_cpp.
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

TARGET_J4 = math.radians(1.0)
NAMES = [f"openarm_right_joint{i}" for i in range(1, 8)]

rclpy.init()
node = rclpy.create_node("arms_jog_right_j4")
js = {}
node.create_subscription(JointState, "/joint_states", lambda m: js.update(zip(m.name, m.position)), 10)


def fresh():
    js.clear()
    end = time.monotonic() + 2.0
    while time.monotonic() < end and not all(n in js for n in NAMES):
        rclpy.spin_once(node, timeout_sec=0.1)
    if not all(n in js for n in NAMES):
        sys.exit("no fresh /joint_states for the right arm -- nothing sent")
    return [js[n] for n in NAMES]


q = fresh()
print("before (deg):", " ".join(f"{math.degrees(v):+.2f}" for v in q))
if abs(q[3] - TARGET_J4) > math.radians(2.0):
    sys.exit(f"right J4 is at {math.degrees(q[3]):+.2f} deg, not within 2 deg of +1.0 -- nothing sent")
goal_q = list(q)
goal_q[3] = TARGET_J4
client = ActionClient(node, FollowJointTrajectory, "/right_controller/follow_joint_trajectory")
if not client.wait_for_server(timeout_sec=3.0):
    sys.exit("right_controller action server not available -- nothing sent")
goal = FollowJointTrajectory.Goal()
goal.trajectory.joint_names = NAMES
goal.trajectory.points = [JointTrajectoryPoint(positions=goal_q, time_from_start=Duration(sec=3))]
print("goal   (deg):", " ".join(f"{math.degrees(v):+.2f}" for v in goal_q), "in 3 s")
fut = client.send_goal_async(goal)
rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
h = fut.result()
if h is None or not h.accepted:
    sys.exit("REJECTED -- see the controller's log line")
print("accepted")
res = h.get_result_async()
rclpy.spin_until_future_complete(node, res, timeout_sec=10.0)
print("result status:", res.result().status if res.result() else "timeout")
q = fresh()
print("after  (deg):", " ".join(f"{math.degrees(v):+.2f}" for v in q))
