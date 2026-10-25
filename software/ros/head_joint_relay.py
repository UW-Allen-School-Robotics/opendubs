#!/usr/bin/env python3
"""Forward the neck's live pan and tilt to robot_state_publisher, so the head's frames (and the Femto
Bolt's, which ride head_tilt_link) are in TF (workplan L.2b, the head half).

  /head/joint_states  --(head_pan, head_tilt only)-->  /opendubs/joint_states  --> robot_state_publisher

The dashboard's neck_state.py publishes /head/joint_states with the robot description's joint names
and signs, stamped when the neck answered. This node passes those two joints on unchanged, stamp
included, so a transform looked up at an image's time is the head's pose at that time.

NOTHING IS INVENTED. When the neck is silent this node is silent too, and the head's transforms age out
of TF: a consumer gets "no transform", never a stale angle presented as the current one (neck_state.py's
rule for its own topic). It never publishes a default or a zero for a joint it has not heard.

/opendubs/joint_states is the robot_state_publisher's one input; the lift's `zaxis`, from its own
source, is published there separately (L.2b, the lift half). robot_state_publisher takes partial
messages, so the two sources do not need merging.

  OPENDUBS_HEAD_JOINTS_IN   the neck's topic                 (default /head/joint_states)
  OPENDUBS_HEAD_JOINTS_OUT  robot_state_publisher's input    (default /opendubs/joint_states)
"""
import os
import time

HEAD_JOINTS = ("head_pan", "head_tilt")
IN_TOPIC = os.environ.get("OPENDUBS_HEAD_JOINTS_IN", "/head/joint_states")
OUT_TOPIC = os.environ.get("OPENDUBS_HEAD_JOINTS_OUT", "/opendubs/joint_states")
RATE_LOG_S = 60.0


def head_only(names, positions):
    """The head's joints out of a JointState's names and positions, in HEAD_JOINTS order; None when the
    message lacks either of them or a position for it."""
    got = {n: p for n, p in zip(names, positions)}
    if not all(j in got for j in HEAD_JOINTS):
        return None
    return [float(got[j]) for j in HEAD_JOINTS]


def main():
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import JointState

    rclpy.init()
    node = Node("head_joint_relay")
    pub = node.create_publisher(JointState, OUT_TOPIC, 10)
    counts = {"in": 0, "out": 0, "since": time.monotonic()}

    def on_joints(msg):
        counts["in"] += 1
        positions = head_only(msg.name, msg.position)
        if positions is None:
            return
        out = JointState()
        out.header = msg.header          # the neck's own time: TF looked up at an image's stamp stays honest
        out.name = list(HEAD_JOINTS)
        out.position = positions
        pub.publish(out)
        counts["out"] += 1

    def log_rate():
        span = time.monotonic() - counts["since"]
        node.get_logger().info(f"{IN_TOPIC} -> {OUT_TOPIC}: {counts['out']} forwarded of {counts['in']} "
                               f"in {span:.0f} s" + ("  (the neck is silent: no head TF)" if counts["in"] == 0 else ""))
        counts.update({"in": 0, "out": 0, "since": time.monotonic()})

    node.create_subscription(JointState, IN_TOPIC, on_joints, 10)
    node.create_timer(RATE_LOG_S, log_rate)
    node.get_logger().info(f"forwarding {', '.join(HEAD_JOINTS)} from {IN_TOPIC} to {OUT_TOPIC}")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
