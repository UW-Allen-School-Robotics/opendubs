"""The hand tracker as a ROS 2 node: the running Femto Bolt driver's colour and depth in, palms out.

  python3 -m hand_tracker.node [--camera /camera] [--models /data/models/mediapipe] --ros-args -r __ns:=/perception

Topics (under the node's namespace, /perception in tools/hand_stack.sh):
  hand/<side>/palm   geometry_msgs/PointStamped  a FOLLOWABLE point of the hand only (palm.py, --follow), in the colour image's own frame
                                                 (camera_color_optical_frame: x right, y down, z forward, metres),
                                                 stamped with the image. Nothing is published for a lost or rough palm,
                                                 so a follower's "fresh palm" test is also its "trustworthy palm" test.
  hand/state         std_msgs/String (JSON)      every processed frame: person distance and, per side, the source, xyz
                                                 and whether it is followable; for watching (`ros2 topic echo`), not for
                                                 steering.
<side> is the person's own hand (palm.py): `right` is their right hand.

ON DEMAND: a frame is only run through MediaPipe while one of those topics has a subscriber, so the node costs nothing
when nobody follows. Newest frame only: a frame that arrives while one is being processed replaces the waiting one.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time

import numpy as np

from hand_tracker.palm import POLICIES, SIDES, PalmTracker, followable


def main(argv=None):
    import rclpy
    from geometry_msgs.msg import PointStamped
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import CameraInfo, Image
    from std_msgs.msg import String

    from perception_server.frames import CameraFrames, image_to_numpy

    argv = sys.argv if argv is None else argv
    args_ros = rclpy.utilities.remove_ros_args(argv)[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default="/camera", help="the orbbec_camera driver's namespace")
    ap.add_argument("--models", default="/data/models/mediapipe", help="hand_landmarker.task and pose_landmarker_full.task")
    ap.add_argument("--max-angle", type=float, default=35.0, help="half-angle (deg) of the 'in front' cone for the person")
    ap.add_argument("--min-vis", type=float, default=0.1)
    ap.add_argument("--occl-tol", type=float, default=0.05, help="m; depth gap that splits a hand's landmarks into clusters")
    ap.add_argument("--arm-tol", type=float, default=0.45, help="m; pose hand points farther than this from the elbow are rejected")
    ap.add_argument("--always", action="store_true", help="process every frame, subscribers or not")
    ap.add_argument("--follow", choices=POLICIES, default="palm",
                    help="what is published as a palm: hand = any point on the hand with depth, palm = the palm only (palm.py)")
    ap.add_argument("--delegate", choices=("gpu", "cpu"), default="gpu",
                    help="where MediaPipe runs (gpu: 9 ms a landmarker on the Thor, cpu: 25-35 ms; gpu needs --runtime nvidia)")
    ap.add_argument("--log-s", type=float, default=60.0, help="seconds between rate lines in the log")
    args = ap.parse_args(args_ros)

    rclpy.init(args=argv)
    node = Node("hand_tracker")
    log = node.get_logger()
    tracker = PalmTracker(args.models, args.max_angle, args.min_vis, args.occl_tol, args.arm_tol, args.delegate)
    frames = CameraFrames()
    pubs = {s: node.create_publisher(PointStamped, f"hand/{s}/palm", qos_profile_sensor_data) for s in SIDES}
    state_pub = node.create_publisher(String, "hand/state", 10)
    wake = threading.Event()

    def on_rgb(msg):
        frames.on_rgb(msg)
        wake.set()

    def on_depth(msg):           # a colour frame's depth often lands after it: try again then, not a frame later
        frames.on_depth(msg)
        wake.set()

    cam = args.camera.rstrip("/")
    node.create_subscription(Image, f"{cam}/color/image_raw", on_rgb, qos_profile_sensor_data)
    node.create_subscription(Image, f"{cam}/depth/image_raw", on_depth, qos_profile_sensor_data)
    node.create_subscription(CameraInfo, f"{cam}/color/camera_info", frames.on_info, qos_profile_sensor_data)

    def spin():
        try:
            rclpy.spin(node)
        except rclpy.executors.ExternalShutdownException:   # shut down from main: the normal end
            pass

    threading.Thread(target=spin, daemon=True).start()
    log.info(f"hand tracker up: {cam} -> {node.get_namespace().rstrip('/')}/hand/{{left,right}}/palm"
             + ("" if args.always else " (on demand)"))

    def wanted():
        return args.always or state_pub.get_subscription_count() > 0 or any(p.get_subscription_count() for p in pubs.values())

    last_stamp, n, n_follow, n_nodepth, busy, t_rate = None, 0, 0, 0, 0.0, time.monotonic()
    try:
        while rclpy.ok():
            if not wake.wait(1.0):
                continue
            wake.clear()
            frame = frames.latest()
            if frame is None or frame.info is None or not wanted() or frame.stamp == last_stamp:
                continue
            if frame.depth_msg is None:      # no depth within the pairing slop of any recent colour frame
                n_nodepth += 1
                continue
            last_stamp = frame.stamp
            t_work = time.monotonic()
            k = frame.info.k
            K = (k[0], k[4], k[2], k[5])
            rgb, depth_m = image_to_numpy(frame.rgb_msg), image_to_numpy(frame.depth_msg)
            depth_m[~np.isfinite(depth_m)] = 0
            person_z, palms = tracker.track(rgb, depth_m, K, frame.stamp)
            n += 1
            state = {"v": 1, "stamp": frame.stamp, "frame": frame.rgb_msg.header.frame_id,
                     "person_z": None if person_z is None else round(float(person_z), 3), "palms": {}}
            for i, side in enumerate(SIDES):
                palm = palms.get(i)
                src, xyz = (palm[0], palm[2]) if palm else (None, None)
                ok = followable(src, args.follow)
                state["palms"][side] = {"source": src, "followable": ok,
                                        "xyz": [round(float(c), 4) for c in xyz] if xyz else None}
                if ok:
                    m = PointStamped()
                    m.header = frame.rgb_msg.header
                    m.point.x, m.point.y, m.point.z = (float(c) for c in xyz)
                    pubs[side].publish(m)
                    n_follow += 1
            state_pub.publish(String(data=json.dumps(state)))
            busy += time.monotonic() - t_work
            if time.monotonic() - t_rate > args.log_s:
                span = time.monotonic() - t_rate
                log.info(f"{n / span:.1f} frames/s processed ({busy / max(n, 1) * 1000:.0f} ms each), "
                         f"{n_follow / span:.1f} followable palms/s, {n_nodepth} colour frames without depth")
                n, n_follow, n_nodepth, busy, t_rate = 0, 0, 0, 0.0, time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
