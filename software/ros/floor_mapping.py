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

THE RECORDING is for rebuilding the map offline, with or without the dead-wheel odometry and with both
LIDARs. It is three ros2 bags (mcap, zstd chunks, split every 2 GB) in one capture directory, started
and stopped together, so the small part can be moved first:
  light/          everything below but the camera's images: small enough to upload over the robot's Wi-Fi
  heavy/          the camera's colour and depth KEYFRAMES only (capture_camera.yaml: a frame when the robot
                  has moved 0.5 m or turned 10 degrees, or the head 5 degrees, and one every 2 s regardless)
  heavy/bursts/   the camera at full rate around each mark: mark.sh writes marks.txt, and about 20 s later
                  the last ~40 s of frames (a rolling buffer, rosbag2's snapshot mode) go to
                  bursts_<n>.mcap, the n-th mark's burst
  marks.txt, the [ODO] log (--attach) and each recorder's .log sit at the capture's root.
It holds:
  /scan /scan_rear /scan_filtered        both LIDARs raw, and the front one as SLAM sees it
  /scan_rear_filtered /scan_depth        when something publishes them (the rear body filter, the depth
                                         camera flattened to a scan): recorded if present, never needed
  /odom /odom/raw                        the Pinpoint pose, and its [ODO] line verbatim (raw pod counts,
                                         status bits, failed reads), published by drive_bridge.py
  /tf /tf_static                         including SLAM's map -> odom
  /head/joint_states, _targets, _limits  the neck's pan and tilt (the dashboard's neck_state.py)
  /opendubs/joint_states                 what robot_state_publisher was told the lift and neck were, so
                                         the camera's TF in the bag can be checked against the real neck
  the depth camera, if its driver is running: colour and depth republished as KEYFRAME_PREFIX/<topic>
      (heavy/) and, at the burst rate, BURST_PREFIX/<topic> (heavy/bursts/), and its camera_info and IMU at
      full rate (light/). Its compressed transports are passed through untouched;
      if it publishes only raw images, the relay compresses them itself (colour JPEG on
      <prefix><topic>/compressed; depth lossless 16-bit PNG on <prefix><topic>/png, format
      "16UC1; png", decoded with cv2.imdecode(..., IMREAD_UNCHANGED)). Without OpenCV the raw images
      are recorded instead, colour at RAW_COLOR_HZ. The camera's topics are found by name
      (.../color/image_raw...), so any driver namespace works; with no camera, the recording goes on
      without it and says so. OPENDUBS_COLOR_FROM_RAW=1 skips a listed compressed colour stream and JPEGs
      the raw one instead: the Femto's driver advertises /camera/color/image_raw/compressed but sends
      nothing on it, which `check` shows as 0 Hz.
  NOT /map: it is the size of the floor every 2 s. The map is saved separately, at the end.

Colour frames are pictures of whoever is in the corridor. The bag stays on the robot and on the
project's own machines, never in the repo or anywhere public; --no-color leaves colour out.

THE DISK: `check` measures light/ and heavy/'s rate from its trial, with the robot still (EXPECTED_GB_H is
only the estimate it falls back on). Sized on 2026-11-11's drives: light/ ~0.12 GB an hour, keyframes
~0.5-0.7 GB an hour, and ~0.1 GB a mark. `floor` refuses to start with less than MIN_FREE_GB + START_MARGIN_GB free, and stops the
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
schemas/capture.schema.json; a copy is in tests/ and the tests validate against it) inside the capture
directory, and copies the map's .pgm, .yaml and site file beside it, so that directory is the capture
to ingest. It names the building and floor (parsed from FRAME, <building>_f<floor>: bldg_f2 -> floor
02, bldg_fG -> 0G; the building's FMS number from MAPS/buildings.yaml, {key: "number"}, site data the
building-model lane owns), the times and whether the clock was NTP-synced, the robot, its URDF's
sha256, the code /opt/opendubs/DEPLOYED says was deployed, the operator and the start landmark
(--operator, --start, or asked at the start), the sensors, the map, and every file of the capture by path,
sha256 and size: light/, heavy/ and heavy/bursts/ (the path says which), marks.txt and the logs. Hashing a long drive's bag takes a minute or so. `floor` checks all of it can be
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
RAW_COLOR_HZ = 3.0                       # raw colour when nothing can compress it: 2.7 MB a frame
TRIAL_S = 6.0                            # check's trial recording
MIN_CAM_FRACTION = 0.8                   # check warns when the burst feed comes out below this share of its hz
KEEP_FRACTION = 0.8                     # keep a frame 0.8 of a period after the last: 30 Hz in -> 15 out, despite jitter
EXPECTED_GB_H = 2.0                      # an estimate: light/ ~0.15 GB/h, keyframes ~1 GB/h, plus ~0.1-0.2 GB a mark
SPLIT_BYTES = 2 * 1024 ** 3
SLAM_UNIT = "opendubs-slam"
SLAM_ENABLED = "/etc/opendubs/slam.enabled"

CORE = ["/scan", "/scan_rear", "/scan_filtered", "/odom", "/odom/raw", "/tf", "/tf_static",
        "/head/joint_states", "/head/joint_targets", "/head/joint_limits"]
OPTIONAL = ["/scan_rear_filtered", "/scan_depth", "/opendubs/joint_states",
            "/tablet/fix", "/tablet/imu", "/power/bus_voltage"]   # recorded when present
NEEDED = ["/scan", "/scan_rear", "/odom", "/tf"]      # without these the drive is not worth recording
RELAY_PREFIX = "/mapping"
KEYFRAME_PREFIX = RELAY_PREFIX + "/key"      # heavy/: the camera's keyframes
BURST_PREFIX = RELAY_PREFIX + "/burst"       # heavy/bursts/: the camera at the burst rate, into the buffer
BURST_NODE = "opendubs_bursts"               # the snapshot recorder; its service is /<node>/snapshot
CAMERA_CONFIG = os.environ.get("OPENDUBS_CAPTURE_CAMERA",
                               os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_camera.yaml"))
CAMERA_DEFAULTS = {"keyframe": {"move_m": 0.5, "turn_deg": 10.0, "head_deg": 5.0, "floor_s": 2.0},
                   "burst": {"hz": 15.0, "before_s": 20.0, "after_s": 20.0, "cache_mb": 200.0}}
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
        if kind == "color" and os.environ.get("OPENDUBS_COLOR_FROM_RAW"):
            topic = None        # the driver lists a compressed colour stream that sends nothing (Femto, 2026-11-11)
        if not topic:
            topic, how = find(f"/{kind}/image_raw", IMAGE_T), "encode"
        if topic:
            out["thin"].append((kind, topic, how))
            base = base or topic[:topic.index(f"/{kind}/")]
    if base:
        out["whole"] = sorted(t for t, ty in topics.items() if t.startswith(base + "/") and
                              ty in ("sensor_msgs/msg/CameraInfo", "sensor_msgs/msg/Imu"))
    return out


def camera_config(path: str = CAMERA_CONFIG) -> dict:
    """capture_camera.yaml over CAMERA_DEFAULTS, key by key."""
    import yaml
    try:
        got = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        got = {}
    return {sec: {k: float((got.get(sec) or {}).get(k, v)) for k, v in d.items()} for sec, d in CAMERA_DEFAULTS.items()}


def keyframe_due(last, now, cfg: dict) -> bool:
    """last, now: (time s, x, y, yaw, pan, tilt), any of the pose or head None when not heard yet.
    True when no frame was kept yet, floor_s has passed, or the robot or head moved far enough."""
    import math
    if last is None or now[0] - last[0] >= cfg["floor_s"]:
        return True
    if None not in (last[1], now[1]):
        if math.hypot(now[1] - last[1], now[2] - last[2]) >= cfg["move_m"]:
            return True
        turn = (now[3] - last[3] + math.pi) % (2 * math.pi) - math.pi
        if abs(turn) >= math.radians(cfg["turn_deg"]):
            return True
    if None not in (last[4], now[4]):
        if max(abs(now[4] - last[4]), abs(now[5] - last[5])) >= math.radians(cfg["head_deg"]):
            return True
    return False


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
    """capture.yaml in the capture directory, with the map's small files copied beside it."""
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
    bag_files = [file_entry(os.path.join(d, f), host=host) for b in bags_in(out) for d, _, fs in [next(os.walk(b))]
                 for f in sorted(fs) if f == "metadata.yaml" or f.endswith(".mcap")]
    bag_files += [file_entry(os.path.join(out, f), host=host) for f in sorted(os.listdir(out))
                  if f == "marks.txt" or f.endswith("-odo.txt")]
    duration, topics = 0.0, []
    for b in bags_in(out):                     # light/ first: its duration is the drive's
        d, t = parse_bag_info(subprocess.run(["ros2", "bag", "info", b], capture_output=True, text=True).stdout)
        duration, topics = duration or d, topics + t
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
        self.counts: dict[str, int] = {}               # frames into the burst feed, per stream
        self.fed_bytes = 0                             # bytes into the burst feed, all streams
        self.kept: dict[str, int] = {}                 # keyframes kept, per stream
        self.frames: dict[str, str] = {}               # the camera's frame_id per stream, for the manifest
        self.cam = camera_config()
        self.state = {"pose": (None, None, None), "head": (None, None)}   # for the keyframe gate
        self._watch_robot()
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

    def _watch_robot(self):
        """/odom and the head's angles, for the keyframe gate."""
        import math
        from nav_msgs.msg import Odometry
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import JointState

        def odom(m):
            q = m.pose.pose.orientation
            self.state["pose"] = (m.pose.pose.position.x, m.pose.pose.position.y,
                                  math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z)))

        def head(m):
            j = dict(zip(m.name, m.position))
            if "head_pan" in j and "head_tilt" in j:
                self.state["head"] = (j["head_pan"], j["head_tilt"])
        self.node.create_subscription(Odometry, "/odom", odom, qos_profile_sensor_data)
        self.node.create_subscription(JointState, "/head/joint_states", head, 10)

    def _outputs(self, label: str, cls, suffix: str):
        """(burst topic, publish(msg_or_bytes)): every frame offered goes to the burst feed, and to the
        keyframe topic when keyframe_due says so."""
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)
        burst = self.node.create_publisher(cls, BURST_PREFIX + suffix, qos)
        key = self.node.create_publisher(cls, KEYFRAME_PREFIX + suffix, qos)
        last = [None]
        self.counts[label] = self.kept[label] = 0

        def publish(msg):
            burst.publish(msg)
            self.counts[label] += 1
            self.fed_bytes += len(msg) if isinstance(msg, bytes) else len(msg.data)
            now = (time.monotonic(), *self.state["pose"], *self.state["head"])
            if keyframe_due(last[0], now, self.cam["keyframe"]):
                last[0] = now
                key.publish(msg)
                self.kept[label] += 1
        return publish

    def thin(self, label: str, topic: str, typ: str, hz: float) -> list:
        """Relay every message of `topic` that comes at least 1/hz after the last one, as raw bytes
        (nothing is decoded). Returns [keyframe topic, burst topic]."""
        from rclpy.qos import qos_profile_sensor_data
        from rosidl_runtime_py.utilities import get_message
        cls = get_message(typ)
        publish = self._outputs(label, cls, topic)
        gap, last = KEEP_FRACTION / hz, [0.0]

        def cb(raw: bytes):
            now = time.monotonic()
            if now - last[0] >= gap:
                last[0] = now
                publish(raw)
                if label not in self.frames:
                    from rclpy.serialization import deserialize_message
                    self.frames[label] = deserialize_message(raw, cls).header.frame_id
        self.node.create_subscription(cls, topic, cb, qos_profile_sensor_data, raw=True,
                                      callback_group=self._own_group())
        return [KEYFRAME_PREFIX + topic, BURST_PREFIX + topic]

    def encode(self, label: str, topic: str, hz: float) -> list:
        """Compress a raw image topic at hz: colour to JPEG (quality 90), 16-bit depth to lossless PNG.
        Only the frames kept are decoded. Without OpenCV, relays the raw images (colour at RAW_COLOR_HZ).
        Returns [keyframe topic, burst topic]."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            hz = RAW_COLOR_HZ if label == "color" else hz
            print(f"WARNING: no OpenCV (python3-opencv): recording {topic} RAW at {hz:g} Hz")
            return self.thin(label, topic, IMAGE_T, hz)
        from rclpy.qos import qos_profile_sensor_data
        from rclpy.serialization import deserialize_message
        from sensor_msgs.msg import CompressedImage, Image
        suffix = f"{topic}/" + ("compressed" if label == "color" else "png")
        publish = self._outputs(label, CompressedImage, suffix)
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
        self.node.create_subscription(Image, topic, cb, qos_profile_sensor_data, raw=True,
                                      callback_group=self._own_group())
        return [KEYFRAME_PREFIX + suffix, BURST_PREFIX + suffix]

    def snapshot(self, timeout_s: float = 30.0) -> str:
        """Write the burst recorder's buffer out as the next bursts_<n>.mcap: '' on success, else why not."""
        from rosbag2_interfaces.srv import Snapshot
        cli = self.node.create_client(Snapshot, f"/{BURST_NODE}/snapshot")
        try:
            if not cli.wait_for_service(timeout_sec=5.0):
                return "the burst recorder offers no snapshot service"
            fut = cli.call_async(Snapshot.Request())
            end = time.monotonic() + timeout_s
            while not fut.done() and time.monotonic() < end:
                time.sleep(0.1)
            return "" if fut.done() and fut.result().success else "the burst recorder did not confirm"
        finally:
            self.node.destroy_client(cli)

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


def streams(n: Node, topics: dict, no_color: bool = False) -> dict:
    """The topics of each recording, starting the camera's relays: {"light", "heavy", "burst": [topics]}."""
    cam = camera_topics(topics)
    rec = {"light": CORE + OPTIONAL + cam["whole"], "heavy": [], "burst": []}
    hz = n.cam["burst"]["hz"]
    for kind, t, how in cam["thin"]:
        if not (kind == "color" and no_color):
            key, burst = n.thin(kind, t, topics[t], hz) if how == "pass" else n.encode(kind, t, hz)
            rec["heavy"].append(key)
            rec["burst"].append(burst)
    return rec


def bag(out: str, record: list, extra=(), log: str | None = None) -> subprocess.Popen:
    """`ros2 bag record` in its own session (the operator's Ctrl-C reaches this script, which then
    stops the bag cleanly), its chatter in out + '.log' rather than over the progress lines."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    log = open(log or out + ".log", "w")
    return subprocess.Popen(["ros2", "bag", "record", "-s", "mcap", "--storage-preset-profile", "zstd_fast",
                             "--max-bag-size", str(SPLIT_BYTES), *extra, "-o", out] + record,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)            # the bag's own clean stop
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


class Recording:
    """A capture's bags in OUT, started and stopped together: light/, heavy/ and heavy/bursts/ (the last
    two only with a camera, bursts not in `check`'s trial). A new line in OUT/marks.txt (mark.sh) makes a
    burst: after_s later the burst recorder's buffer is written out as the next bursts_<n>.mcap."""

    def __init__(self, n: Node, out: str, rec: dict, bursts: bool = True):
        self.n, self.out, self.procs = n, out, {}
        os.makedirs(out, exist_ok=True)
        self.procs["light"] = bag(os.path.join(out, "light"), rec["light"])
        if rec["heavy"]:
            heavy = os.path.join(out, "heavy")
            self.procs["heavy"] = bag(heavy, rec["heavy"])
            if bursts:
                cache = self._cache_bytes()          # also gives rosbag2 time to create heavy/ (bursts/ sits inside it)
                end = time.monotonic() + 15
                while not os.path.isdir(heavy) and time.monotonic() < end:
                    time.sleep(0.2)
                self.procs["bursts"] = bag(os.path.join(heavy, "bursts"), rec["burst"],
                                           ["--snapshot-mode", "--max-cache-size", str(cache), "--node-name", BURST_NODE],
                                           log=os.path.join(out, "bursts.log"))
        self.marks = os.path.join(out, "marks.txt")
        self.seen = self._lines()
        self.due: list[float] = []
        self.burst_count = 0
        self._stop = threading.Event()
        if "bursts" in self.procs:
            threading.Thread(target=self._watch, daemon=True).start()

    def _cache_bytes(self, seconds: float = 5.0) -> int:
        """The burst buffer, sized by time: the feed's measured bytes a second times before_s + after_s
        (+10%), at most cache_mb. rosbag2's buffer is in bytes, so a fixed size would hold far more
        seconds of a slow camera than of a fast one."""
        b = self.n.cam["burst"]
        f0 = self.n.fed_bytes
        time.sleep(seconds)
        rate = (self.n.fed_bytes - f0) / seconds
        cap = b["cache_mb"] * 1e6
        cache = int(min(cap, rate * (b["before_s"] + b["after_s"]) * 1.1)) if rate else int(cap)
        print(f"burst buffer: {cache / 1e6:.0f} MB (camera feed {rate / 1e6:.2f} MB/s"
              + (", none yet: the cap" if not rate else ", at the cap" if cache == int(cap) else "") + ")")
        return cache

    def _lines(self) -> int:
        try:
            with open(self.marks) as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0

    def _burst(self):
        why = self.n.snapshot()
        print(f"  burst {self.burst_count}: " + (why or "written"))
        self.burst_count += 1

    def _watch(self):
        after = self.n.cam["burst"]["after_s"]
        while not self._stop.wait(1.0):
            k = self._lines()
            self.due += [time.monotonic() + after] * max(k - self.seen, 0)
            self.seen = max(k, self.seen)
            while self.due and self.due[0] <= time.monotonic():
                self.due.pop(0)
                self._burst()

    def running(self) -> bool:
        return all(p.poll() is None for p in self.procs.values())

    def stop(self):
        """Write any burst still waiting for its after_s (shorter after the mark), then stop every bag."""
        self._stop.set()
        self.seen, k = self._lines(), self.seen
        self.due += [0.0] * max(self.seen - k, 0)
        for _ in self.due:
            self._burst()
        self.due = []
        for p in self.procs.values():
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in self.procs.values():
            stop(p)


def counts(out: str) -> dict[str, int]:
    """{topic: messages} from `ros2 bag info`, over the capture's bags."""
    got = {}
    for b in bags_in(out):
        info = subprocess.run(["ros2", "bag", "info", b], capture_output=True, text=True).stdout
        got.update({t["name"]: t["count"] for t in parse_bag_info(info)[1]})
    return got


def bags_in(out: str) -> list:
    """The recordings in a capture directory: light/, heavy/, heavy/bursts/, or OUT itself (before 2026-11-12)."""
    found = [os.path.join(out, d) for d in ("light", "heavy", "heavy/bursts")
             if os.path.exists(os.path.join(out, d, "metadata.yaml"))]
    return found or [out]


def slam_mode() -> str:
    if os.path.exists(os.path.join(MAPS, "site.yaml")):
        return "LOCALIZING (site.yaml exists): `floor` refuses; set site.yaml aside to map"
    return "mapping (no site.yaml)"


def check(_args) -> int:
    n = Node()
    try:
        topics = n.topics()
        trial, gb_h, fed = trial_recording(n, topics)
        burst_hz = n.cam["burst"]["hz"]
    finally:
        n.close()
    free = free_gb(BAGS)
    print(f"disk: {free:.0f} GB free for {BAGS}; `floor` needs {MIN_FREE_GB + START_MARGIN_GB:.0f} GB to start "
          f"and stops recording at {MIN_FREE_GB:.0f} GB")
    rate, how = (sum(gb_h.values()), "measured by the trial below, robot still") if gb_h else (EXPECTED_GB_H, "an estimate, not measured")
    hours = max(free - MIN_FREE_GB, 0) / rate if rate else float("inf")
    print(f"rate: about {rate:.1f} GB an hour ({how}): {hours:.1f} h of recording before the disk floor")
    for k, v in gb_h.items():
        print(f"  {k}/ {v:.2f} GB an hour")
    print("  plus each mark's burst (heavy/bursts/), and more keyframes while the robot or head moves")
    print(f"SLAM: {slam_mode()}; enabled: {os.path.exists(SLAM_ENABLED)}")
    for t in CORE:
        print(f"  {'ok  ' if t in topics else 'MISSING'} {t}")
    for t in OPTIONAL:
        print(f"  {'ok  ' if t in topics else '(not running)'} {t}")
    cam = camera_topics(topics)
    if cam["thin"]:
        print("camera:")
        for kind, t, how in cam["thin"]:
            print(f"  ok   {t}: keyframes, and {burst_hz:g} Hz bursts, " + ("relayed as it is" if how == "pass" else
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
    for kind, c in sorted(fed.items()):
        hz = c / TRIAL_S
        print(f"camera burst feed achieved: {hz:4.1f} Hz for {kind}" + (
            "  WARNING: no frames: its driver lists the topic but sends nothing" if not c else
            f"  WARNING: below {MIN_CAM_FRACTION * burst_hz:g} Hz, short of the burst rate" if hz < MIN_CAM_FRACTION * burst_hz else ""))
    return 0


def trial_recording(n: Node, topics: dict, seconds: float = TRIAL_S):
    """Record a few seconds of light/ and heavy/ (no bursts): ({topic: messages}, {bag: GB an hour},
    {camera stream: frames into the burst feed}), so a recording that would come back empty or without
    a topic is found here, not after a floor's drive, and the disk is sized by a measured rate."""
    out = os.path.join(BAGS, f".check-{os.getpid()}")
    rec = Recording(n, out, streams(n, topics), bursts=False)
    try:
        time.sleep(seconds)
    finally:
        rec.stop()
    got = counts(out)
    gb_h = {d: dir_bytes(os.path.join(out, d)) / 1024 ** 3 / seconds * 3600 for d in ("light", "heavy")
            if os.path.isdir(os.path.join(out, d))} if got else {}
    if not got:
        print(open(os.path.join(out, "light.log")).read()[-1500:])
    shutil.rmtree(out, ignore_errors=True)
    return got, gb_h, dict(n.counts)


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
    rec = None
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
        rec = Recording(n, out, streams(n, topics, args.no_color))
        started = datetime.datetime.now().astimezone()
        t0 = time.monotonic()
        print(f"RECORDING to {out} ({', '.join(rec.procs)}). Drive the floor; press Ctrl-C ONCE when done.")
        stopped_for_disk = False
        while rec.running():
            time.sleep(30)
            size, mins = dir_bytes(out) / 1024 ** 3, (time.monotonic() - t0) / 60
            free = free_gb(BAGS)
            rate = size / mins * 60 if mins else 0
            left = max(free - MIN_FREE_GB, 0) / rate * 60 if rate else float("inf")
            cams = " ".join(f"{k}={n.kept[k]}/{c}" for k, c in n.counts.items()) + f" bursts={rec.burst_count}"
            print(f"  {mins:5.1f} min  {size:5.2f} GB  {rate:4.1f} GB/h  {free:.0f} GB free, ~{left:.0f} min left  {cams}")
            if free <= MIN_FREE_GB:
                print(f"DISK at {free:.0f} GB free: stopping the recording now")
                stopped_for_disk = True
                break
        if stopped_for_disk:
            raise KeyboardInterrupt
        print("a recording stopped by itself: saving the map")
    except KeyboardInterrupt:
        pass
    finally:
        if rec:
            rec.stop()
    ended = datetime.datetime.now().astimezone()
    if rec:
        out = os.path.join(BAGS, name)
        attach(out, args.attach)
        closed(out)
    try:
        rc = save(args.frame, n, start, name)
        if rec:
            write_capture(args, n, name, os.path.join(BAGS, name), started, ended)
        return rc
    finally:
        n.close()


def segment(args) -> int:
    """A recording with no map: outdoors, ramps, a pass-through. Same streams as `floor` (colour as JPEG
    through the relay, not raw); SLAM is left as it is and nothing is saved but the bag."""
    free = free_gb(BAGS)
    if free < MIN_FREE_GB + START_MARGIN_GB:
        print(f"only {free:.0f} GB free for {BAGS}; need {MIN_FREE_GB + START_MARGIN_GB:.0f}. Move old bags off first.")
        return 1
    stem = f"{args.name}-{datetime.date.today():%Y%m%d}"
    name, k = stem, 2
    while os.path.exists(os.path.join(BAGS, name)):
        name, k = f"{stem}-{k}", k + 1
    n = Node()
    rec = None
    try:
        topics = n.topics(2.0)
        missing = [t for t in NEEDED if t not in topics]
        if missing:
            print(f"WARNING: not publishing: {' '.join(missing)}")
        out = os.path.join(BAGS, name)
        rec = Recording(n, out, streams(n, topics, args.no_color))
        t0 = time.monotonic()
        print(f"RECORDING segment to {out} ({', '.join(rec.procs)}). Ctrl-C ONCE (or SIGINT) to stop.")
        while rec.running():
            time.sleep(30)
            size, mins, free = dir_bytes(out) / 1024 ** 3, (time.monotonic() - t0) / 60, free_gb(BAGS)
            print(f"  {mins:5.1f} min  {size:5.2f} GB  {free:.0f} GB free  "
                  + " ".join(f"{k}={n.kept[k]}/{c}" for k, c in n.counts.items()) + f" bursts={rec.burst_count}")
            if free <= MIN_FREE_GB:
                print(f"DISK at {free:.0f} GB free: stopping the recording now")
                break
    except KeyboardInterrupt:
        pass
    finally:
        if rec:
            rec.stop()
        n.close()
    out = os.path.join(BAGS, name)
    attach(out, args.attach)
    closed(out)
    return 0


def attach(out: str, paths) -> None:
    """Move each --attach file (the [ODO] log) into the capture's root, once its writer has stopped."""
    for p in paths or []:
        if os.path.exists(p):
            shutil.move(p, os.path.join(out, os.path.basename(p)))
        else:
            print(f"  --attach {p}: not found")


def closed(out: str) -> None:
    for b in bags_in(out):
        got = counts(b)
        size = sum(os.path.getsize(os.path.join(b, f)) for f in os.listdir(b) if os.path.isfile(os.path.join(b, f)))
        print(f"recording closed: {os.path.relpath(b, BAGS)} {size / 1024 ** 3:.2f} GB; messages: "
              + ", ".join(f"{t} {c}" for t, c in sorted(got.items())))


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
    f.add_argument("--attach", action="append", help="a file to move into the capture at the end (the [ODO] log)")
    g = sp.add_parser("segment", help="a recording with no map (outdoors, ramps); Ctrl-C once to stop")
    g.add_argument("name", help="e.g. bldg_ext: recorded as NAME-<date>")
    g.add_argument("--no-color", action="store_true", help="leave the camera's colour frames out")
    g.add_argument("--attach", action="append", help="a file to move into the capture at the end (the [ODO] log)")
    s = sp.add_parser("save", help="save SLAM's current map only")
    s.add_argument("frame")
    args = ap.parse_args()
    if args.cmd == "check":
        return check(args)
    if args.cmd == "floor":
        return floor(args)
    if args.cmd == "segment":
        return segment(args)
    n = Node()
    try:
        return save(args.frame, n)
    finally:
        n.close()


if __name__ == "__main__":
    sys.exit(main())
