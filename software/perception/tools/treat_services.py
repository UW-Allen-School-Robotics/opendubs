"""Trick or treat as ROS 2 services: each step of treat_reach.py on its own, called one at a time by anyone (treat_sequence.py
runs them in the order `reach` does). robot_api_interfaces like the other /perception services: RobotCommand takes a JSON
`req` and returns a result code (ResultCode), a message and JSON `data`; RobotQuery returns data.

  treat_follow.sh services [--real] [--head ...]   this node in the background (services-down stops it)

THE SERVICES, /perception/treat/<name>; each returns when its step has ended (done, failed, or interrupted):

  ready          {}                                      the arm to its ready pose (the arm service's `ready`)
  move_pose      {"position": [x,y,z], "orientation": [w,x,y,z]?, "speed"?, "tolerance_mm"?}
                                                         the hand to a pose, arm_mount (the arm service's `reach_pose`)
  move_joints    {"joints": [7 rad] | "joints_deg": [7], "speed"?}
                                                         ONE continuous move: a straight joint line from where the arm is,
                                                         self-collision checked (engage.PathCheck), minimum-jerk, played
                                                         by follow_joint_trajectory
  centre_person  {"timeout_s"?: 20}                      the head centres the nearest person, settles and HOLDS there:
                                                         the shoulders --head-aim-deg (15) above the picture's centre,
                                                         so the body and the hands are in frame; data: tilt and pan
  get_hand       {"timeout_s"?: 10, "stable_frames"?: 5, "still_m"?: 0.05}
                                                         waits for a hand held out and still; data: side, palm in arm_mount
  find_hand      {"find_s"?: 20, "wait_s"?: 10, ...}     centre_person, then get_hand: one call
  plan_treat     {"palm": [x,y,z], "jaws_level"?: true}  no motion: the goal toward that palm (reach_goal: the standoff and
                                                         height cap) and, jaws-level, the arm's joints and pose there
                                                         (plan_jaws); data: joints, pose, mm from the goal
  hold           {"seconds"?: 5}                         waits with the arm where it is (the treat); --real: a lost
                                                         permit meanwhile latches the stop and returns STOPPED
  gripper_open   {}                                      the gripper fully open (the arm service's open_gripper)
  gripper_close  {}                                      the gripper shut (close_gripper). Both succeed whatever the
                                                         gripper was doing and whatever is between the fingers (data
                                                         "holding": true if it stopped on something); only an error
                                                         (refused, no gripper, timeout, stop) fails
  stop           {}                                      cancels any running move (the arm holds) and stops the head
  rearm          {}                                      --real: clears the stop that a lost permit latched
  status         (RobotQuery)                            mode, permit, stopped, busy, the arm's joints, the head

One step at a time: a second call while one runs is refused (busy). The head and the arm never move together: the head
moves only in centre_person / find_hand, which stop it before they return.

--real: THE REAL RIGHT ARM. Every motion is refused unless CH5 is MANUAL or AUTO, the e-stop is clear and the deadman is
held (as the arm service's own gate reads them, 10 times a second). If that permit is lost during a move, the move is
cancelled (the arm holds) and a STOP IS LATCHED: every motion is refused until `rearm`, so nothing moves again after the
deadman is released unless someone asks for it. Speeds capped (treat_reach.REAL_SPEED, REAL_SPEED_MAX); move_pose refuses
a goal above REAL_MAX_UP over the arm's shoulder. Without --real the motions go to the simulated arm (--sim-domain).
"""

import argparse
import json
import math
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grasp_to_sim as g2s  # noqa: E402
from hand_follow import ARM_FRAME, SHOULDER, Frames, HeadTilt, PositionIk, fmt, neck_host_from_env  # noqa: E402
from treat_brain import SIDES, HandCapture, HeadLaw, HeadParams, Offer, OfferParams, reach_goal  # noqa: E402
from treat_follow import Head  # noqa: E402
from treat_reach import (CANDY_Q_DEG, REAL_MAX_UP, REAL_SPEED, REAL_SPEED_MAX, REAL_STANDOFF, dummy_state,  # noqa: E402
                         plan_jaws)

JOINTS = [f"openarm_right_joint{i}" for i in range(1, 8)]
NAMES = {0: "SUCCESS", 1: "FAILURE", 2: "INVALID_INPUT", 5: "NOT_FOUND (out of reach)"}


class Refused(Exception):
    """A request this node will not serve now: (result code, message)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Treat:
    def __init__(self, args, rclpy, robot_node, arm_node, ResultCode):
        self.a, self.rclpy, self.node, self.arm_node, self.RC = args, rclpy, robot_node, arm_node, ResultCode
        self.lock = threading.Lock()
        self.busy, self.stopped, self.goal = None, None, None
        self.latest = {"msg": None, "at": 0.0}
        self.permit = {"ok": False, "why": "not read yet"}
        self.real_j2 = {"value": {}, "at": 0.0}
        self.arm_joints = {"q": None, "at": 0.0}
        self.ik = PositionIk(args.model, "right")
        self.frames = None if args.dummy else Frames(rclpy, robot_node, "joints")
        self.head = None

    # -- inputs --------------------------------------------------------------------------------------------------------

    def on_state(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        self.latest["msg"], self.latest["at"] = data, time.monotonic()

    def on_real_joints(self, msg):
        got = {n: p for n, p in zip(msg.name, msg.position) if n in ("openarm_left_joint2", "openarm_right_joint2")}
        if got:
            self.real_j2["value"].update(got)
            self.real_j2["at"] = time.monotonic()

    def on_arm_joints(self, msg):
        got = dict(zip(msg.name, msg.position))
        if all(n in got for n in JOINTS):
            self.arm_joints["q"], self.arm_joints["at"] = np.array([got[n] for n in JOINTS]), time.monotonic()

    def watch_permit(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "openarm", "gafro"))
        import guards
        while True:
            try:
                r = guards.robot_state(timeout=0.2)
                ok = guards.motion_still_permitted(state=lambda _u: r)
                why = None if ok else f"CH5 {r.get('ch5')}, e-stop {r.get('estop')}, deadman {r.get('deadman')}"
            except Exception as error:  # noqa: BLE001 -- unknown is no
                ok, why = False, f"the dashboard does not answer ({type(error).__name__})"
            self.permit.update(ok=ok, why=why)
            time.sleep(0.1)

    def arms_clear(self):
        """The follower's rule for the head: both arms' shoulders known (the left counts as stowed with --left-stowed)
        and neither raised. Off in the simulation, where nothing publishes the real arms (--arm-check turns it on)."""
        if not self.a.arm_check:
            return None
        values = dict(self.real_j2["value"])
        if self.a.left_stowed:
            values.setdefault("openarm_left_joint2", 0.0)
        if time.monotonic() - self.real_j2["at"] > 2.0 or len(values) < 2:
            return "the real arms' positions are not known (/joint_states)"
        up = [n for n, v in values.items() if math.degrees(abs(v)) > self.a.arm_j2_max]
        return f"an arm's shoulder is raised ({', '.join(up)})" if up else None

    # -- gates ---------------------------------------------------------------------------------------------------------

    def take(self, name):
        with self.lock:
            if self.busy:
                raise Refused(self.RC.FAILURE, f"busy: {self.busy} is running (one step at a time)")
            self.busy = name

    def give(self):
        with self.lock:
            self.busy = None

    def motion_gate(self):
        if self.a.real:
            if self.stopped:
                raise Refused(self.RC.FAILURE, f"stopped ({self.stopped}): call rearm before any move")
            if not self.permit["ok"]:
                raise Refused(self.RC.FAILURE, f"motion not permitted: {self.permit['why']} (hold the deadman)")

    def watch_during(self, handle):
        """Cancel `handle` the moment the permit is lost (--real) or stop is asked; True if it was cut short."""
        if self.a.real and not self.permit["ok"]:
            self.stopped = f"the permit was lost during a move: {self.permit['why']}"
            handle.cancel_goal_async()
            return True
        return False

    def wait_result(self, handle, timeout):
        result = handle.get_result_async()
        deadline = time.monotonic() + timeout
        while not result.done() and time.monotonic() < deadline:
            if self.watch_during(handle):
                time.sleep(0.5)
                raise Refused(self.RC.FAILURE, f"STOPPED: {self.stopped}. The arm holds; nothing moves until rearm")
            time.sleep(0.05)
        if not result.done():
            handle.cancel_goal_async()
            raise Refused(self.RC.TIMEOUT, f"no result in {timeout:.0f} s: cancelled (the arm holds)")
        from action_msgs.msg import GoalStatus
        status = result.result().status
        if status == GoalStatus.STATUS_CANCELED:
            raise Refused(self.RC.FAILURE, "cancelled (stop): the arm holds where it is")
        if status != GoalStatus.STATUS_SUCCEEDED:
            r = result.result().result
            why = getattr(r, "message", "") or getattr(r, "error_string", "")
            raise Refused(self.RC.FAILURE, f"the move was aborted: {why} (the arm holds)")
        return result.result().result

    def send(self, client, goal, what):
        if not client.wait_for_server(timeout_sec=5.0):
            raise Refused(self.RC.SERVICE_NOT_RUNNING, f"no {what} server on the arm's domain: is the arm service up?")
        sent = client.send_goal_async(goal)
        deadline = time.monotonic() + 10.0
        while not sent.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        handle = sent.result() if sent.done() else None
        if handle is None or not handle.accepted:
            raise Refused(self.RC.FAILURE, f"{what} was rejected (another move running, or invalid: the arm service's log)")
        self.goal = handle
        return handle

    # -- the steps -----------------------------------------------------------------------------------------------------

    def arm_command(self, name, params):
        from opendubs_arm_interfaces.action import ArmCommand
        self.motion_gate()
        handle = self.send(self.arm_client, ArmCommand.Goal(name=name, params=json.dumps(params)), name)
        try:
            r = self.wait_result(handle, self.a.result_s)
        finally:
            self.goal = None
        data = json.loads(r.data) if r.data else {}
        if r.result_code != 0:
            raise Refused(self.RC.NOT_FOUND if r.result_code == 5 else self.RC.FAILURE,
                          f"{name}: {NAMES.get(r.result_code, r.result_code)}: {r.message}")
        return f"{name}: {r.message}", data

    def ready(self, req):
        return self.arm_command("ready", {})

    def speed(self, req):
        if self.a.real:
            return min(float(req.get("speed") or REAL_SPEED), REAL_SPEED_MAX)
        return float(req["speed"]) if req.get("speed") else None

    def move_pose(self, req):
        if "position" not in req:
            raise Refused(self.RC.INVALID_INPUT, 'move_pose needs {"position": [x, y, z]} (arm_mount, m)')
        p = [float(v) for v in req["position"]]
        if self.a.real and p[2] - SHOULDER[2] > REAL_MAX_UP + 1e-6:
            raise Refused(self.RC.INVALID_INPUT, f"z {p[2]:.3f} is over {REAL_MAX_UP} m above the arm's shoulder (--real)")
        params = {"position": p, "tolerance_mm": float(req.get("tolerance_mm", self.a.tolerance_mm))}
        if req.get("orientation") is not None:
            params["orientation"] = [float(v) for v in req["orientation"]]
        if self.speed(req):
            params["speed"] = self.speed(req)
        return self.arm_command("reach_pose", params)

    def move_joints(self, req):
        from builtin_interfaces.msg import Duration
        from control_msgs.action import FollowJointTrajectory
        from trajectory_msgs.msg import JointTrajectoryPoint
        sys.path.insert(0, os.path.join(HERE, "..", "..", "openarm", "gafro"))
        import engage
        if "joints" in req:
            q1 = np.array([float(v) for v in req["joints"]])
        elif "joints_deg" in req:
            q1 = np.radians([float(v) for v in req["joints_deg"]])
        else:
            raise Refused(self.RC.INVALID_INPUT, 'move_joints needs {"joints": [7 rad]} or {"joints_deg": [7]}')
        if q1.shape != (7,):
            raise Refused(self.RC.INVALID_INPUT, "seven joints, the right arm's 1..7")
        self.motion_gate()
        if self.arm_joints["q"] is None or time.monotonic() - self.arm_joints["at"] > 0.5:
            raise Refused(self.RC.SERVICE_NOT_RUNNING, "no current joints from the arm (/joint_states)")
        q0 = self.arm_joints["q"].copy()
        refusal = engage.PathCheck(self.a.model, JOINTS).refusal(q0, q1)
        if refusal:
            raise Refused(self.RC.FAILURE, f"the straight path collides ({refusal}): nothing sent")
        vmax = self.speed(req) or 0.4
        span = float(np.abs(q1 - q0).max())
        T = max(1.5, 1.875 * span / vmax)
        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = JOINTS
        n = max(20, int(T * 20))
        for i in range(n + 1):
            s, t = i / n, T * i / n
            b = 10 * s ** 3 - 15 * s ** 4 + 6 * s ** 5
            db = (30 * s ** 2 - 60 * s ** 3 + 30 * s ** 4) / T
            pt = JointTrajectoryPoint()
            pt.positions = [float(v) for v in q0 + (q1 - q0) * b]
            pt.velocities = [float(v) for v in (q1 - q0) * db]
            pt.time_from_start = Duration(sec=int(t), nanosec=int((t - int(t)) * 1e9))
            goal.trajectory.points.append(pt)
        handle = self.send(self.traj_client, goal, "follow_joint_trajectory")
        try:
            r = self.wait_result(handle, T + self.a.result_s)
        finally:
            self.goal = None
        if r.error_code != 0:
            raise Refused(self.RC.FAILURE, f"the move ended with error {r.error_code}: {r.error_string}")
        return (f"moved: {math.degrees(span):.0f} deg at most in {T:.1f} s ({r.error_string})",
                {"seconds": round(T, 2), "joints_deg": np.degrees(q1).round(2).tolist()})

    def centre_person(self, req):
        if self.head is None:
            if self.a.dummy:
                return "no head (--dummy)", {}
            raise Refused(self.RC.SERVICE_NOT_RUNNING, "the head is off (--no-head, or --real without --head)")
        timeout = float(req.get("timeout_s", 20.0))
        self.head.start()
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                msg = self.latest["msg"]
                self.head.put_mid(None if msg is None else msg.get("shoulder_mid"), self.latest["at"])
                if self.head.acquired():
                    hs = self.head.status()
                    return (f"centred and holding: tilt {hs['tilt']} pan {hs['pan']}",
                            {"tilt": hs["tilt"], "pan": hs["pan"]})
                time.sleep(0.05)
        finally:
            self.head.stop()                    # the neck holds where it is (the lease lapses)
        why = self.head.status()["why"]
        raise Refused(self.RC.NOT_FOUND, f"nobody centred in {timeout:.0f} s ({why})")

    def frame_in_arm(self, msg, at):
        if self.head is not None and at - self.head.quiet_since() <= self.a.head_quiet_s:
            return None
        try:
            T, _ = self.frames.camera_in_arm(msg.get("stamp"))
        except LookupError:
            return None

        def to_arm(v):
            return T[:3, :3] @ np.asarray(v, float) + T[:3, 3]
        shoulders = {s: to_arm(v) for s, v in (msg.get("shoulders") or {}).items() if s in SIDES and v}
        palms = {s: to_arm(d["xyz"]) for s, d in (msg.get("palms") or {}).items()
                 if s in SIDES and d and d.get("followable") and d.get("xyz")}
        return shoulders, palms

    def get_hand(self, req):
        timeout = float(req.get("timeout_s", 10.0))
        capture = HandCapture(Offer(OfferParams()), int(req.get("stable_frames", 5)), float(req.get("still_m", 0.05)))
        t0, seen, nxt, got = time.monotonic(), None, 0.0, None
        while time.monotonic() - t0 < timeout and got is None:
            now = time.monotonic()
            if self.a.dummy:
                if now >= nxt:
                    nxt = now + 1 / 14
                    shoulders, palms = dummy_state(now - t0)
                    got = capture.update(palms, shoulders)
            else:
                msg, at = self.latest["msg"], self.latest["at"]
                if msg is not None and msg is not seen:
                    seen = msg
                    f = self.frame_in_arm(msg, at)
                    if f is not None:
                        got = capture.update(f[1], f[0])
            time.sleep(0.01)
        if got is None:
            raise Refused(self.RC.NOT_FOUND, f"no hand held out and still in {timeout:.0f} s")
        side, palm = got
        return (f"the {side} hand at {fmt(palm)} in {ARM_FRAME}",
                {"side": side, "palm": [round(float(v), 4) for v in palm], "frame": ARM_FRAME})

    def find_hand(self, req):
        msg, data = self.centre_person({"timeout_s": req.get("find_s", 20.0)})
        msg2, data2 = self.get_hand({k: req[k] for k in ("stable_frames", "still_m") if k in req} |
                                    {"timeout_s": req.get("wait_s", 10.0)})
        return f"{msg}; {msg2}", {"head": data, **data2}

    def plan_treat(self, req):
        if "palm" not in req:
            raise Refused(self.RC.INVALID_INPUT, 'plan_treat needs {"palm": [x, y, z]} (arm_mount, m)')
        palm = np.array([float(v) for v in req["palm"]])
        standoff = max(float(req.get("standoff", self.a.standoff)), REAL_STANDOFF if self.a.real else 0.0)
        max_up = min(float(req.get("max_up", self.a.max_up)), REAL_MAX_UP) if self.a.real else float(req.get("max_up", self.a.max_up))
        goal = reach_goal(palm, SHOULDER, standoff=standoff, max_up=max_up)
        ik = PositionIk(self.a.model, "right")
        if req.get("jaws_level", True):
            found = plan_jaws(ik, goal, np.radians(CANDY_Q_DEG))
            if found is None:
                raise Refused(self.RC.NOT_FOUND, "no jaws-level pose toward that palm")
            p, R, q, off = found
        else:
            p, R, q = ik.solve(goal, iterations=300)
            off = float(np.linalg.norm(p - goal))
        x, y, z, w = (float(v) for v in g2s.mat_to_quat(R))
        return (f"the hand at {fmt(p)}, {off * 1000:.0f} mm from the goal {fmt(goal)}",
                {"joints": [round(float(v), 5) for v in q], "joints_deg": np.degrees(q).round(2).tolist(),
                 "pose": {"position": [round(float(v), 4) for v in p], "orientation": [round(w, 5), round(x, 5), round(y, 5), round(z, 5)]},
                 "goal": [round(float(v), 4) for v in goal], "mm_from_goal": round(off * 1000, 1)})

    def hold(self, req):
        """Wait `seconds` with the arm where it is (the treat being taken). --real: losing the permit meanwhile latches
        the stop and returns STOPPED."""
        seconds = float(req.get("seconds", 5.0))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.a.real and not self.permit["ok"]:
                self.stopped = f"the permit was lost while holding: {self.permit['why']}"
                raise Refused(self.RC.FAILURE, f"STOPPED: {self.stopped}. Nothing moves until rearm")
            time.sleep(0.05)
        return f"held {seconds:.0f} s", {}

    def grip(self, req, closing):
        """The gripper through the arm service's own commands on /right_arm/command (ArmCommand `open_gripper`,
        `close_gripper`; software/openarm/gafro/commands.py, 2026-10-29), gated as every move: fully open or fully
        shut, no position. Whatever the gripper was doing before (already open, already shut) and whatever is between
        the fingers (nothing, a candy), a command the arm service ran to the end is a SUCCESS: the arm service's own
        "closed on nothing" and a finger stopped on something are successes here, with data "holding" saying which.
        A FAILURE is only an error: refused, the gripper not available (use_gripper and its calibration in the arm's
        hardware config), timed out, cancelled or stopped. (Was the gripper_cmd action, which never moved the gripper
        on opendubs2, 2026-10-28.)"""
        from action_msgs.msg import GoalStatus
        from opendubs_arm_interfaces.action import ArmCommand
        name = "close_gripper" if closing else "open_gripper"
        self.motion_gate()
        handle = self.send(self.arm_client, ArmCommand.Goal(name=name, params="{}"), name)
        result = handle.get_result_async()
        deadline = time.monotonic() + float(req.get("timeout_s", 10.0))
        try:
            r = self.wait_result(handle, float(req.get("timeout_s", 10.0)))
        finally:
            self.goal = None
        if not result.done():
            handle.cancel_goal_async()
            raise Refused(self.RC.TIMEOUT, f"{name} did not finish: cancelled")
        status, r = result.result().status, result.result().result
        if status == GoalStatus.STATUS_CANCELED:
            raise Refused(self.RC.FAILURE, f"{name} cancelled (stop): the gripper holds where it is")
        data = json.loads(r.data) if r.data else {}
        mm = round(float(data["position"]) * 1000, 1) if data.get("position") is not None else None
        at = f" at {mm} mm" if mm is not None else ""
        # the arm service aborts a close that shut on nothing ("closed on nothing") and a finger that stalled short of
        # its goal; both ran to the end, so both are a success here. Anything else it aborts is an error.
        ran = status == GoalStatus.STATUS_SUCCEEDED or (
            status == GoalStatus.STATUS_ABORTED and (data.get("stalled") or r.message.startswith("closed on nothing")))
        if not ran:
            raise Refused(self.RC.FAILURE, f"{name}: {r.message or NAMES.get(r.result_code, r.result_code)}{at}")
        holding = bool(data.get("holding"))
        return (f"gripper {'closed' if closing else 'open'}{at}" + (" (holding something)" if holding else "")
                + (f" ({r.message})" if r.message and r.message != "reached" else "")), {"position_mm": mm, "holding": holding}

    def gripper_open(self, req):
        return self.grip(req, closing=False)

    def gripper_close(self, req):
        return self.grip(req, closing=True)

    def stop(self, req):
        h = self.goal
        if h is not None:
            h.cancel_goal_async()
        if self.head is not None:
            self.head.stop()
        return ("stop: " + ("the running move cancelled, the arm holds" if h is not None else "nothing was moving"), {})

    def rearm(self, req):
        was = self.stopped
        self.stopped = None
        return ("rearmed" + (f" (was stopped: {was})" if was else " (was not stopped)"), {})

    def status(self):
        q = self.arm_joints["q"]
        return {"mode": "real" if self.a.real else "simulation", "permit": dict(self.permit) if self.a.real else None,
                "stopped": self.stopped, "busy": self.busy,
                "arm_joints_deg": None if q is None else np.degrees(q).round(1).tolist(),
                "arm_joints_age_s": None if q is None else round(time.monotonic() - self.arm_joints["at"], 2),
                "head": None if self.head is None else self.head.status(),
                "tracker_age_s": None if self.latest["msg"] is None else round(time.monotonic() - self.latest["at"], 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", action="store_true", help="THE REAL RIGHT ARM (above)")
    ap.add_argument("--head", dest="real_head", action="store_true", help="--real: the head too (needs --left-stowed)")
    ap.add_argument("--left-stowed", action="store_true", help="--real --head: the left arm is not run and is at stow")
    ap.add_argument("--no-head", dest="head", action="store_false", help="no head")
    ap.add_argument("--head-pan", action="store_true")
    ap.add_argument("--head-aim-deg", type=float, default=15.0,
                    help="hold the person's shoulders this far ABOVE the picture's centre (deg): the head looks lower, so "
                         "a hand held out at the waist is in frame (the top of the head need not be)")
    ap.add_argument("--arm-check", action="store_true", help="the real arms' clearance rule for the head (on with --real)")
    ap.add_argument("--arm-j2-max", type=float, default=142.0)
    ap.add_argument("--neck-host", default=None)
    ap.add_argument("--head-quiet-s", type=float, default=0.35)
    ap.add_argument("--head-lease-ms", type=int, default=600)
    ap.add_argument("--dummy", action="store_true", help="no camera, no head: a scripted person (treat_reach.dummy_state)")
    ap.add_argument("--state-topic", default="/perception/hand/state")
    ap.add_argument("--standoff", type=float, default=0.10)
    ap.add_argument("--max-up", type=float, default=0.35)
    ap.add_argument("--tolerance-mm", type=float, default=25.0)
    ap.add_argument("--result-s", type=float, default=60.0)
    ap.add_argument("--model", default=g2s.REPO_MODEL if os.path.exists(g2s.REPO_MODEL) else "openarm_assets:bimanual")
    ap.add_argument("--robot-domain", type=int, default=int(os.environ.get("ROS_DOMAIN_ID", "0")))
    ap.add_argument("--sim-domain", type=int, default=78)
    args = ap.parse_args()
    args.head = args.head and not args.dummy
    if args.real:
        if args.dummy:
            ap.error("--real and --dummy together: a scripted person must not drive the real arm")
        args.head, args.arm_check = args.real_head, True
        if args.head and not args.left_stowed:
            ap.error("--real --head needs --left-stowed (only the right arm is run)")
        arm_domain = args.robot_domain
    else:
        if args.sim_domain == args.robot_domain:
            ap.error("--sim-domain must differ from the robot's domain: without --real the moves go to the simulation")
        arm_domain = args.sim_domain

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from control_msgs.action import FollowJointTrajectory
    from opendubs_arm_interfaces.action import ArmCommand
    from rclpy.action import ActionClient
    from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
    from rclpy.context import Context
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from robot_api_interfaces.msg import ResultCode
    from robot_api_interfaces.srv import RobotCommand, RobotQuery
    from sensor_msgs.msg import JointState
    from std_msgs.msg import String

    robot_ctx = Context()
    rclpy.init(context=robot_ctx, domain_id=args.robot_domain)
    node = Node("treat_services", context=robot_ctx)
    if arm_domain == args.robot_domain:
        arm_ctx, arm_node = robot_ctx, Node("treat_services_arm", context=robot_ctx)
    else:
        arm_ctx = Context()
        rclpy.init(context=arm_ctx, domain_id=arm_domain)
        arm_node = Node("treat_services_arm", context=arm_ctx)

    t = Treat(args, rclpy, node, arm_node, ResultCode)
    inputs = MutuallyExclusiveCallbackGroup()
    if not args.dummy:
        node.create_subscription(String, args.state_topic, t.on_state, 10, callback_group=inputs)
    node.create_subscription(JointState, "/joint_states", t.on_real_joints, 10, callback_group=inputs)
    arm_node.create_subscription(JointState, "/joint_states", t.on_arm_joints, 10)
    t.arm_client = ActionClient(arm_node, ArmCommand, "/right_arm/command")
    t.traj_client = ActionClient(arm_node, FollowJointTrajectory, "/right_controller/follow_joint_trajectory")
    g2s.spin_in_thread(rclpy, arm_node, arm_ctx)
    if args.real:
        threading.Thread(target=t.watch_permit, daemon=True).start()
    if args.head:
        host = args.neck_host or neck_host_from_env()
        if not host:
            raise SystemExit("no neck host: set OPENDUBS_HEAD_NECK_HOST or --neck-host, or run with --no-head")
        t.head = Head(HeadTilt(host, None, args, t.arms_clear), HeadLaw(HeadParams(pan=args.head_pan, aim_deg=args.head_aim_deg)), lambda: "watch")
        threading.Thread(target=t.head.run, daemon=True).start()

    group = ReentrantCallbackGroup()

    def serve(name, fn, motion=False, exclusive=True):
        def callback(request, response):
            try:
                req = json.loads(request.req) if request.req.strip() else {}
                if not isinstance(req, dict):
                    raise ValueError("not a JSON object")
            except ValueError as error:
                response.result_code.result_code, response.result_code.message = ResultCode.INVALID_INPUT, f"bad JSON: {error}"
                response.data = "{}"
                return response
            try:
                if exclusive:
                    t.take(name)
                try:
                    message, data = fn(req)
                finally:
                    if exclusive:
                        t.give()
                response.result_code.result_code, response.result_code.message = ResultCode.SUCCESS, message
                response.data = json.dumps(data)
            except Refused as refused:
                response.result_code.result_code, response.result_code.message = refused.code, str(refused)
                response.data = json.dumps({"stopped": t.stopped})
            except Exception as error:  # noqa: BLE001 -- the node stays up; the arm holds by its own rules
                response.result_code.result_code, response.result_code.message = ResultCode.FAILURE, f"{type(error).__name__}: {error}"
                response.data = "{}"
            print(f"{name} {request.req or '{}'} -> {response.result_code.result_code}: {response.result_code.message}", flush=True)
            return response
        node.create_service(RobotCommand, f"/perception/treat/{name}", callback, callback_group=group)

    for name, fn in (("ready", t.ready), ("move_pose", t.move_pose), ("move_joints", t.move_joints),
                     ("centre_person", t.centre_person), ("get_hand", t.get_hand), ("find_hand", t.find_hand),
                     ("plan_treat", t.plan_treat), ("hold", t.hold),
                     ("gripper_open", t.gripper_open), ("gripper_close", t.gripper_close)):
        serve(name, fn)
    serve("stop", t.stop, exclusive=False)
    serve("rearm", t.rearm, exclusive=False)

    def on_status(_request, response):
        response.result_code.result_code, response.result_code.message = ResultCode.SUCCESS, "ok"
        response.data = json.dumps(t.status())
        return response

    node.create_service(RobotQuery, "/perception/treat/status", on_status, callback_group=group)
    executor = MultiThreadedExecutor(num_threads=6, context=robot_ctx)
    executor.add_node(node)
    print(f"treat services up: /perception/treat/{{ready,move_pose,move_joints,centre_person,get_hand,find_hand,"
          f"plan_treat,hold,gripper_open,gripper_close,stop,rearm,status}}; moves to {'THE REAL ARM' if args.real else f'the simulation (domain {arm_domain})'}; "
          f"head {'on' if t.head else 'off'}{'; dummy person' if args.dummy else ''}", flush=True)
    try:
        executor.spin()
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        if t.goal is not None:
            t.goal.cancel_goal_async()
        if t.head is not None:
            t.head.stop()
        g2s.shutdown_all(rclpy)


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    sys.exit(main())
