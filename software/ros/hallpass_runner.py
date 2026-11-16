#!/usr/bin/env python3
"""hallpass_runner.py: run a HallPass capture mission (sitekit.navgoals/1) stop by stop. Run by
hallpass-runner.sh (opendubs-hallpass-runner.service, off unless /etc/opendubs/hallpass.enabled exists).

THIS MOVES THE ROBOT, only through things that keep their own gates:
  - the base: Nav2's navigate_to_pose (opendubs-nav2), which drives only through the base lease, only in
    AUTO with a person holding the deadman, under the speed cap and the people guard;
  - the head: the dashboard's look-at (POST /api/head/look_at), which keeps the neck's gates.
Nothing here writes a velocity, a GAZE or a relay.

A MISSION is a navgoals file in MISSIONS (<id>.navgoals.yaml, written by HallPass's sitekit.capture.navgoals
on the robot): poses in a floor frame (frame_id), each with dwell_s, a mark and targets
[{site, kind, xyz (floor frame), size_m}]. The floor frame reaches `map` through TF (<floor> -> map, from
site.yaml), so Nav2 takes the poses as they are. For each stop:
  1. drive to the pose (navigate_to_pose, in the floor frame), then wait dwell_s;
  2. for each target with xyz: look-at, transformed to base_link here; then one colour frame (and depth, if
     aligned depth is configured) with K from camera_info and T_floor_camera from TF at the frame's stamp;
  3. HallPass's stop check (STOPCHECK, a JSON stdin/stdout command): ok | closer | reaim | retake | skip;
  4. act on the verdict at most once (reaim: look-at again offset by its az/el; closer: one goal to its
     suggested pose; retake: wait its wait_s) and check again with attempt 1. Anything but ok goes on the
     redrive list.
A goal that fails is logged and the stop skipped. Letting go of the deadman PAUSES: the current goal is
cancelled and sent again when it is held again. Everything is logged to SESSIONS/<session>/mission.jsonl;
the stop check writes its crops and attempts there too.

Topics:
  /hallpass/mission/cmd     std_msgs/String JSON, in: {"op": "start", "mission"} | pause | resume | skip
                            (the dashboard's mission panel, mission.py)
  /hallpass/mission/state   std_msgs/String JSON, out, latched, 2 Hz and on change (STATE below)
  /hallpass/runner/plan     std_msgs/String JSON, out, latched: the mission being run, as read
  /hallpass/runner/pose     geometry_msgs/PoseStamped, out, 5 Hz: base_link in the floor frame
  /hallpass/stopcheck/result std_msgs/String JSON, out: each stop check's answer, with site and attempt
  /robot/rc                 std_msgs/String JSON, in: {"ch5", "deadman"} (stamplc_bridge.py). Unknown = released

Settings (environment): OPENDUBS_HALLPASS_DIR (/var/lib/opendubs/hallpass: missions/ and the sessions),
OPENDUBS_DASHBOARD (http://127.0.0.1:8080), OPENDUBS_STOPCHECK (the command, default
"python3 -m sitekit.capture.stopcheck") run in OPENDUBS_SITEKIT (the opendubs-site checkout),
OPENDUBS_CAMERA_IMAGE (/camera/color/image_raw), OPENDUBS_CAMERA_INFO (/camera/color/camera_info),
OPENDUBS_CAMERA_DEPTH (/camera/depth/image_raw: the Femto's depth is registered to colour, frame
camera_color_optical_frame, 1280x720; empty = none),
OPENDUBS_HALLPASS_ROBOT (opendubs2: whose head calibration HallPass's stop check applies),
OPENDUBS_LIFT_M (0: the lift's travel for look-at when /zaxis/travel_m is silent).
"""
from __future__ import annotations

import datetime
import glob
import json
import math
import os
import shlex
import subprocess
import threading
import time
import urllib.request

ROOT = os.environ.get("OPENDUBS_HALLPASS_DIR", "/var/lib/opendubs/hallpass")
MISSIONS = os.path.join(ROOT, "missions")
DASH = os.environ.get("OPENDUBS_DASHBOARD", "http://127.0.0.1:8080")
STOPCHECK = os.environ.get("OPENDUBS_STOPCHECK", "python3 -m sitekit.capture.stopcheck")
SITEKIT = os.environ.get("OPENDUBS_SITEKIT", "/opt/hallpass/opendubs-site")   # deploy-all.sh copies sitekit/ here
IMAGE = os.environ.get("OPENDUBS_CAMERA_IMAGE", "/camera/color/image_raw")
INFO = os.environ.get("OPENDUBS_CAMERA_INFO", "/camera/color/camera_info")
DEPTH = os.environ.get("OPENDUBS_CAMERA_DEPTH", "/camera/depth/image_raw")   # the Femto's, registered to colour
LIFT_M = float(os.environ.get("OPENDUBS_LIFT_M", "0"))
OWNER = "hallpass"
ROBOT = os.environ.get("OPENDUBS_HALLPASS_ROBOT", "opendubs2")   # whose calibration HallPass applies
LOOK_TIMEOUT_S, LOOK_HOLD_S = 8.0, 120.0
FRAME_TIMEOUT_S = 3.0
GOAL_TIMEOUT_S = 300.0


# ---- pure (tests/test_hallpass_runner.py) --------------------------------------------------------------

def matrix(tf) -> "list":
    """A geometry_msgs/Transform as a 4 x 4 numpy matrix."""
    import numpy as np
    q, p = tf.rotation, tf.translation
    M = np.eye(4)
    M[:3, :3] = [[1 - 2 * (q.y ** 2 + q.z ** 2), 2 * (q.x * q.y - q.z * q.w), 2 * (q.x * q.z + q.y * q.w)],
                 [2 * (q.x * q.y + q.z * q.w), 1 - 2 * (q.x ** 2 + q.z ** 2), 2 * (q.y * q.z - q.x * q.w)],
                 [2 * (q.x * q.z - q.y * q.w), 2 * (q.y * q.z + q.x * q.w), 1 - 2 * (q.x ** 2 + q.y ** 2)]]
    M[:3, 3] = [p.x, p.y, p.z]
    return M


def nearest(history, t: float, max_gap_s: float = 0.25):
    """The (t, value) in history nearest t, or None when none is within max_gap_s."""
    best = min(history, key=lambda h: abs(h[0] - t), default=None)
    return best[1] if best is not None and abs(best[0] - t) <= max_gap_s else None


def missions(folder: str = MISSIONS) -> dict:
    """{id: path} of the navgoals files in folder (<id>.navgoals.yaml)."""
    return {os.path.basename(p)[:-len(".navgoals.yaml")]: p for p in sorted(glob.glob(os.path.join(folder, "*.navgoals.yaml")))}


def load_mission(path: str) -> dict:
    """A sitekit.navgoals/1 file, checked for what the runner uses."""
    import yaml
    doc = yaml.safe_load(open(path))
    if doc.get("schema") != "sitekit.navgoals/1":
        raise ValueError(f"{path}: schema {doc.get('schema')!r}, not sitekit.navgoals/1")
    for p in doc["poses"]:
        if p["header"]["frame_id"] != doc["frame_id"]:
            raise ValueError(f"stop {p.get('n')}: frame {p['header']['frame_id']} is not the mission's {doc['frame_id']}")
    return doc


def yaw_of(q: dict) -> float:
    return math.atan2(2 * (q["w"] * q["z"] + q.get("x", 0) * q.get("y", 0)), 1 - 2 * (q.get("y", 0) ** 2 + q["z"] ** 2))


def reaimed(cam, point, az_deg: float, el_deg: float):
    """The point to look at instead, so the target moves az_deg right and el_deg down in the image: the
    camera->point ray turned about the vertical through the camera (right = clockwise) and tipped down, at
    the same horizontal distance. cam and point in base_link."""
    dx, dy, dz = (point[i] - cam[i] for i in range(3))
    r = math.hypot(dx, dy)
    a = math.atan2(dy, dx) - math.radians(az_deg)          # right of centre: aim further right (base_link y is LEFT)
    el = math.atan2(dz, r) - math.radians(el_deg)          # below centre: aim lower
    return [cam[0] + r * math.cos(a), cam[1] + r * math.sin(a), cam[2] + r * math.tan(el)]


def act_on(res: dict) -> tuple[str, dict]:
    """(what to do once, its argument) for a first-attempt verdict: ("next", {}) when nothing is worth a retry."""
    v, s = res.get("verdict"), res.get("suggest") or {}
    if v == "reaim" and "az_deg" in s:
        return "reaim", {"az_deg": float(s["az_deg"]), "el_deg": float(s.get("el_deg", 0.0))}
    if v == "closer" and "goal" in s:
        return "closer", s["goal"]
    if v == "retake":
        return "retake", {"wait_s": float(s.get("wait_s", 0.5))}
    return "next", {}


# ---- the robot side --------------------------------------------------------------------------------------

def post(path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(DASH + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}") | {"http": e.code}
        except ValueError:
            return {"http": e.code}
    except OSError as e:
        return {"error": str(e)}


class Runner:
    def __init__(self):
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from geometry_msgs.msg import PoseStamped
        from nav2_msgs.action import NavigateToPose
        from sensor_msgs.msg import CameraInfo, Image
        from std_msgs.msg import String
        from tf2_ros import Buffer, TransformListener
        self.rclpy, self.String, self.PoseStamped, self.NavigateToPose = rclpy, String, PoseStamped, NavigateToPose
        rclpy.init()
        self.node = rclpy.create_node("hallpass_runner")
        self.log = self.node.get_logger()
        self.tf = Buffer()
        self._tfl = TransformListener(self.tf, self.node)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.p_state = self.node.create_publisher(String, "/hallpass/mission/state", latched)
        self.p_plan = self.node.create_publisher(String, "/hallpass/runner/plan", latched)
        self.p_pose = self.node.create_publisher(PoseStamped, "/hallpass/runner/pose", 10)
        self.p_check = self.node.create_publisher(String, "/hallpass/stopcheck/result", 10)
        self.nav = ActionClient(self.node, NavigateToPose, "navigate_to_pose")
        self.rc = {"deadman": False, "ch5": None, "at": 0.0}
        self.cam = {"image": None, "info": None, "depth": None}
        self.cmds: list[dict] = []
        self.st = {"state": "idle", "phase": None, "session": None, "mission": None, "missions": list(missions()), "floor": None,
                   "frame": None, "stop_n": None, "stop_count": None, "stop": None, "target": None, "look_at": None,
                   "redrive": [], "last_check": None, "deadman": False, "lease": None, "why": None}
        self.lock = threading.Lock()
        self.node.create_subscription(String, "/hallpass/mission/cmd", self._cmd, 10)
        self.node.create_subscription(String, "/robot/rc", self._rc, 10)
        self.node.create_subscription(String, "/base/owner", self._owner, latched)
        from std_msgs.msg import Float32
        self.lift = {"m": None, "at": 0.0}
        self.node.create_subscription(Float32, "/zaxis/travel_m",
                                      lambda m: self.lift.update(m=float(m.data), at=time.monotonic()), 10)
        from collections import deque
        from sensor_msgs.msg import JointState
        self.head_hist = deque(maxlen=200)                 # (t, {head_pan, head_tilt}) from /head/joint_states, ~20 Hz
        self.node.create_subscription(JointState, "/head/joint_states", self._head, 10)
        self.node.create_subscription(Image, IMAGE, lambda m: self.cam.__setitem__("image", m), qos_profile_sensor_data)
        self.node.create_subscription(CameraInfo, INFO, lambda m: self.cam.__setitem__("info", m), qos_profile_sensor_data)
        if DEPTH:
            self.node.create_subscription(Image, DEPTH, lambda m: self.cam.__setitem__("depth", m), qos_profile_sensor_data)
        self.node.create_timer(0.5, self.publish_state)
        self.node.create_timer(0.2, self._pose)
        self._exec = MultiThreadedExecutor()
        self._exec.add_node(self.node)
        threading.Thread(target=self._exec.spin, daemon=True).start()

    # -- inputs
    def _cmd(self, m):
        try:
            c = json.loads(m.data)
        except ValueError:
            return
        if isinstance(c, dict) and c.get("op") in ("start", "pause", "resume", "skip"):
            with self.lock:
                self.cmds.append(c)

    def _rc(self, m):
        try:
            d = json.loads(m.data)
        except ValueError:
            return
        self.rc = {"deadman": bool(d.get("deadman")) and d.get("ch5") == "AUTO", "ch5": d.get("ch5"), "at": time.monotonic()}

    def _owner(self, m):
        try:
            o = json.loads(m.data).get("owner")
        except (ValueError, AttributeError):
            return
        self.st["lease"] = o

    def _head(self, m):
        j = dict(zip(m.name, m.position))
        if "head_pan" in j and "head_tilt" in j:
            self.head_hist.append((m.header.stamp.sec + m.header.stamp.nanosec / 1e9,
                                   {"head_pan": float(j["head_pan"]), "head_tilt": float(j["head_tilt"])}))

    def joints_at(self, t: float):
        return nearest(list(self.head_hist), t)

    def deadman(self) -> bool:
        """Held, in AUTO, and heard within the last second: anything else is released."""
        return self.rc["deadman"] and time.monotonic() - self.rc["at"] < 1.0

    # -- outputs
    def publish_state(self, **change):
        self.st.update(change, t=round(time.time(), 2), deadman=self.deadman())
        self.p_state.publish(self.String(data=json.dumps(self.st)))

    def _pose(self):
        f = self.st.get("frame")
        if not f:
            return
        from rclpy.time import Time
        try:
            tr = self.tf.lookup_transform(f, "base_link", Time())
        except Exception:
            return
        p = self.PoseStamped()
        p.header = tr.header
        p.pose.position.x, p.pose.position.y, p.pose.position.z = (tr.transform.translation.x,
                                                                  tr.transform.translation.y, tr.transform.translation.z)
        p.pose.orientation = tr.transform.rotation
        self.p_pose.publish(p)

    def record(self, event: str, **kw):
        line = {"t": round(time.time(), 3), "event": event, **kw}
        with open(os.path.join(self.session_dir, "mission.jsonl"), "a") as f:
            f.write(json.dumps(line) + "\n")
        self.log.info(f"{event} {json.dumps(kw)[:200]}")

    # -- control
    def take_cmd(self, ops=("pause", "resume", "skip", "start")):
        with self.lock:
            for i, c in enumerate(self.cmds):
                if c["op"] in ops:
                    return self.cmds.pop(i)
        return None

    def hold_while_paused(self) -> bool:
        """Wait out a pause (the operator's, or the deadman let go). False when the stop was skipped."""
        paused_by_op = False
        while True:
            c = self.take_cmd(("pause", "resume", "skip"))
            if c and c["op"] == "skip":
                return False
            if c:
                paused_by_op = c["op"] == "pause"
            if not paused_by_op and self.deadman():
                return True
            why = "paused by the operator" if paused_by_op else "deadman released (or not in AUTO)"
            if self.st["state"] != "paused" or self.st["why"] != why:
                self.record("paused", why=why)
                self.publish_state(state="paused", why=why)
            time.sleep(0.2)

    def drive(self, x: float, y: float, yaw: float, frame: str, label: str) -> str:
        """navigate_to_pose to (x, y, yaw) in frame: 'ok', 'failed: ...' or 'skipped'. Cancelled and sent again
        across a pause."""
        from action_msgs.msg import GoalStatus
        while True:
            if not self.hold_while_paused():
                return "skipped"
            self.publish_state(state="running", phase="driving", why=None)
            if not self.nav.wait_for_server(timeout_sec=5.0):
                return "failed: no navigate_to_pose server (is opendubs-nav2 running?)"
            g = self.NavigateToPose.Goal()
            g.pose.header.frame_id = frame
            g.pose.pose.position.x, g.pose.pose.position.y = x, y
            g.pose.pose.orientation.z, g.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
            fut = self.nav.send_goal_async(g)
            if not self._wait(fut, 10.0) or not fut.result().accepted:
                return "failed: goal not accepted"
            h = fut.result()
            res = h.get_result_async()
            t0, interrupted = time.monotonic(), None
            while not res.done():
                c = self.take_cmd(("pause", "skip"))
                if c:
                    interrupted = c["op"]
                elif not self.deadman():
                    interrupted = "deadman"
                if interrupted or time.monotonic() - t0 > GOAL_TIMEOUT_S:
                    self._wait(h.cancel_goal_async(), 5.0)
                    break
                time.sleep(0.1)
            if interrupted == "skip":
                return "skipped"
            if interrupted == "pause":
                with self.lock:
                    self.cmds.insert(0, {"op": "pause"})
                continue
            if interrupted == "deadman":
                continue
            if not res.done():
                return f"failed: no result in {GOAL_TIMEOUT_S:.0f} s"
            status = res.result().status
            return "ok" if status == GoalStatus.STATUS_SUCCEEDED else f"failed: status {status} ({label})"

    def _wait(self, fut, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while not fut.done() and time.monotonic() < end:
            time.sleep(0.05)
        return fut.done()

    def to_base(self, frame: str, xyz):
        """A floor-frame point in base_link now (tf2), or None."""
        from geometry_msgs.msg import PointStamped
        from rclpy.time import Time
        import tf2_geometry_msgs  # noqa: F401  (registers PointStamped with tf2)
        p = PointStamped()
        p.header.frame_id = frame
        p.point.x, p.point.y, p.point.z = (float(v) for v in xyz)
        try:
            q = self.tf.transform(p, "base_link", timeout=self.rclpy.duration.Duration(seconds=1.0))
        except Exception as e:
            self.log.warning(f"to base_link: {e}")
            return None
        return [q.point.x, q.point.y, q.point.z]

    def look_at(self, point) -> dict:
        r = post("/api/head/look_at", {"owner": OWNER, "frame_id": "base_link", "x": point[0], "y": point[1], "z": point[2],
                                       "lift_m": self.lift_m(), "timeout_s": LOOK_TIMEOUT_S, "hold_s": LOOK_HOLD_S},
                 LOOK_TIMEOUT_S + 5)
        self.publish_state(head={k: r.get(k) for k in ("pan_deg", "tilt_deg", "settled", "reason")})
        return r

    def lift_m(self) -> float:
        """The lift's travel: /zaxis/travel_m when heard in the last 2 s, else OPENDUBS_LIFT_M."""
        return self.lift["m"] if self.lift["m"] is not None and time.monotonic() - self.lift["at"] < 2.0 else LIFT_M

    def camera_in_base(self):
        """The colour camera's optical origin in base_link now, for reaim, or None."""
        from rclpy.time import Time
        info = self.cam["info"]
        if info is None:
            return None
        try:
            t = self.tf.lookup_transform("base_link", info.header.frame_id, Time()).transform.translation
        except Exception:
            return None
        return [t.x, t.y, t.z]

    def grab(self, frame: str, out_dir: str, tag: str):
        """One colour frame newer than now (and depth), K and T_floor_camera at its stamp, written for the stop
        check: (request dict, None) or (None, why)."""
        import cv2
        import numpy as np
        from rclpy.time import Time
        t0 = self.node.get_clock().now()
        end = time.monotonic() + FRAME_TIMEOUT_S
        while time.monotonic() < end:
            im = self.cam["image"]
            if im is not None and Time.from_msg(im.header.stamp) > t0:
                break
            time.sleep(0.03)
        else:
            return None, f"no frame on {IMAGE} within {FRAME_TIMEOUT_S:.0f} s"
        info = self.cam["info"]
        if info is None:
            return None, f"no {INFO}"
        try:
            tr = self.tf.lookup_transform(frame, im.header.frame_id or info.header.frame_id,
                                          Time.from_msg(im.header.stamp), timeout=self.rclpy.duration.Duration(seconds=1.0))
        except Exception as e:
            return None, f"no TF {frame} -> camera at the frame's stamp: {e}"
        T = matrix(tr.transform)
        optical = im.header.frame_id or info.header.frame_id
        stamp = Time.from_msg(im.header.stamp)
        ch = {"rgb8": 3, "bgr8": 3, "mono8": 1}.get(im.encoding)
        if not ch:
            return None, f"{IMAGE} is {im.encoding}, not rgb8/bgr8/mono8"
        img = np.frombuffer(im.data, np.uint8).reshape(im.height, im.step)[:, :im.width * ch].reshape(im.height, im.width, ch)
        path = os.path.join(out_dir, f"{tag}.png")
        cv2.imwrite(path, img[..., ::-1] if im.encoding == "rgb8" else img)
        req = {"frame": path, "K": [list(info.k[0:3]), list(info.k[3:6]), list(info.k[6:9])], "T_floor_camera": T.tolist(),
               "stamp": stamp.nanoseconds / 1e9}
        # HallPass's head calibration (stopcheck.calibrated): the raw neck angles at the frame's time and the two
        # fixed transforms below the tilt joint, so it can put its per-robot offsets on TF's camera pose
        joints = self.joints_at(stamp.nanoseconds / 1e9)
        try:
            a = self.tf.lookup_transform("head_tilt_link", "camera_link", Time()).transform
            b = self.tf.lookup_transform("camera_link", optical, Time()).transform
        except Exception:
            a = b = None
        if joints and a is not None:
            req["head"] = {"joints": joints, "T_tilt_camlink": matrix(a).tolist(), "T_camlink_camera": matrix(b).tolist(),
                           "robot": ROBOT}
        d = self.cam["depth"]
        if DEPTH and d is not None and d.encoding in ("16UC1", "32FC1"):
            arr = np.frombuffer(d.data, np.uint16 if d.encoding == "16UC1" else np.float32).reshape(d.height, -1)[:, :d.width]
            np.save(os.path.join(out_dir, f"{tag}-depth.npy"), arr)
            req["depth"] = os.path.join(out_dir, f"{tag}-depth.npy")
        return req, None

    def stopcheck(self, req: dict) -> dict:
        try:
            r = subprocess.run(shlex.split(STOPCHECK), input=json.dumps(req), capture_output=True, text=True,
                               timeout=30, cwd=SITEKIT or None)
            return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else \
                {"verdict": "skip", "reason": f"stop check said nothing: {r.stderr.strip()[-200:]}"}
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            return {"verdict": "skip", "reason": f"stop check failed: {e}"}

    def target(self, frame: str, stop: dict, tg: dict) -> str:
        """Aim, check, act once, check again: the final verdict."""
        site = tg["site"]
        self.publish_state(phase="aiming", target=site, look_at={"site": site, "xyz": tg["xyz"]})
        aim, verdict, attempt, head_limit = list(tg["xyz"]), None, 0, False
        while attempt < 2:
            point = self.to_base(frame, aim)
            if point is None:
                verdict = {"verdict": "skip", "reason": f"no TF {frame} -> base_link"}
                break
            la = self.look_at(point)
            head_limit = bool(la.get("head_at_limit")) or la.get("reason") == "out_of_range"   # aimed as near as it can
            if not la.get("settled") and not head_limit and la.get("reason") != "stopped_short":
                verdict = {"verdict": "skip", "reason": f"look-at: {la.get('reason') or la.get('error') or la}"}
                break
            self.publish_state(phase="checking")
            req, why = self.grab(frame, self.session_dir, f"S{stop['n']:03d}-{site}-a{attempt}")
            if req is None:
                verdict = {"verdict": "skip", "reason": why}
                break
            req.update(target=tg, attempt=attempt, out_dir=self.session_dir, session=self.st["session"],
                       t=req.pop("stamp"), head_at_limit=head_limit)
            verdict = self.stopcheck(req)
            self.p_check.publish(self.String(data=json.dumps({"site": site, "attempt": attempt, **verdict})))
            self.publish_state(last_check={"target": site, "site": site, "verdict": verdict.get("verdict"),
                                           "px_on_target": verdict.get("px_on_target"), "sharpness": verdict.get("sharpness")})
            self.record("check", stop=stop["n"], site=site, attempt=attempt, verdict=verdict.get("verdict"),
                        reason=verdict.get("reason"), suggest=verdict.get("suggest"),
                        calibration=verdict.get("calibration"), head="head" in req)
            if attempt == 1:
                break
            what, arg = act_on(verdict)
            if what == "next":
                break
            self.record("act", stop=stop["n"], site=site, what=what, arg=arg)
            if what == "reaim":
                cam = self.camera_in_base()
                if cam is None:
                    break
                aimed = reaimed(cam, point, arg["az_deg"], arg["el_deg"])
                back = self.to_floor(aimed)
                if back is None:
                    break
                aim = back
            elif what == "closer":
                got = self.drive(float(arg["x"]), float(arg["y"]), math.radians(float(arg.get("yaw_deg", 0.0))), frame,
                                 f"closer to {site}")
                self.record("drive", stop=stop["n"], site=site, why="closer", result=got)
                if got != "ok":
                    verdict = {"verdict": "skip", "reason": f"closer: {got}"}
                    break
            elif what == "retake":
                time.sleep(arg["wait_s"])
            attempt = 1
        v = (verdict or {}).get("verdict")
        if v != "ok":
            self.st["redrive"].append({"stop": stop["n"], "site": site, "verdict": v, "reason": (verdict or {}).get("reason")})
        return v

    def to_floor(self, xyz):
        """A base_link point in the mission's floor frame now, or None."""
        from geometry_msgs.msg import PointStamped
        import tf2_geometry_msgs  # noqa: F401
        p = PointStamped()
        p.header.frame_id = "base_link"
        p.point.x, p.point.y, p.point.z = (float(v) for v in xyz)
        try:
            q = self.tf.transform(p, self.st["frame"], timeout=self.rclpy.duration.Duration(seconds=1.0))
        except Exception:
            return None
        return [q.point.x, q.point.y, q.point.z]

    def run(self, mission_id: str):
        path = missions().get(mission_id)
        if not path:
            self.publish_state(state="failed", why=f"no mission {mission_id}")
            return
        try:
            doc = load_mission(path)
        except (ValueError, KeyError, OSError) as e:
            self.publish_state(state="failed", why=str(e))
            return
        session = f"{datetime.datetime.now().astimezone():%Y%m%d-%H%M%S}-{mission_id}"
        self.session_dir = os.path.join(ROOT, session)
        os.makedirs(self.session_dir, exist_ok=True)
        frame, poses = doc["frame_id"], doc["poses"]
        self.p_plan.publish(self.String(data=json.dumps(doc, default=float)))
        self.publish_state(state="running", session=session, mission=mission_id, floor=doc.get("floor"), frame=frame,
                           stop_count=len(poses), redrive=[], last_check=None, why=None)
        self.record("start", mission=mission_id, path=path, frame=frame, stops=len(poses))
        for stop in poses:
            self.publish_state(stop_n=stop["n"], stop={"n": stop["n"], "of": len(poses), "mark": stop.get("mark")})
            pos = stop["pose"]["position"]
            got = self.drive(pos["x"], pos["y"], yaw_of(stop["pose"]["orientation"]), frame, f"stop {stop['n']}")
            self.record("drive", stop=stop["n"], result=got)
            if got.startswith(("failed: goal not accepted", "failed: no navigate_to_pose")):
                # Nav2 itself is down (not this stop's fault): stop the mission instead of skipping every stop
                post("/api/head/look_at/release", {"owner": OWNER}, 5)
                self.record("failed", why=got)
                self.publish_state(state="failed", phase=None, why=f"Nav2: {got[len('failed: '):]}")
                return
            if got != "ok":
                self.st["redrive"].append({"stop": stop["n"], "site": None, "verdict": "skip", "reason": got})
                continue
            self.publish_state(phase="dwelling")
            time.sleep(float(stop.get("dwell_s", 0)))
            for tg in stop.get("targets") or []:
                if isinstance(tg, dict) and "xyz" in tg:
                    self.target(frame, stop, tg)
        post("/api/head/look_at/release", {"owner": OWNER}, 5)
        self.record("done", redrive=self.st["redrive"])
        self.publish_state(state="done", phase=None, target=None, look_at=None, why=None)

    def loop(self):
        while True:
            c = self.take_cmd(("start",))
            if c:
                self.publish_state(missions=list(missions()))
                try:
                    self.run(c.get("mission"))
                except Exception as e:                       # noqa: BLE001 — the mission fails, the runner stays
                    self.log.error(f"mission failed: {e!r}")
                    post("/api/head/look_at/release", {"owner": OWNER}, 5)
                    self.publish_state(state="failed", why=repr(e))
            time.sleep(0.2)


def main():
    os.makedirs(MISSIONS, exist_ok=True)
    r = Runner()
    try:
        r.loop()
    except KeyboardInterrupt:
        pass
    finally:
        post("/api/head/look_at/release", {"owner": OWNER}, 3)
        r._exec.shutdown()
        r.rclpy.try_shutdown()


if __name__ == "__main__":
    main()
