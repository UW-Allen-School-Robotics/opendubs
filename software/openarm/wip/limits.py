# Read-only: fetch robot_description from robot_state_publisher, print arm joint limits.
import math, rclpy, xml.etree.ElementTree as ET
from rcl_interfaces.srv import GetParameters
rclpy.init(); n = rclpy.create_node("arms_read_limits")
c = n.create_client(GetParameters, "/robot_state_publisher/get_parameters")
assert c.wait_for_service(timeout_sec=5)
f = c.call_async(GetParameters.Request(names=["robot_description"]))
rclpy.spin_until_future_complete(n, f, timeout_sec=10)
root = ET.fromstring(f.result().values[0].string_value)
M = -0.005
for j in root.iter("joint"):
    name = j.get("name")
    if not any(f"openarm_{s}_joint" in name for s in ("left", "right")): continue
    l = j.find("limit")
    lo, hi = float(l.get("lower")), float(l.get("upper"))
    print(f"{name:22s} URDF [{lo:+.4f}, {hi:+.4f}] rad  admissible [{lo+M:+.4f}, {hi-M:+.4f}] rad = [{math.degrees(lo+M):+7.2f}, {math.degrees(hi-M):+7.2f}] deg")
