#!/usr/bin/env python3
"""head_joints.py: the neck's /head/joint_states onto the robot description's moving joints, for
robot_state_publisher (/opendubs/joint_states), so base_link -> head_tilt_link -> camera_link resolves.

  in   /head/joint_states   head_pan, head_tilt (radians from centre; neck_state.py, in the dashboard)
  out  /opendubs/joint_states   head_pan, head_tilt, zaxis, with the input's stamp

neck_state.py already reports in the URDF's convention (head_pan + = the robot's left), so the angles
pass through unchanged. One escape hatch, per robot, never in the repo, for a neck found to disagree:
  OPENDUBS_HEAD_PAN_URDF_SIGN   in /etc/opendubs/robot.env, +1 (default, every robot) or -1.
                                Unverified against data; a still pan sweep settles it.
zaxis (the lift) is published as 0: /zaxis/encoder carries no reading yet, so the lift is assumed down.
Nothing is published while the neck is silent: a stale head angle would be a confident wrong camera
pose, and robot_state_publisher simply stops publishing the frames below the head instead."""
from __future__ import annotations

import os

PAN, TILT, LIFT = "head_pan", "head_tilt", "zaxis"


def pan_sign(env: dict) -> float:
    try:
        return -1.0 if float(env.get("OPENDUBS_HEAD_PAN_URDF_SIGN", "1")) < 0 else 1.0
    except ValueError:
        return 1.0


def map_joints(names, positions, sign: float):
    """(names, positions) for /opendubs/joint_states from the neck's, or None without both joints."""
    got = dict(zip(names, positions))
    if PAN not in got or TILT not in got:
        return None
    return [PAN, TILT, LIFT], [sign * float(got[PAN]), float(got[TILT]), 0.0]


def main() -> None:
    import rclpy
    from sensor_msgs.msg import JointState
    rclpy.init()
    node = rclpy.create_node("head_joints")
    sign = pan_sign(os.environ)
    pub = node.create_publisher(JointState, "/opendubs/joint_states", 10)

    def on_head(m: JointState) -> None:
        out = map_joints(m.name, m.position, sign)
        if out is None:
            return
        js = JointState()
        js.header.stamp = m.header.stamp
        js.name, js.position = out
        pub.publish(js)

    node.create_subscription(JointState, "/head/joint_states", on_head, 10)
    node.get_logger().info(f"head_joints: /head/joint_states -> /opendubs/joint_states, pan sign {sign:+.0f}, zaxis 0")
    rclpy.spin(node)


if __name__ == "__main__":
    main()
