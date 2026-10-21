#!/usr/bin/env python3
"""Publish the building floor frame and its rooms: `<floor> -> map` as static TF, and room labels.

Reads the robot's site file (/var/lib/opendubs/maps/site.yaml, written by floorplan.py fit):
which saved map SLAM localizes on and where that map sits in the floor frame (map_in_floor). The
floor frame is fixed by the building plan, so room positions never move when a new map is made;
only map_in_floor is refitted.

  TF        <floor.frame> -> map             static, from floor.map_in_floor
  /floor/rooms  visualization_msgs/MarkerArray   one text label per room, in the floor frame
                                                 (latched: a viewer that joins later gets it)

The site file and the plan's rooms are site data: they live on the robot, never in the repo.

  OPENDUBS_SITE   the site file   (default /var/lib/opendubs/maps/site.yaml)
"""
import math
import os

SITE = os.environ.get("OPENDUBS_SITE", "/var/lib/opendubs/maps/site.yaml")


def yaw_quaternion(yaw):
    """(x, y, z, w) for a rotation of `yaw` about z."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def load(site_path=SITE):
    """The site file and its plan: (floor frame, (x, y, yaw) of map in it, {room: (x, y)})."""
    import yaml
    site = yaml.safe_load(open(site_path))
    floor = site["floor"]
    plan = yaml.safe_load(open(os.path.join(os.path.dirname(site_path), floor["plan"])))
    if plan["frame"] != floor["frame"]:
        raise SystemExit(f"{floor['plan']} is for {plan['frame']}, the site file says {floor['frame']}")
    x, y, yaw = (float(v) for v in floor["map_in_floor"])
    return floor["frame"], (x, y, yaw), {str(k): (float(v[0]), float(v[1])) for k, v in plan["rooms"].items()}


def main():
    import rclpy
    from geometry_msgs.msg import TransformStamped
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from tf2_ros import StaticTransformBroadcaster
    from visualization_msgs.msg import Marker, MarkerArray

    frame, (x, y, yaw), rooms = load()
    rclpy.init()
    node = Node("floor_frames")
    now = node.get_clock().now().to_msg()

    t = TransformStamped()
    t.header.stamp, t.header.frame_id, t.child_frame_id = now, frame, "map"
    t.transform.translation.x, t.transform.translation.y = x, y
    (t.transform.rotation.x, t.transform.rotation.y,
     t.transform.rotation.z, t.transform.rotation.w) = yaw_quaternion(yaw)
    tf = StaticTransformBroadcaster(node)
    tf.sendTransform(t)

    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
    pub = node.create_publisher(MarkerArray, "/floor/rooms", latched)
    arr = MarkerArray()
    for i, (label, (rx, ry)) in enumerate(sorted(rooms.items())):
        m = Marker()
        m.header.stamp, m.header.frame_id = now, frame
        m.ns, m.id, m.type, m.action = "rooms", i, Marker.TEXT_VIEW_FACING, Marker.ADD
        m.pose.position.x, m.pose.position.y, m.pose.position.z = rx, ry, 1.0
        m.pose.orientation.w = 1.0
        m.scale.z = 0.4
        m.color.r = m.color.g = m.color.b = 0.9
        m.color.a = 1.0
        m.text = label
        arr.markers.append(m)
    pub.publish(arr)
    node.get_logger().info(f"{frame} -> map at x={x:.2f} y={y:.2f} yaw={math.degrees(yaw):.1f} deg; "
                           f"{len(rooms)} rooms on /floor/rooms")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
