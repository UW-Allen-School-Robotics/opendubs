"""grip.py SIDE POSITION -- one GripperCommand goal, then print the result and the finger reading.

Run in the openarm container (RMW_IMPLEMENTATION=rmw_fastrtps_cpp) as `python3 - SIDE POSITION`.
Refuses a target more than 0.012 m from the finger's current reading (step, never jump).
"""
import sys
import time

import rclpy
from rclpy.action import ActionClient
from control_msgs.action import GripperCommand
from sensor_msgs.msg import JointState

side, target = sys.argv[1], float(sys.argv[2])
assert side in ("left", "right")
name = f"openarm_{side}_finger_joint1"
rclpy.init()
node = rclpy.create_node(f"arms_grip_{side}")
js = {}
node.create_subscription(JointState, "/joint_states", lambda m: js.update(zip(m.name, m.position)), 10)


def reading():
    js.pop(name, None)
    end = time.monotonic() + 6.0
    while time.monotonic() < end and name not in js:
        rclpy.spin_once(node, timeout_sec=0.1)
    if name not in js:
        sys.exit(f"no fresh {name} -- nothing sent")
    return js[name]


before = reading()
print(f"{side}: before {before:+.4f} m, target {target:+.4f} m")
if abs(target - before) > 0.012:
    sys.exit("target is more than 0.012 m from the reading -- nothing sent")
client = ActionClient(node, GripperCommand, f"/{side}_gripper_controller/gripper_cmd")
if not client.wait_for_server(timeout_sec=3.0):
    sys.exit("gripper action server not available -- nothing sent")
goal = GripperCommand.Goal()
goal.command.position, goal.command.max_effort = target, 3.0
fut = client.send_goal_async(goal)
rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
h = fut.result()
if h is None or not h.accepted:
    sys.exit("REJECTED")
res = h.get_result_async()
rclpy.spin_until_future_complete(node, res, timeout_sec=8.0)
r = res.result()
status = {4: "SUCCEEDED", 5: "CANCELED", 6: "ABORTED"}.get(r.status, r.status) if r else "timeout"
extra = f" (stalled {r.result.stalled}, reached {r.result.reached_goal})" if r else ""
time.sleep(0.3)
print(f"{side}: {status}{extra}; after {reading():+.4f} m")
