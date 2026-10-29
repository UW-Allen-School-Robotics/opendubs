#!/usr/bin/env python3
"""Drop the robot's own returns from a LaserScan before SLAM sees them.

The front RPLIDAR sees the robot itself 0.18-0.28 m away. slam_toolbox would map those returns as
walls that travel with the robot, so this node subscribes to /scan, replaces every return that falls
inside the chassis box (plus a margin, in base_link) with +inf, and publishes /scan_filtered.

The box is the chassis span from description/opendubs/dimensions.yaml (datum x -32..612,
y +/-324.5) less base_link (324, 0), plus 10 cm: x -0.456..0.388, y +/-0.4245 m, EXCEPT at the
back, where it runs to 10 cm behind the rear LIDAR (laser_rear, x -0.524): that unit and its mount
stand out behind the chassis, and the rear unit sees its own mount 6 cm away (measured 2026-10-21).
So x -0.624..0.388. The LIDAR's
mounting comes from TF (robot_state_publisher), looked up once: it is a fixed joint.

laser_filters' box filter does the same job, but its Jazzy build at the time needed a newer
diagnostic_updater than the robot's, and provisioning never upgrades installed ROS packages.

  OPENDUBS_SCAN_IN / OPENDUBS_SCAN_OUT   topics   (default /scan, /scan_filtered)
  OPENDUBS_SCAN_NODE                    node name (default scan_body_filter; use a
                                        different name for a second lidar, e.g. rear)
"""
import math
import os

BOX = (-0.624, 0.388, -0.4245, 0.4245)   # min_x, max_x, min_y, max_y in base_link, metres


def yaw_of(q) -> float:
    """Yaw of a quaternion (x, y, z, w); the LIDARs are mounted level."""
    x, y, z, w = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def mask(ranges, angle_min, angle_inc, tx, ty, yaw, box=BOX):
    """The ranges with every return inside `box` replaced by +inf. Pure: no ROS."""
    x0, x1, y0, y1 = box
    out = list(ranges)
    for i, r in enumerate(out):
        if not math.isfinite(r):
            continue
        a = angle_min + i * angle_inc + yaw
        px, py = tx + r * math.cos(a), ty + r * math.sin(a)
        if x0 <= px <= x1 and y0 <= py <= y1:
            out[i] = math.inf
    return out


def main():
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.time import Time
    from sensor_msgs.msg import LaserScan
    from tf2_ros import Buffer, TransformListener

    rclpy.init()
    node_name = os.environ.get("OPENDUBS_SCAN_NODE", "scan_body_filter").strip() or "scan_body_filter"
    scan_in = os.environ.get("OPENDUBS_SCAN_IN", "/scan")
    scan_out = os.environ.get("OPENDUBS_SCAN_OUT", "/scan_filtered")
    node = Node(node_name)
    log = node.get_logger()
    tf_buffer = Buffer()
    TransformListener(tf_buffer, node)
    pub = node.create_publisher(LaserScan, scan_out, qos_profile_sensor_data)
    mount = {}

    def on_scan(msg):
        frame = msg.header.frame_id
        if frame not in mount:
            try:
                t = tf_buffer.lookup_transform("base_link", frame, Time()).transform
            except Exception as e:                   # robot_state_publisher not up yet
                log.warning(f"no base_link -> {frame} yet ({e}); dropping scans until there is",
                            throttle_duration_sec=10.0)
                return
            q = (t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w)
            mount[frame] = (t.translation.x, t.translation.y, yaw_of(q))
            log.info(f"{frame} at x={mount[frame][0]:.3f} y={mount[frame][1]:.3f} "
                     f"yaw={math.degrees(mount[frame][2]):.1f} deg in base_link")
        tx, ty, yaw = mount[frame]
        out = LaserScan()
        out.header = msg.header
        out.angle_min, out.angle_max, out.angle_increment = msg.angle_min, msg.angle_max, msg.angle_increment
        out.time_increment, out.scan_time = msg.time_increment, msg.scan_time
        out.range_min, out.range_max = msg.range_min, msg.range_max
        out.ranges = mask(msg.ranges, msg.angle_min, msg.angle_increment, tx, ty, yaw)
        out.intensities = msg.intensities
        pub.publish(out)

    log.info(f"{node_name}: {scan_in} -> {scan_out}")
    node.create_subscription(LaserScan, scan_in, on_scan, qos_profile_sensor_data)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
