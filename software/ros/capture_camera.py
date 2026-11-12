#!/usr/bin/env python3
"""capture_camera.py: a capture's camera, for any robot. A camera stream goes two ways:

  KEYFRAMES   <keyframe_prefix><topic>: a frame whenever the robot has moved or turned far enough, or
              its head (any joints, or none) has turned far enough, since the last frame kept, and at
              least one every floor_s. Recorded continuously (heavy/).
  BURST FEED  <burst_prefix><topic>: every frame, at up to burst.hz, into a rosbag2 recorder in snapshot
              mode (a rolling buffer). A new line in the capture's marks.txt makes a burst: after_s
              later the buffer is written out as that recorder's next file (heavy/bursts/bursts_<n>.mcap).

Everything robot-specific is in the config (capture_camera.yaml beside this file, or --config /
OPENDUBS_CAPTURE_CAMERA): the odometry topic, the head's JointState topic and joint names (or none), the
map and base frames, the data root, the relay prefixes and the burst recorder's node name. OpenDubs's
values are the defaults; another robot (Spot: base frame `body`, no head) gives its own file.

As a library (floor_mapping.py): load_config, keyframe_due, Relays(node, cfg), cache_bytes, BurstTrigger.
As a program, for a robot with its own recorders:

  capture_camera.py relay --marks CAPTURE/marks.txt [--config FILE] [--source LABEL=TOPIC[:pass|jpeg|png]]...

runs the relays (and, with --marks, the burst trigger) until Ctrl-C, and prints the topics its heavy and
burst recorders should take, and the burst buffer size to give the snapshot recorder:
  ros2 bag record -s mcap --snapshot-mode --max-cache-size <bytes> --node-name <burst.recorder_node> ...
Without --source, the camera is found by name (.../color/image_raw..., .../depth/image_raw...).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

CONFIG = os.environ.get("OPENDUBS_CAPTURE_CAMERA",
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_camera.yaml"))
DEFAULTS = {
    "robot": {"odom": "/odom", "head": "/head/joint_states", "head_joints": ["head_pan", "head_tilt"],
              "map_frame": "map", "base_frame": "base_link", "data_root": "/var/lib/opendubs/bags",
              "exclude": ["/tablet/"]},
    "relay": {"keyframe_prefix": "/mapping/key", "burst_prefix": "/mapping/burst"},
    "keyframe": {"move_m": 0.5, "turn_deg": 10.0, "head_deg": 5.0, "floor_s": 2.0},
    "burst": {"hz": 15.0, "before_s": 20.0, "after_s": 20.0, "cache_mb": 200.0, "recorder_node": "opendubs_bursts"},
}
KEEP_FRACTION = 0.8          # keep a frame 0.8 of a period after the last: 30 Hz in -> 15 out, despite jitter
RAW_COLOR_HZ = 3.0           # raw colour when nothing can compress it: 2.7 MB a frame
IMAGE_T = "sensor_msgs/msg/Image"
COMPRESSED_T = "sensor_msgs/msg/CompressedImage"


# ---- pure (tests/test_floor_mapping.py) ---------------------------------------------------------------

def load_config(path: str = CONFIG) -> dict:
    """The config over DEFAULTS, key by key. Numbers come back as floats; an empty `head` means none."""
    import yaml
    try:
        got = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        got = {}
    out = {}
    for sec, d in DEFAULTS.items():
        g = got.get(sec) or {}
        out[sec] = {k: (float(g.get(k, v)) if isinstance(v, float) else g.get(k, v)) for k, v in d.items()}
    out["robot"]["head"] = out["robot"]["head"] or ""
    out["robot"]["head_joints"] = list(out["robot"]["head_joints"] or [])
    out["robot"]["exclude"] = list(out["robot"]["exclude"] or [])
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


def camera_topics(topics: dict[str, str], exclude=("/tablet/",), relay_prefixes=("/mapping/",)) -> dict:
    """From {topic: type}, a depth camera's streams found by name: "thin" lists (label, topic, how) for
    colour and depth, how being "pass" (a compressed transport, relayed as it is) or "jpeg" / "png" (only
    the raw image exists: the relay compresses it), and "whole" the camera's small topics (camera_info,
    IMU) to record as they are. OPENDUBS_COLOR_FROM_RAW=1 skips a listed compressed colour stream."""
    def find(suffix, typ):
        hits = sorted(t for t, ty in topics.items() if t.endswith(suffix) and ty == typ
                      and not any(t.startswith(e) for e in (*exclude, *relay_prefixes)))
        return hits[0] if hits else None
    out = {"thin": [], "whole": []}
    base = None
    for kind, transport, enc in (("color", "compressed", "jpeg"), ("depth", "compressedDepth", "png")):
        topic, how = find(f"/{kind}/image_raw/{transport}", COMPRESSED_T), "pass"
        if kind == "color" and os.environ.get("OPENDUBS_COLOR_FROM_RAW"):
            topic = None        # the driver lists a compressed colour stream that sends nothing (Femto, 2026-11-11)
        if not topic:
            topic, how = find(f"/{kind}/image_raw", IMAGE_T), enc
        if topic:
            out["thin"].append((kind, topic, how))
            base = base or topic[:topic.index(f"/{kind}/")]
    if base:
        out["whole"] = sorted(t for t, ty in topics.items() if t.startswith(base + "/") and
                              ty in ("sensor_msgs/msg/CameraInfo", "sensor_msgs/msg/Imu"))
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
        self.state = {"pose": None, "head": None}
        self._watch_robot()

    def _watch_robot(self):
        from rclpy.qos import qos_profile_sensor_data
        r = self.cfg["robot"]
        if r["odom"]:
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
            burst.publish(msg)
            self.counts[label] += 1
            self.fed_bytes += len(msg) if isinstance(msg, bytes) else len(msg.data)
            now = (time.monotonic(), self.state["pose"], self.state["head"])
            if keyframe_due(last[0], now, self.cfg["keyframe"]):
                last[0] = now
                key.publish(msg)
                self.kept[label] += 1
        return publish, (kt, bt)

    def _group(self):
        """Each relay in its own callback group: a multi-threaded executor then runs them side by side
        (OpenCV and the middleware release the GIL) instead of one at a time."""
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
        return MutuallyExclusiveCallbackGroup()

    def add(self, label: str, topic: str, how: str, typ: str | None = None) -> tuple[str, str]:
        """One stream: how "pass" (relay as it is, typ its message type) or "jpeg" / "png" (compress a raw
        Image). Returns (keyframe topic, burst topic)."""
        hz = self.cfg["burst"]["hz"]
        return self.thin(label, topic, typ or COMPRESSED_T, hz) if how == "pass" else self.encode(label, topic, how, hz)

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
    r.add_argument("--config", default=CONFIG)
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
        cam = camera_topics(topics, cfg["robot"]["exclude"], (cfg["relay"]["keyframe_prefix"], cfg["relay"]["burst_prefix"]))
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
