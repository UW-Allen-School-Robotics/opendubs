"""The hand tracker as a ROS 2 node: the running Femto Bolt driver's colour and depth in, palms out.

  python3 -m hand_tracker.node [--camera /camera] [--models /data/models/mediapipe] --ros-args -r __ns:=/perception

Topics (under the node's namespace, /perception in tools/hand_stack.sh):
  hand/<side>/palm   geometry_msgs/PointStamped  a FOLLOWABLE point of the hand only (--follow), in the colour image's own
                                                 frame (camera_color_optical_frame: x right, y down, z forward, metres),
                                                 stamped with the image. Nothing is published for a lost or rough palm,
                                                 so a follower's "fresh palm" test is also its "trustworthy palm" test.
  hand/state         std_msgs/String (JSON)      every processed frame: person distance; per side the palm source, xyz,
                                                 whether it was published, and the shoulder (pose + depth, only where
                                                 the pose model sees it in the image), and shoulder_mid: the nearest
                                                 person's shoulders as angles below and right of the optical axis (from
                                                 the landmarks, also just off the image). hand_follow.py and
                                                 treat_follow.py read both.
<side> is the person's own hand (palm.py): `right` is their right hand.

--follow (palm.py's sources):
  gated (default)  the palm always; any other point on the hand (one landmark, the palm with a wider depth patch, the pose
                   model's hand points) only within --gate-m of the last point published for that hand, less than
                   --gate-s ago. The arm keeps moving through the frames where the palm centre is not seen, and a stray
                   pixel elsewhere is never taken for the hand.
  palm             the prototype's: the palm only.   hand: any point on the hand, ungated.

REAL TIME: the images are taken undecoded (raw subscriptions: 2.7 MB of colour and 1.8 MB of depth, 14 times a second
each, cost the Python side of the receiver most of its time) and only the pair processed is decoded; the stamps for
pairing are read from the message's own header bytes. The pose landmarker (who is the person) runs every --pose-every
frames, the hand landmarker on every frame; the hand landmarker's thresholds are lowered so it keeps tracking a hand
from frame to frame rather than losing and re-detecting it. Newest frame only.

ON DEMAND: a frame is only run through MediaPipe while one of those topics has a subscriber.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import struct
import sys
import threading
import time

import numpy as np

from hand_tracker.palm import SIDES, PalmTracker, followable

FOLLOW = ("gated", "palm", "hand")


def cdr_stamp(raw: bytes) -> float:
    """header.stamp of a serialized message whose first field is a std_msgs/Header (CDR: 4 bytes of encapsulation, then
    int32 sec and uint32 nanosec, little-endian on this robot)."""
    sec, nsec = struct.unpack_from("<iI", raw, 4)
    return sec + nsec * 1e-9


class RawFrames:
    """The newest colour frame with a depth frame within `slop` of it, both still serialized."""

    def __init__(self, slop=0.05, max_age=0.5):
        self.slop, self.max_age = slop, max_age
        self.lock = threading.Lock()
        self.rgb = collections.deque(maxlen=6)
        self.depth = collections.deque(maxlen=10)
        self.info = None

    def put(self, which, raw):
        with self.lock:
            (self.rgb if which == "rgb" else self.depth).append((cdr_stamp(raw), raw))

    def latest(self):
        """(stamp, rgb raw, depth raw or None)."""
        with self.lock:
            if not self.rgb:
                return None
            newest = self.rgb[-1][0]
            for t, raw in reversed(self.rgb):
                if newest - t > self.max_age:
                    break
                best = min(self.depth, key=lambda d: abs(d[0] - t), default=None)
                if best is not None and abs(best[0] - t) <= self.slop:
                    return t, raw, best[1]
            return newest, self.rgb[-1][1], None


class Gate:
    """Per hand: the last point published and when (image time)."""

    def __init__(self, metres, seconds):
        self.metres, self.seconds, self.last = metres, seconds, {}

    def passes(self, side, source, xyz, stamp, policy):
        if policy != "gated":
            return followable(source, policy)
        if followable(source, "palm"):
            return True
        if not followable(source, "hand"):
            return False
        last = self.last.get(side)
        return bool(last and stamp - last[1] <= self.seconds and math.dist(last[0], xyz) <= self.metres)

    def published(self, side, xyz, stamp):
        self.last[side] = (tuple(xyz), stamp)


def main(argv=None):
    import rclpy
    from geometry_msgs.msg import PointStamped
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import CameraInfo, Image
    from std_msgs.msg import String

    from perception_server.frames import image_to_numpy

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
    ap.add_argument("--follow", choices=FOLLOW, default="gated", help="what is published as a palm (above)")
    ap.add_argument("--gate-m", type=float, default=0.15, help="gated: m a non-palm point may be from the last one published")
    ap.add_argument("--gate-s", type=float, default=1.0, help="gated: s the last one published counts for")
    ap.add_argument("--pose-every", type=int, default=3, help="run the pose landmarker every this many frames")
    ap.add_argument("--hand-confidence", type=float, default=0.3,
                    help="the hand landmarker's detection, presence and tracking thresholds (MediaPipe's default 0.5)")
    ap.add_argument("--delegate", choices=("gpu", "cpu"), default="gpu",
                    help="where MediaPipe runs (gpu: 9 ms a landmarker on the Thor, cpu: 25-35 ms; gpu needs --runtime nvidia)")
    ap.add_argument("--log-s", type=float, default=60.0, help="seconds between rate lines in the log")
    args = ap.parse_args(args_ros)

    rclpy.init(args=argv)
    node = Node("hand_tracker")
    log = node.get_logger()
    tracker = PalmTracker(args.models, args.max_angle, args.min_vis, args.occl_tol, args.arm_tol, args.delegate,
                          pose_every=args.pose_every, hand_confidence=args.hand_confidence)
    frames = RawFrames()
    gate = Gate(args.gate_m, args.gate_s)
    pubs = {s: node.create_publisher(PointStamped, f"hand/{s}/palm", qos_profile_sensor_data) for s in SIDES}
    state_pub = node.create_publisher(String, "hand/state", 10)
    wake = threading.Event()

    def taker(which):
        def take(raw):
            frames.put(which, raw)
            wake.set()            # a colour frame's depth often lands after it: try again then, not a frame later
        return take

    def on_info(msg):
        frames.info = msg

    cam = args.camera.rstrip("/")
    node.create_subscription(Image, f"{cam}/color/image_raw", taker("rgb"), qos_profile_sensor_data, raw=True)
    node.create_subscription(Image, f"{cam}/depth/image_raw", taker("depth"), qos_profile_sensor_data, raw=True)
    node.create_subscription(CameraInfo, f"{cam}/color/camera_info", on_info, qos_profile_sensor_data)

    def spin():
        try:
            rclpy.spin(node)
        except rclpy.executors.ExternalShutdownException:   # shut down from main: the normal end
            pass

    threading.Thread(target=spin, daemon=True).start()
    log.info(f"hand tracker up: {cam} -> {node.get_namespace().rstrip('/')}/hand/{{left,right}}/palm, follow {args.follow}"
             + ("" if args.always else " (on demand)"))

    def wanted():
        return args.always or state_pub.get_subscription_count() > 0 or any(p.get_subscription_count() for p in pubs.values())

    last_stamp, n, n_follow, n_nodepth, busy, t_rate = None, 0, 0, 0, 0.0, time.monotonic()
    try:
        while rclpy.ok():
            if not wake.wait(1.0):
                continue
            wake.clear()
            got = frames.latest()
            if got is None or frames.info is None or not wanted() or got[0] == last_stamp:
                continue
            stamp, rgb_raw, depth_raw = got
            if depth_raw is None:      # no depth within the pairing slop of any recent colour frame
                n_nodepth += 1
                continue
            last_stamp = stamp
            t_work = time.monotonic()
            rgb_msg = deserialize_message(rgb_raw, Image)
            rgb, depth_m = image_to_numpy(rgb_msg), image_to_numpy(deserialize_message(depth_raw, Image))
            depth_m[~np.isfinite(depth_m)] = 0
            k = frames.info.k
            person_z, palms, shoulders = tracker.track(rgb, depth_m, (k[0], k[4], k[2], k[5]), stamp)
            n += 1
            state = {"v": 1, "stamp": stamp, "frame": rgb_msg.header.frame_id,
                     "person_z": None if person_z is None else round(float(person_z), 3), "palms": {},
                     "shoulders": {s: None if shoulders.get(i) is None else [round(float(c), 4) for c in shoulders[i]]
                                   for i, s in enumerate(SIDES)},
                     "shoulder_mid": None if "mid" not in shoulders else
                     {"below_deg": round(shoulders["mid"]["below_deg"], 2), "right_deg": round(shoulders["mid"]["right_deg"], 2),
                      "vis": round(shoulders["mid"]["vis"], 2)}}
            for i, side in enumerate(SIDES):
                palm = palms.get(i)
                src, xyz = (palm[0], palm[2]) if palm else (None, None)
                ok = bool(xyz) and gate.passes(side, src, xyz, stamp, args.follow)
                state["palms"][side] = {"source": src, "followable": ok,
                                        "xyz": [round(float(c), 4) for c in xyz] if xyz else None}
                if ok:
                    gate.published(side, xyz, stamp)
                    m = PointStamped()
                    m.header = rgb_msg.header
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
