#!/usr/bin/env python3
"""Map one floor of a building, attended: a fresh SLAM map, a recording of the drive, and the saved map.

Robot-agnostic through a capture profile, capture_robots/<robot>.yaml, chosen with
OPENDUBS_CAPTURE_ROBOT=<robot or path> (default opendubs2, which is everything said below): the topics,
extra bag parts, SLAM (or none: no restart, no map), the camera's topic patterns, the sensors, the URDF,
the frames and the keyframe and burst settings. Another robot (Spot) gives its own file; whatever it
leaves out comes from opendubs2.yaml.

Run at the robot by the person driving it, through floor-mapping.sh (which sources ROS and the robot's
DDS profile). One floor is one command:

  floor-mapping.sh check                 free disk, which topics are live, SLAM's mode, and a 6 s
                                         trial recording's message counts: run it first
  floor-mapping.sh floor FRAME           e.g. bldg_f1. Restarts SLAM as a fresh map, records the drive,
                                         and when you press Ctrl-C ONCE, stops the recording and saves
                                         the map as FRAME-<date>
  floor-mapping.sh save FRAME            save SLAM's current map only (if `floor` was cut short)
  floor-mapping.sh derive CAPTURE        the profile's derive steps again (they also run after each stop)

Nothing here drives the robot: the operator drives it by RC, as for any mapping drive. Live SLAM runs
as it always does (opendubs-slam, slam_toolbox on the front scan); `floor` only restarts it, so each
floor starts from an empty map, which is why nothing has to track the robot between floors.

THE RECORDING is for rebuilding the map offline, with or without the dead-wheel odometry and with both
LIDARs. It is three ros2 bags (mcap, zstd chunks, split every 2 GB) in one capture directory, started
and stopped together, so the small part can be moved first:
  light/          everything below but the camera's images: small enough to upload over the robot's Wi-Fi
  heavy/          the camera's colour and depth KEYFRAMES only (the robot's profile: a frame when the robot
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
  the depth camera, if its driver is running: colour and depth republished as /mapping/key/<topic>
      (heavy/) and, at the burst rate, /mapping/burst/<topic> (heavy/bursts/), by capture_camera.py (the
      robot-agnostic keyframe and burst relays), and its camera_info and IMU
      at full rate (light/). Its compressed transports are passed through untouched;
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

import capture_camera as cc
from capture_camera import COMPRESSED_T, IMAGE_T, camera_topics as _camera_topics, keyframe_due  # noqa: F401

MAPS = os.environ.get("OPENDUBS_MAPS", "/var/lib/opendubs/maps")
BAGS = os.environ.get("OPENDUBS_BAG_DIR", os.path.expanduser("~/bags"))
MIN_FREE_GB = float(os.environ.get("OPENDUBS_BAG_MIN_FREE_GB", "20"))
START_MARGIN_GB = 10.0
TRIAL_S = 6.0                            # check's trial recording
MIN_CAM_FRACTION = 0.8                   # check warns when the burst feed comes out below this share of its hz
EXPECTED_GB_H = 2.0                      # an estimate: light/ ~0.15 GB/h, keyframes ~1 GB/h, plus ~0.1-0.2 GB a mark
SPLIT_BYTES = 2 * 1024 ** 3

RELAY_PREFIX = "/mapping"
DEPLOYED = os.environ.get("OPENDUBS_DEPLOYED", "/opt/opendubs/DEPLOYED")
BUILDINGS = os.path.join(MAPS, "buildings.yaml")
CAPTURE_SCHEMA = "opendubs.capture/1"
_PROFILE = None


def profile() -> dict:
    """This robot's capture profile (capture_robots/<OPENDUBS_CAPTURE_ROBOT>.yaml over opendubs2.yaml): its
    topics, extra bag parts, SLAM, camera patterns, sensors, URDF, keyframe and burst settings."""
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = cc.load_config()
    return _PROFILE


def urdf() -> str:
    return os.environ.get("OPENDUBS_URDF") or profile()["robot"].get("urdf") or ""


def slam_on() -> bool:
    return profile()["slam"].get("kind", "none") != "none"

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
    """The camera's streams, found by the profile's camera.streams patterns (capture_camera.camera_topics);
    camera.exclude (opendubs2: the tablet's cameras) and the relays' own topics are left out."""
    return _camera_topics(topics, profile()["camera"], (RELAY_PREFIX + "/",))


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
    for sn in profile()["sensors"]:
        got = [t for t in sn["topics"] if recorded.get(t)]
        if got:
            out.append({"id": sn["id"], "model": sn["model"], "topics": got, "frame_id": sn["frame_id"],
                        **({"calibration": sn["calibration"]} if sn.get("calibration") else {})})
    for sid, topics, frame in cameras:
        got = [t for t in topics if recorded.get(t)]
        if got:
            out.append({"id": sid, "model": profile()["camera"]["model"], "topics": got, "frame_id": frame,
                        "calibration": "factory"})
    return out


def capture_manifest(*, cid, key, fms, floor_code, start, end, ntp, robot, code, operator, landmark,
                     room, sensors, map_name_, resolution, map_files, bag_files, duration_s, topics,
                     color, notes=None, map_kind=None, topic_map=None) -> dict:
    """The opendubs.capture/1 document, in the schema's key order. No map_files: no `map` (a walk with
    no map, e.g. a robot without SLAM). topic_map: the schema's optional `topics` (which topic carries
    what, where it differs from sitekit's defaults for robot.kind)."""
    doc = {"schema": CAPTURE_SCHEMA, "id": cid, "building": {"key": key, "fms": fms}, "floor": floor_code,
           "time": {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
                    "clock": "ntp" if ntp else "unsynced"},
           "robot": robot, "code": code, "operator": operator, "sensors": sensors,
           "start": {"landmark": landmark, **({"room": room} if room else {})},
           **({"map": {"name": map_name_, **({"kind": map_kind} if map_kind else {}),
                       **({"resolution_m": round(resolution, 4)} if resolution else {}), "files": map_files}}
              if map_files else {}),
           "bag": {"storage": "mcap/zstd_fast", "files": bag_files, "duration_s": round(duration_s, 3),
                   "bytes": sum(f["bytes"] for f in bag_files),
                   "topics": [dict(t, **({"hz": round(t["count"] / duration_s, 2)} if duration_s else {}))
                              for t in topics]},
           "privacy": {"color": color}, **({"topics": topic_map} if topic_map else {})}
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


def pictures(label: str) -> bool:
    """A camera stream that shows people (the profile's camera.pictures): --no-color drops it."""
    return bool(re.search(profile()["camera"].get("pictures") or "$^", label))


def camera_sensors(recorded: dict, labels: dict, frames: dict) -> list:
    """(id, topics, frame_id) per camera stream: its relayed topics, the camera_info beside its source
    (e.g. /camera/color/camera_info for /camera/color/image_raw), and camera.sensor_extra's topics."""
    cam = profile()["camera"]
    out = []
    for label, (src, *relayed) in labels.items():
        home = os.path.dirname(re.sub(r"/(compressed|compressedDepth)$", "", src)) + "/"
        extra = (cam.get("sensor_extra") or {}).get(label) or []
        topics = relayed + [t for t in recorded if (t.startswith(home) and t.endswith("camera_info"))
                            or any(re.search(x, t) for x in extra)]
        out.append((cam.get("sensor_id", "{label}_head").format(label=label), topics, frames.get(label, "camera_link")))
    return out


def capture_files(out: str, host: str):
    """(bag files, duration, topics) of a capture: every bag's metadata.yaml and .mcap files (light/, the
    parts, heavy/, heavy/bursts/, the derived bags), marks.txt and the [ODO] log, by sha256."""
    bag_files = [file_entry(os.path.join(d, f), host=host) for b in bags_in(out) for d, _, fs in [next(os.walk(b))]
                 for f in sorted(fs) if f == "metadata.yaml" or f.endswith(".mcap")]
    bag_files += [file_entry(os.path.join(out, f), host=host) for f in sorted(os.listdir(out))
                  if f == "marks.txt" or f.endswith("-odo.txt")]
    duration, topics = 0.0, []
    for b in bags_in(out):                     # light/ first: its duration is the drive's
        d, t = parse_bag_info(subprocess.run(["ros2", "bag", "info", b], capture_output=True, text=True).stdout)
        duration, topics = duration or d, topics + t
    return bag_files, duration, topics


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
    map_name_, map_kind = name, profile()["slam"].get("kind")
    if getattr(args, "map", None):              # a map made elsewhere (Spot's GraphNav): a file or a directory
        m = os.path.abspath(args.map.rstrip("/"))
        paths = [os.path.join(d, f) for d, _, fs in os.walk(m) for f in sorted(fs)] if os.path.isdir(m) else [m]
        map_files = [file_entry(f, host=host) for f in sorted(paths)]
        map_name_, map_kind = os.path.basename(m), args.map_kind
    if slam_on() and not map_files and not getattr(args, "map", None):
        print("  capture.yaml NOT written: no map file was saved")
        return
    if map_kind not in ("slam_toolbox", "graphnav", "other"):
        map_kind = "other" if map_files else None
    print("  hashing the recording for capture.yaml")
    bag_files, duration, topics = capture_files(out, host)
    recorded = {t["name"]: t["count"] for t in topics}
    cameras = camera_sensors(recorded, n.relays.labels, n.frames)
    resolution = None
    try:
        resolution = float(yaml.safe_load(open(os.path.join(MAPS, name + ".yaml")))["resolution"])
    except (OSError, KeyError, TypeError, ValueError):
        pass
    robot = {"name": host, **({"kind": profile()["robot"]["kind"]} if profile()["robot"].get("kind") else {})}
    if urdf() and os.path.exists(urdf()):
        robot["description_sha256"] = sha256(urdf())
    if args.lift_m is not None:
        robot["lift_m"] = args.lift_m
    code = dict(parse_deployed(open(DEPLOYED).read()), **({"slam": profile()["slam"]["description"]}
                                                          if profile()["slam"].get("description") else {}))
    color = "present" if any(recorded.get(t) for lb, (_, *outs) in n.relays.labels.items() if pictures(lb)
                             for t in outs) else "not_recorded"
    doc = capture_manifest(cid=capture_id(started.date(), key, floor_code, name), key=key, fms=buildings()[key],
                           floor_code=floor_code, start=started, end=ended, ntp=ntp_synced(), robot=robot,
                           code=code, operator=args.operator, landmark=args.start, room=args.room,
                           sensors=sensors_block(recorded, cameras), map_name_=map_name_, resolution=resolution,
                           map_files=map_files, map_kind=map_kind, topic_map=profile().get("manifest_topics"),
                           bag_files=bag_files, duration_s=duration, topics=topics,
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
        self.cam = cc.load_config()
        self.relays = cc.Relays(self.node, self.cam)   # the camera's keyframe and burst outputs
        self.counts, self.kept, self.frames = self.relays.counts, self.relays.kept, self.relays.frames
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
    """The topics of each recording, starting the camera's relays: {"light", "heavy", "burst": [topics],
    "parts": {name: [topics]}} (the profile's extra bags, e.g. Spot's lidar/)."""
    cam = camera_topics(topics)
    tp, parts = profile()["topics"], profile()["parts"]
    in_parts = {t for ts in parts.values() for t in ts}
    rec = {"light": [t for t in tp["core"] + tp.get("optional", []) + cam["whole"] if t not in in_parts],
           "heavy": [], "burst": [], "parts": {k: list(v) for k, v in parts.items()}}
    for kind, t, how in cam["thin"]:
        if not (pictures(kind) and no_color):
            key, burst = n.relays.add(kind, t, how, topics[t])
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
        for part, topics in rec.get("parts", {}).items():
            self.procs[part] = bag(os.path.join(out, part), topics)
        if rec["heavy"]:
            heavy = os.path.join(out, "heavy")
            self.procs["heavy"] = bag(heavy, rec["heavy"])
            if bursts:
                cache = cc.cache_bytes(n.relays)     # also gives rosbag2 time to create heavy/ (bursts/ sits inside it)
                end = time.monotonic() + 15
                while not os.path.isdir(heavy) and time.monotonic() < end:
                    time.sleep(0.2)
                self.procs["bursts"] = bag(os.path.join(heavy, "bursts"), rec["burst"],
                                           ["--snapshot-mode", "--max-cache-size", str(cache),
                                            "--node-name", n.cam["burst"]["recorder_node"]],
                                           log=os.path.join(out, "bursts.log"))
        self.trigger = cc.BurstTrigger(n.node, os.path.join(out, "marks.txt"), n.cam) if "bursts" in self.procs else None

    @property
    def burst_count(self) -> int:
        return self.trigger.count if self.trigger else 0

    def running(self) -> bool:
        return all(p.poll() is None for p in self.procs.values())

    def stop(self):
        """Write any burst still waiting for its after_s (shorter after the mark), then stop every bag."""
        if self.trigger:
            self.trigger.stop()
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
    """The recordings in a capture directory: light/, the profile's parts, heavy/, heavy/bursts/, or OUT
    itself (before 2026-11-12)."""
    derived = [d["into"].strip("/") for d in (profile().get("derive") or {}).values()]
    found = [os.path.join(out, d) for d in ("light", *profile()["parts"], "heavy", "heavy/bursts", *derived)
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
    print(f"profile: {profile()['_path']}")
    sl = profile()["slam"]
    print(f"SLAM: {slam_mode()}; enabled: {os.path.exists(sl['enabled_flag'])}" if slam_on() else "SLAM: none (this robot's profile)")
    for t in profile()["topics"]["core"]:
        print(f"  {'ok  ' if t in topics else 'MISSING'} {t}")
    for t in profile()["topics"].get("optional", []) + [t for ts in profile()["parts"].values() for t in ts]:
        print(f"  {'ok  ' if t in topics else '(not running)'} {t}")
    cam = camera_topics(topics)
    if cam["thin"]:
        print("camera:")
        for kind, t, how in cam["thin"]:
            print(f"  ok   {t}: keyframes, and {burst_hz:g} Hz bursts, " + ("relayed as it is" if how == "pass" else
                  "RAW: compressed here (" + ("JPEG" if how == "jpeg" else "16-bit PNG") + ")"))
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
    sl = profile()["slam"]
    if slam_on() and os.path.exists(os.path.join(MAPS, "site.yaml")):
        print(f"{MAPS}/site.yaml exists, so SLAM would localize on it rather than map. Set it aside first.")
        return 1
    if slam_on() and not os.path.exists(sl["enabled_flag"]):
        print(f"SLAM is off on this robot ({sl['enabled_flag']} does not exist)")
        return 1
    free = free_gb(BAGS)
    if free < MIN_FREE_GB + START_MARGIN_GB:
        print(f"only {free:.0f} GB free for {BAGS}; need {MIN_FREE_GB + START_MARGIN_GB:.0f}. Move old bags off first.")
        return 1
    why = manifest_inputs(args)
    if why:
        print(f"cannot write the capture manifest: {why}")
        return 1
    taken = saved_maps() | (set(os.listdir(BAGS)) if os.path.isdir(BAGS) else set())   # never reuse a capture
    name = map_name(args.frame, taken, datetime.date.today())
    print(f"floor {args.frame}: map {name}. KEEP THE ROBOT STILL until it says RECORDING.")
    if slam_on():
        print(f"restarting {sl['unit']} for a fresh map")
        if subprocess.run(["sudo", "systemctl", "restart", sl["unit"]]).returncode:
            return 1
    n = Node()
    rec = None
    try:
        start = None
        r = profile()["robot"]
        end = time.monotonic() + 60
        while slam_on() and start is None and time.monotonic() < end:
            time.sleep(1)
            start = n.pose(r["map_frame"], r["base_frame"])
        if slam_on() and start is None:
            print(f"SLAM gave no {r['map_frame']} -> {r['base_frame']} within 60 s: is /scan up? (`floor-mapping.sh check`)")
            return 1
        if start:
            print(f"start pose in the new map: {start[0]:.3f} {start[1]:.3f} {start[2]:.4f}")
        topics = n.topics(0.5)
        missing = [t for t in profile()["topics"]["needed"] if t not in topics]
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
        rc = save(args.frame, n, start, name) if slam_on() else 0
        if rec:
            write_capture(args, n, name, os.path.join(BAGS, name), started, ended)
            if profile().get("derive"):
                rc = rc or derive(os.path.join(BAGS, name))
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
        missing = [t for t in profile()["topics"]["needed"] if t not in topics]
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
    return derive(out) if profile().get("derive") else 0


def derive(out: str, only: str | None = None) -> int:
    """The profile's post-stop derive steps (Spot: the Ouster's packets -> /ouster/points). Each replays
    one part of the capture ON THE BAG'S CLOCK (`ros2 bag play --clock`) into its `launch` command, which
    must take use_sim_time:=true, and records `record` into `into` with --use-sim-time, so what it
    derives carries the walk's stamps. It runs on its own ROS domain (derive_domain_id, local only), so
    the replay never reaches the live robot. Then capture.yaml's bag block is rebuilt with the new files."""
    steps = {k: v for k, v in (profile().get("derive") or {}).items() if not only or k == only}
    if not steps:
        print("no derive step in this robot's profile" + (f" named {only}" if only else ""))
        return 0 if not only else 1
    env = dict(os.environ, ROS_DOMAIN_ID=str(int(profile().get("derive_domain_id", 87))),
               ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST")
    rc = 0
    for name, st in steps.items():
        src, dst = os.path.join(out, st["from_part"]), os.path.join(out, st["into"].strip("/"))
        if not os.path.exists(os.path.join(src, "metadata.yaml")):
            print(f"derive {name}: no {src}/metadata.yaml: skipped")
            rc = 1
            continue
        if os.path.exists(dst):
            print(f"derive {name}: {dst} exists: skipped (remove it to derive again)")
            continue
        print(f"derive {name}: {st['from_part']}/ -> {st['into']} ({' '.join(st['record'])}), on the bag's clock")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        log = open(os.path.join(out, f"derive-{name}.log"), "w")
        drv = subprocess.Popen(st["launch"], shell=True, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(float(st.get("settle_s", 5)))
        recp = subprocess.Popen(["ros2", "bag", "record", "-s", "mcap", "--storage-preset-profile", "zstd_fast",
                                 "--use-sim-time", "-o", dst] + st["record"], env=env, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(3)
        play = subprocess.run(["ros2", "bag", "play", src, "--clock", "-r", str(st.get("rate", 1.0))],
                              env=env, stdout=log, stderr=subprocess.STDOUT)
        time.sleep(float(st.get("drain_s", 3)))
        for p in (recp, drv):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGINT)
        for p in (recp, drv):
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        got = counts(dst) if os.path.exists(os.path.join(dst, "metadata.yaml")) else {}
        print(f"  {name}: play rc {play.returncode}; recorded " + (", ".join(f"{t} {c}" for t, c in got.items()) or "NOTHING"))
        rc = rc or (0 if any(got.values()) else 1)
    refresh_capture(out)
    return rc


def refresh_capture(out: str) -> None:
    """Rebuild capture.yaml's bag block from what is in the capture now, and re-validate it against the
    schema (tests/capture.schema.json, when jsonschema is installed)."""
    import yaml
    path = os.path.join(out, "capture.yaml")
    if not os.path.exists(path):
        print("  no capture.yaml to update")
        return
    doc = yaml.safe_load(open(path))
    print("  hashing the recording for capture.yaml")
    files, duration, topics = capture_files(out, socket.gethostname().split(".")[0])
    duration = duration or doc["bag"].get("duration_s", 0.0)
    doc["bag"] = dict(doc["bag"], files=files, bytes=sum(f["bytes"] for f in files), duration_s=round(duration, 3),
                      topics=[dict(t, **({"hz": round(t["count"] / duration, 2)} if duration else {})) for t in topics])
    try:
        import json
        import jsonschema
        schema = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "capture.schema.json")
        jsonschema.validate(doc, json.load(open(schema)))
        how = "valid"
    except ImportError:
        how = "NOT validated: no jsonschema here"
    with open(path, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
    print(f"  capture.yaml updated ({len(files)} files, {how})")


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
    f.add_argument("--map", help="a map made elsewhere to list in capture.yaml (a file or a directory, e.g. Spot's GraphNav map)")
    f.add_argument("--map-kind", default="graphnav", choices=["graphnav", "slam_toolbox", "other"],
                   help="what made --map (default graphnav)")
    g = sp.add_parser("segment", help="a recording with no map (outdoors, ramps); Ctrl-C once to stop")
    g.add_argument("name", help="e.g. bldg_ext: recorded as NAME-<date>")
    g.add_argument("--no-color", action="store_true", help="leave the camera's colour frames out")
    g.add_argument("--attach", action="append", help="a file to move into the capture at the end (the [ODO] log)")
    d = sp.add_parser("derive", help="run the profile's derive steps on a capture again (e.g. Spot's point clouds)")
    d.add_argument("capture", help="the capture directory")
    d.add_argument("--only", help="one derive step by name")
    s = sp.add_parser("save", help="save SLAM's current map only")
    s.add_argument("frame")
    args = ap.parse_args()
    if args.cmd == "check":
        return check(args)
    if args.cmd == "floor":
        return floor(args)
    if args.cmd == "segment":
        return segment(args)
    if args.cmd == "derive":
        return derive(args.capture.rstrip("/"), args.only)
    n = Node()
    try:
        return save(args.frame, n)
    finally:
        n.close()


if __name__ == "__main__":
    sys.exit(main())
