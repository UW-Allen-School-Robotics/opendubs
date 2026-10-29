#!/usr/bin/env python3
"""Hold the lift and the neck at the URDF zero so camera_link connects to base_link.

robot_state_publisher reads /opendubs/joint_states and publishes nothing below a joint it
has never heard (robot-state-publisher.sh). Zero is lift down, head straight ahead, face
level: the pose the camera's mounting was measured at.

shortcut: the head and the lift do not move these frames. Publish /head/joint_states and
the lift height here when the camera should follow them.
"""
JOINTS = ("zaxis", "head_pan", "head_tilt")


def main():
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import JointState

    rclpy.init()
    node = Node("head_joints")
    # robot_state_publisher subscribes best-effort. A reliable publisher is
    # discovered, but this DDS profile does not deliver those samples.
    pub = node.create_publisher(JointState, "/opendubs/joint_states", qos_profile_sensor_data)

    def tick():
        msg = JointState()
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.name = list(JOINTS)
        msg.position = [0.0] * len(JOINTS)
        pub.publish(msg)

    node.create_timer(0.2, tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
