#!/usr/bin/env python3
"""capture_camera.py: a capture's camera, for any robot. A camera stream goes two ways:

  KEYFRAMES   <keyframe_prefix><topic>: a frame whenever the robot has moved or turned far enough, or
              its head (any joints, or none) has turned far enough, since the last frame kept, and at
              least one every floor_s. Recorded continuously (heavy/).
  BURST FEED  <burst_prefix><topic>: every frame, at up to burst.hz, into a rosbag2 recorder in snapshot
              mode (a rolling buffer). A new line in the capture's marks.txt makes a burst: after_s
              later the buffer is written out as that recorder's next file (heavy/bursts/bursts_<n>.mcap).

Everything robot-specific is in the robot's capture profile (capture_robots/<robot>.yaml beside this
file, selected with OPENDUBS_CAPTURE_ROBOT=<robot or path>, or --config): the odometry topic or TF frames,
the head's JointState topic and joint names (or none), the camera's topic patterns, the frames, the data
root, the relay prefixes and the burst recorder's node name. capture_robots/opendubs2.yaml is the
default, and fills in whatever another robot's file leaves out.

As a library (floor_mapping.py): load_config, camera_topics, keyframe_due, Relays(node, cfg), cache_bytes,
BurstTrigger.
As a program, for a robot with its own recorders:

  capture_camera.py relay --marks CAPTURE/marks.txt [--config FILE] [--source LABEL=TOPIC[:pass|jpeg|png]]...

runs the relays (and, with --marks, the burst trigger) until Ctrl-C, and prints the topics its heavy and
burst recorders should take, and the burst buffer size to give the snapshot recorder:
  ros2 bag record -s mcap --snapshot-mode --max-cache-size <bytes> --node-name <burst.recorder_node> ...
Without --source, the camera is found by the profile's camera.streams patterns.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES = os.path.join(HERE, "capture_robots")
DEFAULT_ROBOT = "opendubs2"


def profile_path(robot: str | None = None) -> str:
    """A profile by robot name (capture_robots/<name>.yaml) or path; OPENDUBS_CAPTURE_ROBOT, else opendubs2."""
    r = robot or os.environ.get("OPENDUBS_CAPTURE_ROBOT") or DEFAULT_ROBOT
    return r if (os.sep in r or r.endswith(".yaml")) else os.path.join(PROFILES, r + ".yaml")


CONFIG = profile_path()
KEEP_FRACTION = 0.8          # keep a frame 0.8 of a period after the last: 30 Hz in -> 15 out, despite jitter
RAW_COLOR_HZ = 3.0           # raw colour when nothing can compress it: 2.7 MB a frame
IMAGE_T = "sensor_msgs/msg/Image"
COMPRESSED_T = "sensor_msgs/msg/CompressedImage"


# ---- pure (tests/test_floor_mapping.py) ---------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    """The robot's capture profile over the default one (opendubs2), section by section and key by key; a
    list or a mapping value given in the profile replaces the default's. Numbers come back as floats."""
    import yaml
    def read(p):
        return yaml.safe_load(open(p)) or {}
    base = read(profile_path(DEFAULT_ROBOT))
    path = path or profile_path()
    got = read(path) if os.path.abspath(path) != os.path.abspath(profile_path(DEFAULT_ROBOT)) else {}
    out = {}
    for sec in set(base) | set(got):
        b, g = base.get(sec), got.get(sec)
        out[sec] = dict(b or {}, **(g or {})) if isinstance(b, dict) and isinstance(g, (dict, type(None))) \
            else (g if sec in got else b)
    for sec in ("keyframe", "burst"):
        out[sec] = {k: (v if isinstance(v, str) else float(v)) for k, v in out[sec].items()}
    r = out["robot"]
    r["odom"], r["head"] = r.get("odom") or "", r.get("head") or ""
    r["head_joints"], r["pose_frames"] = list(r.get("head_joints") or []), list(r.get("pose_frames") or [])
    out["parts"] = out.get("parts") or {}
    out["_path"] = path
    return out


def keyframe_due(last, now, cfg: dict) -> bool:
    """last, now: (time s, pose (x, y, yaw) or None, head joint angles (tuple) or None).
    True when no frame was kept yet, floor_s has passed, or the robot or head moved far enough."""
    if last is None or now[0] - last[0] >= cfg["floor_s"]:
        return True
    if last[1] is not None and now[1] is not None:
        (x0, y0, a0), (x1, y1, a1) = last[1], now[1]
        if math.hypot(x1 - x0, y1 - y0) >= cfg["move_m"]:
            return True
        if abs((a1 - a0 + math.pi) % (2 * math.pi) - math.pi) >= math.radians(cfg["turn_deg"]):
            return True
    if last[2] and now[2] and len(last[2]) == len(now[2]):
        if max(abs(b - a) for a, b in zip(last[2], now[2])) >= math.radians(cfg["head_deg"]):
            return True
    return False


def camera_topics(topics: dict[str, str], cam: dict, relay_prefixes=("/mapping/",)) -> dict:
    """From {topic: type}, the camera's streams by the profile's camera.streams patterns: "thin" lists
    (label, topic, how), how being "pass" (a CompressedImage, relayed as it is) or "jpeg" / "png" (a raw
    Image, compressed by the relay), and "whole" the small topics (camera.whole_types) under any stream's
    `base` group, recorded as they are."""
    import re
    skip = tuple(cam.get("exclude") or ()) + tuple(relay_prefixes)
    ok = {t: ty for t, ty in topics.items() if not t.startswith(skip)}
    out, bases, taken = {"thin": [], "whole": []}, [], set()
    for entry in cam.get("streams") or []:
        for c in entry["try"]:
            if c["how"] == "pass" and entry["label"] == "color" and os.environ.get("OPENDUBS_COLOR_FROM_RAW"):
                continue        # the driver lists a compressed colour stream that sends nothing (Femto, 2026-11-11)
            want = COMPRESSED_T if c["how"] == "pass" else IMAGE_T
            for t in sorted(ok):
                m = re.search(c["match"], t)
                if not m or ok[t] != want:
                    continue
                label = entry["label"].format(**m.groupdict())
                if label in taken:
                    continue
                taken.add(label)
                out["thin"].append((label, t, c["how"]))
                if m.groupdict().get("base") and m["base"] not in bases:
                    bases.append(m["base"])
    types = set(cam.get("whole_types") or ())
    out["whole"] = sorted(t for t, ty in ok.items() if ty in types and any(t.startswith(b + "/") for b in bases))
    return out


# ---- ROS ------------------------------------------------------------------------------------------------

class Relays:
    """The keyframe and burst outputs of each camera stream, on an existing rclpy node (spun elsewhere)."""

    def __init__(self, node, cfg: dict):
        self.node, self.cfg = node, cfg
        self.counts: dict[str, int] = {}       # frames into the burst feed, per stream
        self.kept: dict[str, int] = {}         # keyframes kept, per stream
        self.frames: dict[str, str] = {}       # each stream's frame_id
        self.fed_bytes = 0                     # bytes into the burst feed, all streams
        self.pairs: list[tuple[str, str]] = [] # (keyframe topic, burst topic) per stream
        self.labels: dict[str, tuple[str, str, str]] = {}   # label -> (source, keyframe topic, burst topic)
        self.state = {"pose": None, "head": None}
        self._watch_robot()

    def _watch_robot(self):
        from rclpy.qos import qos_profile_sensor_data
        r = self.cfg["robot"]
        if r["pose_frames"]:
            from tf2_ros import Buffer, TransformListener
            self._tf = Buffer()
            self._tfl = TransformListener(self._tf, self.node, spin_thread=False)
        elif r["odom"]:
            from nav_msgs.msg import Odometry

            def odom(m):
                q = m.pose.pose.orientation
                self.state["pose"] = (m.pose.pose.position.x, m.pose.pose.position.y,
                                      math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)))
            self.node.create_subscription(Odometry, r["odom"], odom, qos_profile_sensor_data)
        if r["head"] and r["head_joints"]:
            from sensor_msgs.msg import JointState

            def head(m):
                j = dict(zip(m.name, m.position))
                if all(n in j for n in r["head_joints"]):
                    self.state["head"] = tuple(j[n] for n in r["head_joints"])
            self.node.create_subscription(JointState, r["head"], head, 10)

    def _outputs(self, label: str, cls, suffix: str):
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)
        kt, bt = self.cfg["relay"]["keyframe_prefix"] + suffix, self.cfg["relay"]["burst_prefix"] + suffix
        burst, key = self.node.create_publisher(cls, bt, qos), self.node.create_publisher(cls, kt, qos)
        self.pairs.append((kt, bt))
        last = [None]
        self.counts[label] = self.kept[label] = 0

        def publish(msg):
            if self.cfg["robot"]["pose_frames"]:
                self.state["pose"] = self._tf_pose()
            burst.publish(msg)
            self.counts[label] += 1
            self.fed_bytes += len(msg) if isinstance(msg, bytes) else len(msg.data)
            now = (time.monotonic(), self.state["pose"], self.state["head"])
            if keyframe_due(last[0], now, self.cfg["keyframe"]):
                last[0] = now
                key.publish(msg)
                self.kept[label] += 1
        return publish, (kt, bt)

    def _tf_pose(self):
        """(x, y, yaw) of pose_frames[1] in pose_frames[0] from TF, or None."""
        from rclpy.time import Time
        try:
            tr = self._tf.lookup_transform(*self.cfg["robot"]["pose_frames"][:2], Time()).transform
        except Exception:
            return None
        q = tr.rotation
        return (tr.translation.x, tr.translation.y,
                math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)))

    def _group(self):
        """Each relay in its own callback group: a multi-threaded executor then runs them side by side
        (OpenCV and the middleware release the GIL) instead of one at a time."""
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
        return MutuallyExclusiveCallbackGroup()

    def add(self, label: str, topic: str, how: str, typ: str | None = None) -> tuple[str, str]:
        """One stream: how "pass" (relay as it is, typ its message type) or "jpeg" / "png" (compress a raw
        Image). Returns (keyframe topic, burst topic)."""
        hz = self.cfg["burst"]["hz"]
        out = self.thin(label, topic, typ or COMPRESSED_T, hz) if how == "pass" else self.encode(label, topic, how, hz)
        self.labels[label] = (topic, *out)
        return out

    def thin(self, label: str, topic: str, typ: str, hz: float):
        """Every message of `topic` at least 1/hz after the last one, as raw bytes (nothing decoded)."""
        from rclpy.qos import qos_profile_sensor_data
        from rclpy.serialization import deserialize_message
        from rosidl_runtime_py.utilities import get_message
        cls = get_message(typ)
        publish, out = self._outputs(label, cls, topic)
        gap, last = KEEP_FRACTION / hz, [0.0]

        def cb(raw: bytes):
            now = time.monotonic()
            if now - last[0] >= gap:
                last[0] = now
                publish(raw)
                if label not in self.frames:
                    self.frames[label] = deserialize_message(raw, cls).header.frame_id
        self.node.create_subscription(cls, topic, cb, qos_profile_sensor_data, raw=True, callback_group=self._group())
        return out

    def encode(self, label: str, topic: str, how: str, hz: float):
        """A raw Image at hz: "jpeg" (8-bit colour or mono, quality 90) or "png" (16-bit depth, lossless,
        format "16UC1; png"). Only the frames kept are decoded. Without OpenCV, the raw images instead
        (colour at RAW_COLOR_HZ)."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            hz = RAW_COLOR_HZ if how == "jpeg" else hz
            print(f"WARNING: no OpenCV (python3-opencv): recording {topic} RAW at {hz:g} Hz")
            return self.thin(label, topic, IMAGE_T, hz)
        from rclpy.qos import qos_profile_sensor_data
        from rclpy.serialization import deserialize_message
        from sensor_msgs.msg import CompressedImage, Image
        publish, out = self._outputs(label, CompressedImage, f"{topic}/" + ("compressed" if how == "jpeg" else "png"))
        gap, last, warned = KEEP_FRACTION / hz, [0.0], set()

        def cb(raw: bytes):
            now = time.monotonic()
            if now - last[0] < gap:
                return
            last[0] = now
            m = deserialize_message(raw, Image)
            if m.encoding in ("rgb8", "bgr8", "mono8"):
                ch = 1 if m.encoding == "mono8" else 3
                img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.step)[:, :m.width * ch]
                img = img.reshape(m.height, m.width, ch)
                if m.encoding == "rgb8":
                    img = img[..., ::-1]
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                fmt = f"{m.encoding}; jpeg compressed {'mono8' if ch == 1 else 'bgr8'}"
            elif m.encoding in ("16UC1", "mono16"):
                img = np.frombuffer(m.data, ">u2" if m.is_bigendian else "<u2").reshape(m.height, m.step // 2)
                ok, buf = cv2.imencode(".png", np.ascontiguousarray(img[:, :m.width]), [cv2.IMWRITE_PNG_COMPRESSION, 1])
                fmt = f"{m.encoding}; png"
            else:
                if m.encoding not in warned:
                    warned.add(m.encoding)
                    print(f"WARNING: {topic} is {m.encoding}, which this relay does not compress: skipped")
                return
            if ok:
                publish(CompressedImage(header=m.header, format=fmt, data=buf.tobytes()))
                self.frames.setdefault(label, m.header.frame_id)
        self.node.create_subscription(Image, topic, cb, qos_profile_sensor_data, raw=True, callback_group=self._group())
        return out


def cache_bytes(relays: Relays, seconds: float = 5.0) -> int:
    """The burst buffer, sized by time: the feed's measured bytes a second times before_s + after_s
    (+10%), at most cache_mb. rosbag2's buffer is in bytes, so a fixed size would hold far more seconds of
    a slow camera than of a fast one. Waits `seconds` to measure."""
    b = relays.cfg["burst"]
    f0 = relays.fed_bytes
    time.sleep(seconds)
    rate = (relays.fed_bytes - f0) / seconds
    cap = b["cache_mb"] * 1e6
    cache = int(min(cap, rate * (b["before_s"] + b["after_s"]) * 1.1)) if rate else int(cap)
    print(f"burst buffer: {cache / 1e6:.0f} MB (camera feed {rate / 1e6:.2f} MB/s"
          + (", none yet: the cap" if not rate else ", at the cap" if cache == int(cap) else "") + ")")
    return cache


def snapshot(node, recorder_node: str, timeout_s: float = 30.0) -> str:
    """Write a snapshot recorder's buffer out as its next file: '' on success, else why not."""
    from rosbag2_interfaces.srv import Snapshot
    cli = node.create_client(Snapshot, f"/{recorder_node}/snapshot")
    try:
        if not cli.wait_for_service(timeout_sec=5.0):
            return f"/{recorder_node} offers no snapshot service"
        fut = cli.call_async(Snapshot.Request())
        end = time.monotonic() + timeout_s
        while not fut.done() and time.monotonic() < end:
            time.sleep(0.1)
        return "" if fut.done() and fut.result().success else f"/{recorder_node} did not confirm the snapshot"
    finally:
        node.destroy_client(cli)


class BurstTrigger:
    """Watches marks.txt: for each new line, after_s later, the burst recorder writes its buffer out.
    A mark less than before_s + after_s after the last starts where that burst ended (rosbag2 empties the
    buffer on each snapshot), so no frame is written twice. stop() writes any burst still waiting."""

    def __init__(self, node, marks: str, cfg: dict):
        self.node, self.marks, self.cfg = node, marks, cfg
        self.seen, self.due, self.count = self._lines(), [], 0
        self._stop = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _lines(self) -> int:
        try:
            with open(self.marks) as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    def _burst(self):
        why = snapshot(self.node, self.cfg["burst"]["recorder_node"])
        print(f"  burst {self.count}: " + (why or "written"))
        self.count += 1

    def _new(self, when: float):
        k = self._lines()
        self.due += [when] * max(k - self.seen, 0)
        self.seen = max(k, self.seen)

    def _watch(self):
        while not self._stop.wait(1.0):
            self._new(time.monotonic() + self.cfg["burst"]["after_s"])
            while self.due and self.due[0] <= time.monotonic():
                self.due.pop(0)
                self._burst()

    def stop(self):
        self._stop.set()
        self._new(0.0)
        for _ in self.due:
            self._burst()
        self.due = []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("relay", help="run the keyframe and burst relays (and the burst trigger) until Ctrl-C")
    r.add_argument("--config", help="the robot's capture profile (default: OPENDUBS_CAPTURE_ROBOT, else opendubs2)")
    r.add_argument("--marks", help="the capture's marks.txt: a new line makes a burst")
    r.add_argument("--source", action="append", default=[], help="LABEL=TOPIC[:pass|jpeg|png] (default: found by name)")
    args = ap.parse_args()
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.signals import SignalHandlerOptions
    cfg = load_config(args.config)
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)   # Ctrl-C: write any waiting burst first
    node = rclpy.create_node("capture_camera")
    ex = MultiThreadedExecutor()
    ex.add_node(node)
    threading.Thread(target=ex.spin, daemon=True).start()
    time.sleep(2.0)                                     # let discovery fill in
    topics = {t: ty[0] for t, ty in node.get_topic_names_and_types()}
    if args.source:
        sources = []
        for s in args.source:
            label, rest = s.split("=", 1)
            topic, _, how = rest.partition(":")
            sources.append((label, topic, how or ("pass" if topics.get(topic) == COMPRESSED_T else "jpeg")))
        whole = []
    else:
        cam = camera_topics(topics, cfg["camera"], (cfg["relay"]["keyframe_prefix"], cfg["relay"]["burst_prefix"]))
        sources, whole = cam["thin"], cam["whole"]
    if not sources:
        print("no camera streams found (give --source LABEL=TOPIC)")
        return 1
    relays = Relays(node, cfg)
    for label, topic, how in sources:
        relays.add(label, topic, how, topics.get(topic))
    print("heavy recorder topics: " + " ".join(k for k, _ in relays.pairs))
    print("burst recorder topics: " + " ".join(b for _, b in relays.pairs))
    if whole:
        print("record whole (light): " + " ".join(whole))
    print(f"burst recorder: --snapshot-mode --max-cache-size {cache_bytes(relays)} --node-name {cfg['burst']['recorder_node']}")
    trig = BurstTrigger(node, args.marks, cfg) if args.marks else None
    try:
        while True:
            time.sleep(30)
            print("  " + " ".join(f"{k}={relays.kept[k]}/{c}" for k, c in relays.counts.items())
                  + (f" bursts={trig.count}" if trig else ""))
    except KeyboardInterrupt:
        pass
    finally:
        if trig:
            trig.stop()
        ex.shutdown()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
