#!/usr/bin/env python3
"""Map one floor of a building, attended: a fresh SLAM map, a recording of the drive, and the saved map.

Run at the robot by the person driving it, through floor-mapping.sh (which sources ROS and the robot's
DDS profile). One floor is one command:

  floor-mapping.sh check                 free disk, which topics are live, SLAM's mode, and a 6 s
                                         trial recording's message counts: run it first
  floor-mapping.sh floor FRAME           e.g. bldg_f1. Restarts SLAM as a fresh map, records the drive,
                                         and when you press Ctrl-C ONCE, stops the recording and saves
                                         the map as FRAME-<date>
  floor-mapping.sh save FRAME            save SLAM's current map only (if `floor` was cut short)

Nothing here drives the robot: the operator drives it by RC, as for any mapping drive. Live SLAM runs
as it always does (opendubs-slam, slam_toolbox on the front scan); `floor` only restarts it, so each
floor starts from an empty map, which is why nothing has to track the robot between floors.

THE RECORDING (a ros2 bag, mcap with zstd chunks, split every 2 GB) is for rebuilding the map offline,
with or without the dead-wheel odometry and with both LIDARs. It holds:
  /scan /scan_rear /scan_filtered        both LIDARs raw, and the front one as SLAM sees it
  /scan_rear_filtered /scan_depth        when something publishes them (the rear body filter, the depth
                                         camera flattened to a scan): recorded if present, never needed
  /odom /odom/raw                        the Pinpoint pose, and its [ODO] line verbatim (raw pod counts,
                                         status bits, failed reads), published by drive_bridge.py
  /tf /tf_static                         including SLAM's map -> odom
  /head/joint_states, _targets, _limits  the neck's pan and tilt (the dashboard's neck_state.py)
  /opendubs/joint_states                 what robot_state_publisher was told the lift and neck were, so
                                         the camera's TF in the bag can be checked against the real neck
  the depth camera, if its driver is running: colour and depth at CAM_HZ, enough to judge visual and
      RGB-D odometry offline, republished under /mapping/... (the bag records only these copies), and
      its camera_info and IMU at full rate. Its compressed transports are passed through untouched;
      if it publishes only raw images, the relay compresses them itself (colour JPEG on
      /mapping/<topic>/compressed; depth lossless 16-bit PNG on /mapping/<topic>/png, format
      "16UC1; png", decoded with cv2.imdecode(..., IMREAD_UNCHANGED)). Without OpenCV the raw images
      are recorded instead, colour at RAW_COLOR_HZ. The camera's topics are found by name
      (.../color/image_raw...), so any driver namespace works; with no camera, the recording goes on
      without it and says so.
  NOT /map: it is the size of the floor every 2 s. The map is saved separately, at the end.

Colour frames are pictures of whoever is in the corridor. The bag stays on the robot and on the
project's own machines, never in the repo or anywhere public; --no-color leaves colour out.

THE DISK: `check` measures the recording's rate from its trial (EXPECTED_GB_H, about 30 GB an hour
with the camera at 15 Hz, is only the estimate it falls back on). `floor` refuses to start with less than MIN_FREE_GB + START_MARGIN_GB free, and stops the
recording itself (and still saves the map) if free space falls to MIN_FREE_GB, so a long drive can
never fill the robot's disk. Every 30 s it prints the recording's size, its rate and the minutes left.

THE SAVED MAP, in MAPS (/var/lib/opendubs/maps), the formats README.md "SLAM and the building floor
plan" describes:
  FRAME-<date>.posegraph, .data   slam_toolbox's serialize_map, for localizing on it later
  FRAME-<date>.pgm, .yaml         /map as a map_server image (trinary), for floorplan.py fit
  site.FRAME-<date>.yaml          a site file for this map with its start pose. NOT ACTIVE: SLAM reads
                                  only site.yaml. floorplan.py fit writes the full one, with the floor
                                  frame, once the map is fitted to the plan.
`floor` refuses to start while site.yaml exists, because SLAM would then localize instead of mapping.

THE MANIFEST: at the end, `floor` writes capture.yaml (opendubs.capture/1, the project's site repo
schemas/capture.schema.json; a copy is in tests/ and the tests validate against it) inside the bag's
directory, and copies the map's .pgm, .yaml and site file beside it, so that directory is the capture
to ingest. It names the building and floor (parsed from FRAME, <building>_f<floor>: bldg_f2 -> floor
02, bldg_fG -> 0G; the building's FMS number from MAPS/buildings.yaml, {key: "number"}, site data the
building-model lane owns), the times and whether the clock was NTP-synced, the robot, its URDF's
sha256, the code /opt/opendubs/DEPLOYED says was deployed, the operator and the start landmark
(--operator, --start, or asked at the start), the sensors, the map, and every bag file by path,
sha256 and size. Hashing a long drive's bag takes a minute or so. `floor` checks all of it can be
filled before it restarts SLAM.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import socket
import shutil
import signal
import subprocess
import sys
import threading
import time

MAPS = os.environ.get("OPENDUBS_MAPS", "/var/lib/opendubs/maps")
BAGS = os.environ.get("OPENDUBS_BAG_DIR", os.path.expanduser("~/bags"))
MIN_FREE_GB = float(os.environ.get("OPENDUBS_BAG_MIN_FREE_GB", "20"))
START_MARGIN_GB = 10.0
CAM_HZ = 15.0                            # colour and depth: what visual / RGB-D odometry needs
RAW_COLOR_HZ = 3.0                       # raw colour when nothing can compress it: 2.7 MB a frame
TRIAL_S = 6.0                            # check's trial recording
MIN_CAM_HZ = 12.0                        # below this, check warns that the camera came out too slow
KEEP_FRACTION = 0.8                     # keep a frame 0.8 of a period after the last: 30 Hz in -> 15 out, despite jitter
EXPECTED_GB_H = 30.0                     # an estimate (JPEG ~0.2 MB, depth PNG ~0.35 MB a frame at 15 Hz)
SPLIT_BYTES = 2 * 1024 ** 3
SLAM_UNIT = "opendubs-slam"
SLAM_ENABLED = "/etc/opendubs/slam.enabled"

CORE = ["/scan", "/scan_rear", "/scan_filtered", "/odom", "/odom/raw", "/tf", "/tf_static",
        "/head/joint_states", "/head/joint_targets", "/head/joint_limits"]
OPTIONAL = ["/scan_rear_filtered", "/scan_depth", "/opendubs/joint_states"]   # recorded when present
NEEDED = ["/scan", "/scan_rear", "/odom", "/tf"]      # without these the drive is not worth recording
RELAY_PREFIX = "/mapping"
DEPLOYED = os.environ.get("OPENDUBS_DEPLOYED", "/opt/opendubs/DEPLOYED")
URDF = os.environ.get("OPENDUBS_URDF", "/opt/opendubs/description/opendubs.urdf")
BUILDINGS = os.path.join(MAPS, "buildings.yaml")
CAPTURE_SCHEMA = "opendubs.capture/1"
CAMERA_MODEL = "Orbbec Femto Bolt"       # the robot description's head camera
SLAM_DESCRIPTION = "slam_toolbox (online async) on /scan_filtered, Pinpoint odometry"
# (id, model, topics, frame_id, calibration): a sensor is listed when any of its topics was recorded
SENSORS = [
    ("lidar_front", "RPLIDAR A1", ["/scan", "/scan_filtered"], "laser", "urdf"),
    ("lidar_rear", "RPLIDAR A1", ["/scan_rear", "/scan_rear_filtered"], "laser_rear", "urdf"),
    ("odom_pinpoint", "goBILDA Pinpoint", ["/odom", "/odom/raw"], "odom", "urdf"),
    ("neck", "M5Dial neck, servo pan and tilt", ["/head/joint_states", "/head/joint_targets",
                                                  "/head/joint_limits", "/opendubs/joint_states"], "head_tilt_link", "none"),
]
IMAGE_T = "sensor_msgs/msg/Image"
COMPRESSED_T = "sensor_msgs/msg/CompressedImage"


# ---- pure helpers (tests/test_floor_mapping.py) ----------------------------------------------------

def trinary(value: int, occupied: int = 65, free: int = 25) -> int:
    """One OccupancyGrid cell (-1 unknown, 0..100) as a map_server trinary pixel: 0 occupied, 254 free,
    205 unknown, with map_server's default thresholds (the .yaml's occupied_thresh 0.65, free 0.25)."""
    if value < 0:
        return 205
    if value >= occupied:
        return 0
    if value <= free:
        return 254
    return 205


def pgm_bytes(width: int, height: int, data) -> bytes:
    """A binary PGM of an OccupancyGrid's cells. The grid's row 0 is its BOTTOM (lowest y), a PGM's row 0
    is its top, so rows are written last first: floorplan.read_map reads it back the same way."""
    rows = bytearray()
    for r in range(height - 1, -1, -1):
        rows += bytes(trinary(v) for v in data[r * width:(r + 1) * width])
    return f"P5\n{width} {height}\n255\n".encode() + bytes(rows)


def map_yaml(image: str, resolution: float, origin) -> str:
    x, y = origin
    return (f"image: {image}\nmode: trinary\nresolution: {resolution:.4f}\n"
            f"origin: [{x:.4f}, {y:.4f}, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n")


def camera_topics(topics: dict[str, str]) -> dict:
    """From {topic: type}, the depth camera's streams: "thin" lists (kind, topic, how) for colour and
    depth, how being "pass" (a compressed transport, relayed as it is) or "encode" (only the raw image
    exists: the relay compresses it), and "whole" the small topics recorded as they are. The tablet's
    cameras (/tablet/...) are a different thing and are left out."""
    def find(suffix, typ):
        hits = sorted(t for t, ty in topics.items() if t.endswith(suffix) and ty == typ
                      and not t.startswith("/tablet/") and not t.startswith(RELAY_PREFIX + "/"))
        return hits[0] if hits else None
    out = {"thin": [], "whole": []}
    base = None
    for kind, transport in (("color", "compressed"), ("depth", "compressedDepth")):
        topic, how = find(f"/{kind}/image_raw/{transport}", COMPRESSED_T), "pass"
        if not topic:
            topic, how = find(f"/{kind}/image_raw", IMAGE_T), "encode"
        if topic:
            out["thin"].append((kind, topic, how))
            base = base or topic[:topic.index(f"/{kind}/")]
    if base:
        out["whole"] = sorted(t for t, ty in topics.items() if t.startswith(base + "/") and
                              ty in ("sensor_msgs/msg/CameraInfo", "sensor_msgs/msg/Imu"))
    return out


def map_name(frame: str, existing, today: datetime.date) -> str:
    """FRAME-<yyyymmdd>, or with -2, -3 ... when that is taken. A robot clock still at 1970 (no NTP yet)
    gives FRAME-run instead of a misleading date."""
    stem = f"{frame}-{today:%Y%m%d}" if today.year >= 2026 else f"{frame}-run"
    name, k = stem, 2
    while name in existing:
        name, k = f"{stem}-{k}", k + 1
    return name


def parse_frame(frame: str):
    """(building key, FMS floor code) from a floor frame: bldg_f2 -> ("bldg", "02"), bldg_fG -> "0G",
    bldg_fB1 -> "B1". None when FRAME is not <building>_f<floor>."""
    m = re.fullmatch(r"([a-z0-9]+)_f(G|B[0-9]|M[0-9]|RF|[0-9]{1,2})", frame)
    if not m:
        return None
    fl = m[2]
    return m[1], "0G" if fl == "G" else fl.zfill(2) if fl.isdigit() else fl


def parse_deployed(text: str) -> dict:
    """/opt/opendubs/DEPLOYED (deploy-all.sh's key=value lines) as the manifest's code: block."""
    kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    code = {"opendubs_sha": kv.get("sha", "")}
    if kv.get("branch") and kv["branch"] != "main":
        code["branch"] = kv["branch"]
    code["dirty"] = "UNCOMMITTED" in kv.get("source", "")
    return code


def parse_bag_info(text: str):
    """(duration in s, [{name, type, count}]) from `ros2 bag info`."""
    d = re.search(r"Duration:\s*([0-9.]+)\s*s", text)
    topics = [{"name": m[1], "type": m[2], "count": int(m[3])}
              for m in re.finditer(r"Topic: (\S+) \| Type: (\S+) \| Count: (\d+)", text)]
    return (float(d[1]) if d else 0.0), topics


def capture_id(day: datetime.date, key: str, floor_code: str, map_name_: str) -> str:
    """<yyyymmdd>-<building>-<floor>[-n], n taken from the map's own -n (map_name never overwrites)."""
    m = re.search(r"-([0-9]+)$", map_name_)
    n = f"-{m[1]}" if m and len(m[1]) < 8 else ""   # not the date itself
    return f"{day:%Y%m%d}-{key}-{floor_code}{n}"


def sensors_block(recorded: dict, cameras: list) -> list:
    """The sensors whose topics hold messages. cameras: (id, topics, frame_id) from the relays."""
    out = []
    for sid, model, topics, frame, cal in SENSORS:
        got = [t for t in topics if recorded.get(t)]
        if got:
            out.append({"id": sid, "model": model, "topics": got, "frame_id": frame, "calibration": cal})
    for sid, topics, frame in cameras:
        got = [t for t in topics if recorded.get(t)]
        if got:
            out.append({"id": sid, "model": CAMERA_MODEL, "topics": got, "frame_id": frame, "calibration": "factory"})
    return out


def capture_manifest(*, cid, key, fms, floor_code, start, end, ntp, robot, code, operator, landmark,
                     room, sensors, map_name_, resolution, map_files, bag_files, duration_s, topics,
                     color, notes=None) -> dict:
    """The opendubs.capture/1 document, in the schema's key order."""
    doc = {"schema": CAPTURE_SCHEMA, "id": cid, "building": {"key": key, "fms": fms}, "floor": floor_code,
           "time": {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
                    "clock": "ntp" if ntp else "unsynced"},
           "robot": robot, "code": code, "operator": operator, "sensors": sensors,
           "start": {"landmark": landmark, **({"room": room} if room else {})},
           "map": {"name": map_name_, **({"resolution_m": round(resolution, 4)} if resolution else {}),
                   "files": map_files},
           "bag": {"storage": "mcap/zstd_fast", "files": bag_files, "duration_s": round(duration_s, 3),
                   "bytes": sum(f["bytes"] for f in bag_files),
                   "topics": [dict(t, **({"hz": round(t["count"] / duration_s, 2)} if duration_s else {}))
                              for t in topics]},
           "privacy": {"color": color}}
    if notes:
        doc["notes"] = notes
    return doc


# ---- the robot side ---------------------------------------------------------------------------------

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_entry(path: str, host: str | None = None, rel: str | None = None) -> dict:
    e = {"host": host} if host else {}
    e.update(path=rel or path, sha256=sha256(path), bytes=os.path.getsize(path))
    return e


def ntp_synced() -> bool:
    try:
        return subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                              text=True, timeout=5).stdout.strip() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return False


def buildings() -> dict:
    import yaml
    try:
        return {str(k): str(v) for k, v in (yaml.safe_load(open(BUILDINGS)) or {}).items()}
    except FileNotFoundError:
        return {}


def manifest_inputs(args) -> str:
    """'' when everything the manifest needs is at hand (asking for the operator and the start if
    they were not given), else why not: checked BEFORE the drive, not after it."""
    pf = parse_frame(args.frame)
    if not pf:
        return f"{args.frame!r} is not <building>_f<floor> (e.g. bldg_f2, bldg_fG)"
    if pf[0] not in buildings():
        return f"building {pf[0]!r} has no FMS number in {BUILDINGS}: add a line   {pf[0]}: \"<number>\""
    if not os.path.exists(DEPLOYED):
        return f"no {DEPLOYED}: this robot was not deployed by deploy-all.sh, so the code cannot be named"
    if not parse_deployed(open(DEPLOYED).read())["opendubs_sha"]:
        return f"{DEPLOYED} names no sha"
    while not args.operator:
        args.operator = input("operator (initials or team handle): ").strip()
    while not args.start:
        args.start = input("start landmark (where the robot is now, in words): ").strip()
    return ""


def write_capture(args, n, name: str, out: str, started: datetime.datetime, ended: datetime.datetime) -> None:
    """capture.yaml in the bag directory, with the map's small files copied beside it."""
    import yaml
    key, floor_code = parse_frame(args.frame)
    host = socket.gethostname().split(".")[0]
    for suffix in (".pgm", ".yaml"):
        if os.path.exists(os.path.join(MAPS, name + suffix)):
            shutil.copy2(os.path.join(MAPS, name + suffix), out)
    site = f"site.{name}.yaml"
    if os.path.exists(os.path.join(MAPS, site)):
        shutil.copy2(os.path.join(MAPS, site), out)
    map_files = [file_entry(os.path.join(out, f), rel=f) for f in (name + ".pgm", name + ".yaml", site)
                 if os.path.exists(os.path.join(out, f))]
    map_files += [file_entry(os.path.join(MAPS, name + s), host=host) for s in (".posegraph", ".data")
                  if os.path.exists(os.path.join(MAPS, name + s))]
    if not map_files:
        print("  capture.yaml NOT written: no map file was saved")
        return
    print("  hashing the recording for capture.yaml")
    bag_files = [file_entry(os.path.join(out, f), host=host) for f in sorted(os.listdir(out))
                 if f == "metadata.yaml" or f.endswith(".mcap")]
    duration, topics = parse_bag_info(subprocess.run(["ros2", "bag", "info", out], capture_output=True,
                                                     text=True).stdout)
    recorded = {t["name"]: t["count"] for t in topics}
    cameras = [(f"{kind}_head", [t for t in recorded if f"/{kind}/" in t and t.startswith(RELAY_PREFIX + "/")]
                + [t for t in recorded if (f"/{kind}/" in t and t.endswith("camera_info")) or
                   (kind == "depth" and (t.endswith(("/imu", "/sample")) or t == "/scan_depth"))],
                n.frames.get(kind, "camera_link")) for kind in ("color", "depth")]
    resolution = None
    try:
        resolution = float(yaml.safe_load(open(os.path.join(MAPS, name + ".yaml")))["resolution"])
    except (OSError, KeyError, TypeError, ValueError):
        pass
    robot = {"name": host}
    if os.path.exists(URDF):
        robot["description_sha256"] = sha256(URDF)
    if args.lift_m is not None:
        robot["lift_m"] = args.lift_m
    code = dict(parse_deployed(open(DEPLOYED).read()), slam=SLAM_DESCRIPTION)
    color = "present" if any(recorded.get(t) for t in recorded if "/color/" in t and t.startswith(RELAY_PREFIX + "/")) \
        else "not_recorded"
    doc = capture_manifest(cid=capture_id(started.date(), key, floor_code, name), key=key, fms=buildings()[key],
                           floor_code=floor_code, start=started, end=ended, ntp=ntp_synced(), robot=robot,
                           code=code, operator=args.operator, landmark=args.start, room=args.room,
                           sensors=sensors_block(recorded, cameras), map_name_=name, resolution=resolution,
                           map_files=map_files, bag_files=bag_files, duration_s=duration, topics=topics,
                           color=color, notes=args.notes)
    with open(os.path.join(out, "capture.yaml"), "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
    print(f"  capture.yaml ({doc['id']}): {out}")

def free_gb(path: str) -> float:
    os.makedirs(path, exist_ok=True)
    return shutil.disk_usage(path).free / 1024 ** 3


def dir_bytes(path: str) -> int:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)


def saved_maps() -> set:
    try:
        return {f.rsplit(".", 1)[0] for f in os.listdir(MAPS) if f.endswith((".posegraph", ".pgm"))}
    except FileNotFoundError:
        return set()


class Node:
    """One rclpy node, spun in a thread: the graph, TF, /map, and the camera thinning relays."""

    def __init__(self):
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.signals import SignalHandlerOptions
        from tf2_ros import Buffer, TransformListener
        # rclpy's own SIGINT handler shuts the context down, and the map is saved AFTER the operator's
        # Ctrl-C: leave Ctrl-C to Python (KeyboardInterrupt in the main thread) instead.
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.rclpy = rclpy
        self.node = rclpy.create_node("floor_mapping")
        self.tf = Buffer()
        self._tfl = TransformListener(self.tf, self.node, spin_thread=False)
        self.counts: dict[str, int] = {}
        self.frames: dict[str, str] = {}               # the camera's frame_id per stream, for the manifest
        self._exec = MultiThreadedExecutor()
        self._exec.add_node(self.node)
        threading.Thread(target=self._exec.spin, daemon=True).start()

    def topics(self, settle_s: float = 2.0) -> dict[str, str]:
        time.sleep(settle_s)                         # let discovery fill in
        return {t: ty[0] for t, ty in self.node.get_topic_names_and_types()}

    def pose(self, frame="map", child="base_link"):
        from rclpy.time import Time
        try:
            t = self.tf.lookup_transform(frame, child, Time())
        except Exception:
            return None
        import math
        q = t.transform.rotation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        return (t.transform.translation.x, t.transform.translation.y, yaw)

    def thin(self, label: str, topic: str, typ: str, hz: float) -> str:
        """Republish every message of `topic` that comes at least 1/hz after the last one, as raw bytes
        (nothing is decoded), on RELAY_PREFIX + topic. Returns the new topic."""
        from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from rosidl_runtime_py.utilities import get_message
        cls = get_message(typ)
        out = RELAY_PREFIX + topic
        pub = self.node.create_publisher(cls, out, QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE))
        gap, last = KEEP_FRACTION / hz, [0.0]
        self.counts[label] = 0

        def cb(raw: bytes):
            now = time.monotonic()
            if now - last[0] >= gap:
                last[0] = now
                pub.publish(raw)
                self.counts[label] += 1
                if label not in self.frames:
                    from rclpy.serialization import deserialize_message
                    self.frames[label] = deserialize_message(raw, cls).header.frame_id
        self.node.create_subscription(cls, topic, cb, qos_profile_sensor_data, raw=True,
                                      callback_group=self._own_group())
        return out

    def encode(self, label: str, topic: str, hz: float) -> str:
        """Compress a raw image topic at hz: colour to JPEG (quality 90), 16-bit depth to lossless PNG.
        Only the frames kept are decoded. Without OpenCV, relays the raw images (colour at RAW_COLOR_HZ)."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            hz = RAW_COLOR_HZ if label == "color" else hz
            print(f"WARNING: no OpenCV (python3-opencv): recording {topic} RAW at {hz:g} Hz")
            return self.thin(label, topic, IMAGE_T, hz)
        from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from rclpy.serialization import deserialize_message
        from sensor_msgs.msg import CompressedImage, Image
        out = f"{RELAY_PREFIX}{topic}/" + ("compressed" if label == "color" else "png")
        pub = self.node.create_publisher(CompressedImage, out, QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE))
        gap, last, warned = KEEP_FRACTION / hz, [0.0], set()
        self.counts[label] = 0

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
                pub.publish(CompressedImage(header=m.header, format=fmt, data=buf.tobytes()))
                self.counts[label] += 1
                self.frames.setdefault(label, m.header.frame_id)
        self.node.create_subscription(Image, topic, cb, qos_profile_sensor_data, raw=True,
                                      callback_group=self._own_group())
        return out

    def _own_group(self):
        """Each relay in its own callback group: the executor's threads then run them side by side
        (OpenCV and the middleware release the GIL) instead of one at a time behind TF."""
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
        return MutuallyExclusiveCallbackGroup()

    def grid(self, timeout_s: float = 15.0):
        """The latest /map (slam_toolbox latches it)."""
        from nav_msgs.msg import OccupancyGrid
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        got = []
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        sub = self.node.create_subscription(OccupancyGrid, "/map", got.append, qos)
        end = time.monotonic() + timeout_s
        while not got and time.monotonic() < end:
            time.sleep(0.1)
        self.node.destroy_subscription(sub)
        return got[-1] if got else None

    def serialize(self, base: str, timeout_s: float = 60.0) -> str:
        """slam_toolbox's serialize_map to base(.posegraph/.data): '' on success, else why not."""
        from slam_toolbox.srv import SerializePoseGraph
        cli = self.node.create_client(SerializePoseGraph, "/slam_toolbox/serialize_map")
        try:
            if not cli.wait_for_service(timeout_sec=10.0):
                return "slam_toolbox's serialize_map is not offered (is opendubs-slam running?)"
            fut = cli.call_async(SerializePoseGraph.Request(filename=base))
            end = time.monotonic() + timeout_s
            while not fut.done() and time.monotonic() < end:
                time.sleep(0.1)
            if not fut.done():
                return f"no reply within {timeout_s:.0f} s"
            return "" if fut.result().result == 0 else f"slam_toolbox could not write {base}.posegraph"
        finally:
            self.node.destroy_client(cli)

    def close(self):
        self._exec.shutdown()
        self.rclpy.try_shutdown()


def streams(n: Node, topics: dict, no_color: bool = False) -> list:
    """Every topic the recording takes, starting the camera's thinning relays."""
    record = CORE + OPTIONAL
    cam = camera_topics(topics)
    for kind, t, how in cam["thin"]:
        if not (kind == "color" and no_color):
            record.append(n.thin(kind, t, topics[t], CAM_HZ) if how == "pass" else n.encode(kind, t, CAM_HZ))
    return record + cam["whole"]


def bag(out: str, record: list) -> subprocess.Popen:
    """`ros2 bag record` in its own session (the operator's Ctrl-C reaches this script, which then
    stops the bag cleanly), its chatter in out + '.log' rather than over the progress lines."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    log = open(out + ".log", "w")
    return subprocess.Popen(["ros2", "bag", "record", "-s", "mcap", "--storage-preset-profile", "zstd_fast",
                             "--max-bag-size", str(SPLIT_BYTES), "-o", out] + record,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)            # the bag's own clean stop
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def counts(out: str) -> dict[str, int]:
    """{topic: messages} from `ros2 bag info`."""
    info = subprocess.run(["ros2", "bag", "info", out], capture_output=True, text=True).stdout
    return {t["name"]: t["count"] for t in parse_bag_info(info)[1]}


def slam_mode() -> str:
    if os.path.exists(os.path.join(MAPS, "site.yaml")):
        return "LOCALIZING (site.yaml exists): `floor` refuses; set site.yaml aside to map"
    return "mapping (no site.yaml)"


def check(_args) -> int:
    n = Node()
    try:
        topics = n.topics()
        trial, gb_h = trial_recording(n, topics)
    finally:
        n.close()
    free = free_gb(BAGS)
    print(f"disk: {free:.0f} GB free for {BAGS}; `floor` needs {MIN_FREE_GB + START_MARGIN_GB:.0f} GB to start "
          f"and stops recording at {MIN_FREE_GB:.0f} GB")
    rate, how = (gb_h, "measured by the trial below") if gb_h else (EXPECTED_GB_H, "an estimate, not measured")
    hours = max(free - MIN_FREE_GB, 0) / rate if rate else float("inf")
    print(f"rate: about {rate:.1f} GB an hour ({how}): {hours:.1f} h of recording before the disk floor")
    print(f"SLAM: {slam_mode()}; enabled: {os.path.exists(SLAM_ENABLED)}")
    for t in CORE:
        print(f"  {'ok  ' if t in topics else 'MISSING'} {t}")
    for t in OPTIONAL:
        print(f"  {'ok  ' if t in topics else '(not running)'} {t}")
    cam = camera_topics(topics)
    if cam["thin"]:
        print("camera:")
        for kind, t, how in cam["thin"]:
            print(f"  ok   {t} at {CAM_HZ:g} Hz, " + ("relayed as it is" if how == "pass" else
                  "RAW: compressed here (" + ("JPEG" if kind == "color" else "16-bit PNG") + ")"))
        for t in cam["whole"]:
            print(f"  ok   {t}")
    else:
        print("camera: NO depth-camera topics (.../color/image_raw...): start its driver first, or record without it")
    print("the 6 s trial recording (deleted afterwards) got, per topic:")
    for t in sorted(trial):
        print(f"  {trial[t]:5d}  {t}")
    if not trial:
        print("  NOTHING: `ros2 bag record` did not record (its log is above)")
    for t in sorted(t for t in trial if t.startswith(RELAY_PREFIX + "/")):
        hz = trial[t] / TRIAL_S
        print(f"camera rate achieved: {hz:4.1f} Hz on {t}" + (
            "  WARNING: no frames: its driver lists the topic but sends nothing" if not trial[t] else
            f"  WARNING: below {MIN_CAM_HZ:g} Hz, too slow to judge visual odometry" if hz < MIN_CAM_HZ else ""))
    return 0


def trial_recording(n: Node, topics: dict, seconds: float = TRIAL_S):
    """Record a few seconds: ({topic: messages}, GB an hour), so a recording that would come back
    empty or without a topic is found here, not after a floor's drive, and the disk is sized by a
    measured rate."""
    out = os.path.join(BAGS, f".check-{os.getpid()}")
    proc = bag(out, streams(n, topics))
    try:
        time.sleep(seconds)
    finally:
        stop(proc)
    got = counts(out)
    gb_h = dir_bytes(out) / 1024 ** 3 / seconds * 3600 if got else 0.0
    if not got:
        print(open(out + ".log").read()[-1500:])
    shutil.rmtree(out, ignore_errors=True)
    os.remove(out + ".log")
    return got, gb_h


def save(frame: str, node: Node, start_pose=None, name: str | None = None) -> int:
    name = name or map_name(frame, saved_maps(), datetime.date.today())
    base = os.path.join(MAPS, name)
    print(f"saving the map as {name} in {MAPS}")
    why = node.serialize(base)                 # a failure here must not stop the image below
    if why or not os.path.exists(base + ".posegraph"):
        print(f"  NO pose graph: {why or 'serialize_map replied but wrote no file'}")
    else:
        print(f"  {name}.posegraph, .data")
    g = node.grid()
    if g is None:
        print("  no /map within 15 s: the .pgm was NOT written (the pose graph still holds the map)")
        return 1
    with open(base + ".pgm", "wb") as f:
        f.write(pgm_bytes(g.info.width, g.info.height, g.data))
    with open(base + ".yaml", "w") as f:
        f.write(map_yaml(name + ".pgm", g.info.resolution,
                         (g.info.origin.position.x, g.info.origin.position.y)))
    print(f"  {name}.pgm, .yaml ({g.info.width} x {g.info.height} at {g.info.resolution:.2f} m)")
    sp = start_pose or (0.0, 0.0, 0.0)
    with open(os.path.join(MAPS, f"site.{name}.yaml"), "w") as f:
        f.write(f"# {name}: NOT ACTIVE (SLAM reads site.yaml). floorplan.py fit writes the full site file.\n"
                f"map: {name}\nstart_pose: [{sp[0]:.3f}, {sp[1]:.3f}, {sp[2]:.4f}]"
                f"{'  # NOT MEASURED: the start was not seen' if start_pose is None else ''}\n")
    print(f"  site.{name}.yaml (start pose {sp[0]:.3f} {sp[1]:.3f} {sp[2]:.4f}"
          f"{', NOT MEASURED: give fit the real start' if start_pose is None else ''}; not active)")
    return 0


def floor(args) -> int:
    if os.path.exists(os.path.join(MAPS, "site.yaml")):
        print(f"{MAPS}/site.yaml exists, so SLAM would localize on it rather than map. Set it aside first.")
        return 1
    if not os.path.exists(SLAM_ENABLED):
        print(f"SLAM is off on this robot ({SLAM_ENABLED} does not exist)")
        return 1
    free = free_gb(BAGS)
    if free < MIN_FREE_GB + START_MARGIN_GB:
        print(f"only {free:.0f} GB free for {BAGS}; need {MIN_FREE_GB + START_MARGIN_GB:.0f}. Move old bags off first.")
        return 1
    why = manifest_inputs(args)
    if why:
        print(f"cannot write the capture manifest: {why}")
        return 1
    name = map_name(args.frame, saved_maps(), datetime.date.today())
    print(f"floor {args.frame}: map {name}. KEEP THE ROBOT STILL until it says RECORDING.")
    print(f"restarting {SLAM_UNIT} for a fresh map")
    if subprocess.run(["sudo", "systemctl", "restart", SLAM_UNIT]).returncode:
        return 1
    n = Node()
    proc = None
    try:
        start = None
        end = time.monotonic() + 60
        while start is None and time.monotonic() < end:
            time.sleep(1)
            start = n.pose()
        if start is None:
            print("SLAM gave no map -> base_link within 60 s: is /scan up? (`floor-mapping.sh check`)")
            return 1
        print(f"start pose in the new map: {start[0]:.3f} {start[1]:.3f} {start[2]:.4f}")
        topics = n.topics(0.5)
        missing = [t for t in NEEDED if t not in topics]
        if missing:
            print(f"WARNING: not publishing: {' '.join(missing)}")
        if not camera_topics(topics)["thin"]:
            print("WARNING: no depth camera running: recording without it")
        os.makedirs(BAGS, exist_ok=True)
        out = os.path.join(BAGS, name)
        proc = bag(out, streams(n, topics, args.no_color))
        started = datetime.datetime.now().astimezone()
        t0 = time.monotonic()
        print(f"RECORDING to {out}. Drive the floor; press Ctrl-C ONCE when done.")
        stopped_for_disk = False
        while proc.poll() is None:
            time.sleep(30)
            size, mins = dir_bytes(out) / 1024 ** 3, (time.monotonic() - t0) / 60
            free = free_gb(BAGS)
            rate = size / mins * 60 if mins else 0
            left = max(free - MIN_FREE_GB, 0) / rate * 60 if rate else float("inf")
            cams = " ".join(f"{k}={c}" for k, c in n.counts.items())
            print(f"  {mins:5.1f} min  {size:5.2f} GB  {rate:4.1f} GB/h  {free:.0f} GB free, ~{left:.0f} min left  {cams}")
            if free <= MIN_FREE_GB:
                print(f"DISK at {free:.0f} GB free: stopping the recording now")
                stopped_for_disk = True
                break
        if stopped_for_disk:
            raise KeyboardInterrupt
        print("the recording stopped by itself: saving the map")
    except KeyboardInterrupt:
        pass
    finally:
        if proc:
            stop(proc)
    ended = datetime.datetime.now().astimezone()
    if proc:
        out = os.path.join(BAGS, name)
        got = counts(out)
        print(f"recording closed: {dir_bytes(out) / 1024 ** 3:.2f} GB in {out}; messages: "
              + ", ".join(f"{t} {c}" for t, c in sorted(got.items())))
    try:
        rc = save(args.frame, n, start, name)
        if proc:
            write_capture(args, n, name, os.path.join(BAGS, name), started, ended)
        return rc
    finally:
        n.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("check", help="free disk, live topics, SLAM's mode")
    f = sp.add_parser("floor", help="fresh SLAM map + recording; Ctrl-C once to stop and save")
    f.add_argument("frame", help="the floor's frame, e.g. bldg_f1: the map is saved as FRAME-<date>")
    f.add_argument("--no-color", action="store_true", help="leave the camera's colour frames out")
    f.add_argument("--operator", help="who drives (initials or team handle); asked if not given")
    f.add_argument("--start", help="the start landmark in words; asked if not given")
    f.add_argument("--room", help="the room number nearest the start, if known")
    f.add_argument("--lift-m", type=float, help="the lift's height during the drive, if fixed")
    f.add_argument("--notes", help="anything the next person needs to know about the drive")
    s = sp.add_parser("save", help="save SLAM's current map only")
    s.add_argument("frame")
    args = ap.parse_args()
    if args.cmd == "check":
        return check(args)
    if args.cmd == "floor":
        return floor(args)
    n = Node()
    try:
        return save(args.frame, n)
    finally:
        n.close()


if __name__ == "__main__":
    sys.exit(main())
