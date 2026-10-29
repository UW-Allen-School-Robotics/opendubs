"""The gafro backend: ARCHITECT's primitives on tobi's arm service (software/openarm/gafro, one
`arm_service.py run` per arm), one ROS action goal per arm or gripper call, as the program runs.

    OPENDUBS_ARCHITECT_BACKEND=gafro   (config.py; dev.sh gafro)

WHAT EACH CALL BECOMES
  move_ee_to_pose        planned on the twin from the arm's measured joints, as the recorded candy show's
                         moves were (live_arms.Planner: the IK, with "approach" the fingers held along it
                         by axis_ik.py; the joints kept the validator's margin inside their limits; the
                         whole move at least 30 mm from the robot's own body), then sent as ONE smoothed
                         FollowJointTrajectory goal on the arm service's /<side>_controller/
                         follow_joint_trajectory, at live_arms' speeds (50 deg/s at most, under the
                         service's 1.0 rad/s), and its arrival measured. Not the service's `reach_pose`:
                         without ssik (not in the robot's gafro environment) its planner has no posture
                         to start from, and a move from `ready` out to the side "leaves a joint's range"
                         (the simulation, 2026-10-28). The twin and tobi's model are the same arm: his
                         arm frame (openarm_body_link0) is the chassis frame moved by (0.423, 0, 0.3575) m, not
                         turned, and his tool frame is the twin's ee_site (fitted to 0.0 mm and 0.0 deg
                         over five poses, 2026-10-28); the signed degrees are his model's.
  move_arm_to_named_pose `ready`, the arm service's working pose. Stow is the operator's park (the
                         service's `ctl park`, which the dashboard's user cannot reach): not offered here.
  reset_robot            `ready` on each arm this backend serves, then the head's hold released.
  set_gripper_width      control_msgs GripperCommand on /<side>_gripper_controller/gripper_cmd: the
                         finger opening in metres (opening x FINGER_TRAVEL_M). A close that stops on
                         something is a grip, not a failure.
  set_head_pose          the dashboard's head hold (POST /api/head/hold {pan, tilt}: the neck slews there
                         and stays, clamped to its limits), then waits for /head/joint_states to settle.
  get_hand_pose          Archit's hand tracker (/perception/hand/state, palms in the colour camera's
                         optical frame) into the chassis frame with the head's live angles
                         (camera_frames.py); stable once STABLE_FRAMES palms in a row lie within STILL_M.
  place_in_hand          a stable hand, then a move to STANDOFF_M short of it (inside the workspace box),
                         the fingers pointing down (OVER_PALM) so the candy drops into the palm; a pause;
                         open; `ready`.
  wait                   a sleep, cut short by a stop.

GATES. The arm service gates every goal itself: CH5, the e-stop and the deadman (CH6), and on a real bus
`supervised` (a person at the e-stop). On top of that, each goal here is sent only with the deadman held
(the dashboard's robot state), and the moment it is released, or the run is stopped, the goal is
cancelled, the arm holds where it is, and the program is refused: nothing more is sent. The service's
collision check knows the robot's own arms and body but not the head, the camera mount or the bucket:
a program for this backend is checked on the twin with them (README).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

from opendubs_architect import api
from opendubs_architect.client import MotionRefused

FINGER_TRAVEL_M = 0.044               # the model's finger joint range (gafro/gripper.py TRAVEL)
SERVO_SPAN = 300.0 / 180.0            # real degrees per commanded neck degree (neck_state.py SERVO_SPAN_DEG)
HEAD_SETTLED_DEG = 1.5                # the head is there when its angles are this near where it is going
HEAD_SETTLE_S = 6.0
HAND_TOPIC = "/perception/hand/state"
STABLE_FRAMES, STILL_M = 5, 0.05      # treat_reach.py's --stable-frames and --still-m
HAND_MIN_Z = 0.70                     # m, chassis: lower than this is a hand hanging down, not held out
STANDOFF_M = 0.20                     # short of the palm, toward the robot (treat_reach.py --real's)
TREAT_S = 1.5                         # held at the hand before the gripper opens
GOAL_ACCEPT_S = 10.0
GOAL_RESULT_S = 90.0
POLL_S = 0.1
#: The fingers' direction over a palm, tried in turn until one is reachable: the pick's own (20 deg off
#: vertical, toward the person and the robot's left), 40 deg, then none (the posture nearest the arm's):
#: a hand at shoulder height is out of reach with the fingers straight down (the simulation, 2026-10-28).
OVER_PALM = ((0.242, 0.242, -0.940), (0.454, 0.454, -0.766), None)
COMMAND_SUCCESS = 0


def neck_command(pan_deg: float, tilt_deg: float) -> dict:
    """The neck's commanded degrees for a head pose in the API's signs (pan + left, tilt + up, real
    degrees). The neck's: 90 is centre, pan's rise to the left, tilt's rise as the face tips up."""
    return {"pan": round(90.0 + pan_deg / SERVO_SPAN, 1), "tilt": round(90.0 + tilt_deg / SERVO_SPAN, 1)}


class HandWatch:
    """The newest palm, and whether the last STABLE_FRAMES of them lie within STILL_M of their mean."""

    def __init__(self):
        self.recent, self.lock = [], threading.Lock()

    def add(self, xyz) -> None:
        with self.lock:
            self.recent = (self.recent + [(time.monotonic(), tuple(float(v) for v in xyz))])[-STABLE_FRAMES:]

    def clear(self) -> None:
        with self.lock:
            self.recent = []

    def hand(self, fresh_s: float = 0.5):
        """(xyz, stable) of the newest palm, or None if none is fresh."""
        with self.lock:
            recent = list(self.recent)
        if not recent or time.monotonic() - recent[-1][0] > fresh_s:
            return None
        pts = [p for _, p in recent]
        mean = [sum(c) / len(pts) for c in zip(*pts)]
        still = len(pts) == STABLE_FRAMES and all(math.dist(p, mean) <= STILL_M for p in pts)
        return (mean if still else list(pts[-1])), still


def pick_palm(state: dict):
    """The palm to follow in one hand tracker message (optical frame), or None: the followable one
    nearest the camera."""
    palms = [d for d in (state.get("palms") or {}).values() if isinstance(d, dict) and d.get("followable") and d.get("xyz")]
    return min((d["xyz"] for d in palms), key=lambda v: v[2], default=None)


class GafroRobot:
    """The primitives. `ros` carries the ROS calls (GafroRos on the robot; a fake in the tests);
    `deadman_held()` and `stopped()` are read before and while every goal runs."""

    def __init__(self, ros, config, planner=None, deadman_held=lambda: True, stopped=lambda: False,
                 head=None, frames=None, sides=("right",), speed=None, log=None, sim_gripper=False):
        self.ros, self.config, self.planner = ros, config, planner
        #: A SIMULATED arm service's gripper does not run (gafro/README.md: the model's finger actuator is
        #: not a joint transmission), so in a simulation the gripper calls are logged and move nothing.
        self.sim_gripper = sim_gripper
        self.deadman_held, self.stopped = deadman_held, stopped
        self.head, self.frames, self.sides, self.speed = head, frames, tuple(sides), speed
        self.log = log or (lambda ev: None)
        self.hands = HandWatch()

    # ---- the gate around every goal -------------------------------------------------------------
    def _permit(self, what: str) -> None:
        if self.stopped():
            raise MotionRefused(f"{what}: the run was stopped")
        try:
            held = bool(self.deadman_held())
        except Exception as e:  # noqa: BLE001  unknown is not held
            raise MotionRefused(f"{what}: the deadman cannot be read ({e})") from None
        if not held:
            raise MotionRefused(f"{what}: the deadman is not held")

    def _run_goal(self, what: str, send):
        """`send()` -> a handle with .done(), .result() and .cancel(). Sent with the deadman held; cancelled,
        and refused, the moment it is released or the run is stopped."""
        self._permit(what)
        handle = send()
        end = time.monotonic() + GOAL_RESULT_S
        while not handle.done():
            try:
                self._permit(what)
            except MotionRefused:
                handle.cancel()
                self.log({"event": "goal_cancelled", "what": what})
                raise
            if time.monotonic() > end:
                handle.cancel()
                raise MotionRefused(f"{what}: no result in {GOAL_RESULT_S:.0f} s (cancelled; the arm holds)")
            time.sleep(POLL_S)
        return handle.result()

    def _side(self, arm: str) -> str:
        arm = api.check_arm(arm)
        if arm not in self.sides:
            raise MotionRefused(f"the {arm} arm is not run by this backend (it runs: {', '.join(self.sides)})")
        return arm

    def _command(self, side: str, name: str, params: dict, what: str):
        res = self._run_goal(what, lambda: self.ros.command(side, name, json.dumps(params)))
        if res.result_code != COMMAND_SUCCESS:
            raise MotionRefused(f"{what}: {res.message}")
        self.log({"event": "arm_command", "side": side, "name": name, "params": params, "result": res.message})
        return res

    # ---- arm and gripper ------------------------------------------------------------------------
    def _in_workspace(self, side: str, point) -> None:
        box = self.config.workspace.get(side)
        if not box:
            raise MotionRefused(f"no workspace is configured for the {side} arm")
        for axis, v in zip("xyz", point):
            lo, hi = box[axis]
            if not lo <= v <= hi:
                raise MotionRefused(f"{axis} = {v:.3f} m is outside the {side} arm's workspace {lo:.2f}..{hi:.2f}")

    def _pose(self) -> dict:
        """The arms as they stand, as the twin's pose (live_arms: {slot: 7 signed degrees})."""
        return live_arms_pose(self.ros.joints_deg(), self.sides)

    def move_ee_to_pose(self, arm: str, target_pose: dict) -> bool:
        side = self._side(arm)
        p = api.check_pose(target_pose)["position"]
        point = [float(p[k]) for k in "xyz"]
        self._in_workspace(side, point)
        if self.planner is None:
            raise MotionRefused("a move needs the twin (no planner)")
        from opendubs_architect import live_arms
        approach = api.approach_of(target_pose)
        try:
            move = (self.planner.plan(side, self._pose(), point=point, approach=approach) if approach is not None
                    else self.planner.plan(side, self._pose(), point=point))
        except live_arms.Refused as e:
            raise MotionRefused(f"move_ee_to_pose {point}: {e}") from None
        self._follow(move, f"move_ee_to_pose {point}")
        return True

    def _follow(self, move, what: str) -> None:
        """A planned move (live_arms.Move, checked on the twin) as one FollowJointTrajectory goal on the arm
        service's /<side>_controller/follow_joint_trajectory, then its arrival measured."""
        from opendubs_architect import live_arms
        points = move.points()
        res = self._run_goal(what, lambda: self.ros.trajectory(move.side, points))
        if getattr(res, "error_code", 0) != 0:
            raise MotionRefused(f"{what}: the arm service ended it ({res.error_code}: {res.error_string})")
        now = self._pose()[live_arms.ARM_SLOT[move.side]]
        off = max(abs(a - b) for a, b in zip(now, move.goal))
        if off > live_arms.ARRIVAL_TOL_DEG:
            raise MotionRefused(f"{what}: finished {off:.1f} deg from its goal (at most {live_arms.ARRIVAL_TOL_DEG:g})")
        self.log({"event": "arm_move", "side": move.side, "what": what, "seconds": move.seconds,
                  "clearance_mm": move.clearance_mm, "goal_signed_deg": [round(v, 1) for v in move.goal]})

    def move_ee_to_rel_pose(self, arm: str, delta: dict) -> bool:
        side = self._side(arm)
        d = api.check_delta(delta)
        here = self.get_current_ee_pose()[side]["position"]
        return self.move_ee_to_pose(side, {"position": {k: here[k] + float(d.get(k, 0.0)) for k in "xyz"},
                                           "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}})

    def move_arm_to_named_pose(self, arm: str, name: str) -> bool:
        side = self._side(arm)
        if name != "ready":
            raise MotionRefused(f"on the arm service the named pose is 'ready' (not {name!r}): stow is the operator's park")
        self._command(side, "ready", {}, f"{side} to ready")
        return True

    def reset_robot(self) -> bool:
        for side in self.sides:
            self._command(side, "ready", {}, f"{side} to ready")
        self.release_head()
        return True

    def set_gripper_width(self, arm: str, opening: float) -> bool:
        side = self._side(arm)
        metres = round(api.check_opening(opening) * FINGER_TRAVEL_M, 4)
        if self.sim_gripper:
            self._permit(f"{side} gripper to {opening:.2f}")
            self.log({"event": "gripper_not_moved", "side": side, "would_go_to_m": metres})
            return True
        res = self._run_goal(f"{side} gripper to {opening:.2f}", lambda: self.ros.grip(side, metres))
        if not (res.reached_goal or res.stalled):        # a close that stops on something is a grip
            raise MotionRefused(f"the {side} gripper did not get to {opening:.2f} (it is at {res.position:.4f} m)")
        self.log({"event": "gripper", "side": side, "goal_m": metres, "at_m": round(res.position, 4),
                  "stalled": bool(res.stalled)})
        return True

    def wait(self, seconds: float) -> bool:
        end = time.monotonic() + api.check_wait(seconds)
        while time.monotonic() < end:
            if self.stopped():
                raise MotionRefused("wait: the run was stopped")
            time.sleep(min(POLL_S, max(0.0, end - time.monotonic())))
        return True

    # ---- the head -------------------------------------------------------------------------------
    def set_head_pose(self, pan_deg: float, tilt_deg: float) -> bool:
        if self.head is None:
            raise MotionRefused("no head on this backend")
        self.head.hold(neck_command(float(pan_deg), float(tilt_deg)))
        self.head.settle()
        self._plan_with_head(float(pan_deg), float(tilt_deg))
        return True

    def _plan_with_head(self, pan_deg: float, tilt_deg: float) -> None:
        """Every move from here is checked on the twin with the head where it now is (the description's
        signs: tilt + down), not centred: tipped 30 deg down at the bucket, the right shoulder link passed
        29.1 mm from it, under the 30 mm a move must keep (the simulation, 2026-10-28)."""
        if self.planner is not None:
            self.planner.config.head = [(pan_deg, -tilt_deg)]

    def release_head(self) -> None:
        self._plan_with_head(0.0, 0.0)
        if self.head is not None:
            try:
                self.head.release()
            except Exception as e:  # noqa: BLE001  the hold lapses with the next one anyway
                self.log({"event": "head_release_failed", "error": str(e)})

    # ---- the hand -------------------------------------------------------------------------------
    def on_hand_state(self, state: dict) -> None:
        """One hand tracker message (HAND_TOPIC): its palm into the chassis frame, if one is followable."""
        xyz = pick_palm(state)
        if xyz is None or self.frames is None:
            return
        try:
            T = self.frames()
        except Exception as e:  # noqa: BLE001  the head's angle unknown: the palm cannot be placed
            self.log({"event": "hand_unplaced", "why": str(e)})
            return
        p = [sum(T[i][j] * float(xyz[j]) for j in range(3)) + T[i][3] for i in range(3)]
        if p[2] >= HAND_MIN_Z:
            self.hands.add(p)

    def get_hand_pose(self) -> dict:
        got = self.hands.hand()
        if got is None:
            return {"hand": None}
        xyz, stable = got
        return {"hand": {"position": dict(zip("xyz", (round(v, 4) for v in xyz))),
                         "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}, "stable": bool(stable)}}

    def place_in_hand(self, arm: str, timeout_s: float = 15.0) -> dict:
        side = self._side(arm)
        self.hands.clear()
        end = time.monotonic() + float(timeout_s)
        got = None
        while time.monotonic() < end:
            if self.stopped():
                raise MotionRefused("place_in_hand: the run was stopped")
            h = self.get_hand_pose()["hand"]
            if h and h["stable"]:
                got = [h["position"][k] for k in "xyz"]
                break
            time.sleep(POLL_S)
        if got is None:
            self.move_arm_to_named_pose(side, "ready")
            return {"released": False, "reason": f"no stable hand held out in {timeout_s:.0f} s"}
        box = self.config.workspace[side]
        goal = [got[0] - STANDOFF_M, got[1], got[2]]
        goal = [min(max(v, box[a][0]), box[a][1]) for v, a in zip(goal, "xyz")]
        self.log({"event": "hand", "side": side, "palm": [round(v, 3) for v in got], "goal": [round(v, 3) for v in goal]})
        refusals = []
        for approach in OVER_PALM:
            pose = {"position": dict(zip("xyz", goal)), "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}
            if approach is not None:
                pose["approach"] = dict(zip("xyz", approach))
            try:
                self.move_ee_to_pose(side, pose)
                break
            except MotionRefused as e:
                if self.stopped() or "OUT_OF_REACH" not in str(e):
                    raise                            # a stop, the deadman, or a move that went wrong: no retry
                refusals.append(str(e))
        else:
            self.move_arm_to_named_pose(side, "ready")
            return {"released": False, "reason": "the hand held out is out of reach: " + refusals[-1][:160]}
        self.wait(TREAT_S)
        self.set_gripper_width(side, 1.0)
        self.move_arm_to_named_pose(side, "ready")
        return {"released": True, "reason": f"placed {STANDOFF_M * 100:.0f} cm short of the palm at "
                                            f"{[round(v, 2) for v in got]}"}

    # ---- what the arm knows ---------------------------------------------------------------------
    def get_current_joints(self) -> dict:
        q = self.ros.joints_deg()
        return {side: {f"joint{j}": {"position": math.radians(q[side][j - 1]), "velocity": 0.0, "effort": None}
                       for j in range(1, 8)} for side in self.sides if side in q}

    def get_current_ee_pose(self) -> dict:
        if self.planner is None:
            raise MotionRefused("the hand's pose needs the twin (no planner)")
        pose = live_arms_pose(self.ros.joints_deg(), self.sides)
        out = {}
        for side in self.sides:
            pos, quat = self.planner.tool(side, pose)
            out[side] = {"position": dict(zip("xyz", pos)), "orientation": quat}
        return out

    def get_current_gripper_width(self) -> dict:
        f = self.ros.fingers_m()
        return {side: round(min(max(f[side] / FINGER_TRAVEL_M, 0.0), 1.0), 3) for side in self.sides if side in f}

    def get_workspace(self) -> dict:
        return {s: b for s, b in self.config.workspace.items() if s in self.sides}

    def get_named_poses(self) -> dict:
        return {"poses": ["ready"]}

    def __getattr__(self, name):
        if name in api.PRIMITIVE_NAMES:
            def refused(*args, **kwargs):
                raise MotionRefused(f"{name} is not offered on the gafro backend")
            return refused
        raise AttributeError(name)


def live_arms_pose(joints_deg: dict, sides) -> dict:
    """The twin's pose (live_arms: {slot: 7 signed degrees}) for the arms' measured joints (model
    degrees), every other arm at zero."""
    from opendubs_architect import live_arms
    pose = {slot: [0.0] * 7 for slot in live_arms.ARM_SLOT.values()}
    for side in sides:
        if side in joints_deg:
            pose[live_arms.ARM_SLOT[side]] = live_arms.to_signed(side, model_to_outward(side, joints_deg[side]))
    return pose


def live_arms_pose_from_kin(kin, sides) -> dict:
    """The twin's pose as its arms stand now (after a solve), in live_arms' signed degrees."""
    from opendubs_architect import live_arms
    pose = {slot: [0.0] * 7 for slot in live_arms.ARM_SLOT.values()}
    for side in live_arms.SIDES:
        q = [math.degrees(kin.d.qpos[kin.m.jnt_qposadr[kin._jid(side, j)]]) for j in range(1, 8)]
        pose[live_arms.ARM_SLOT[side]] = live_arms.to_signed(side, model_to_outward(side, q))
    return pose


def model_to_outward(side: str, q_deg) -> list:
    """The model's joint degrees as the twin's outward ones (sim/scene.py mirrors the left's joints 1-3)."""
    import cue
    return [(-v if side == "left" and j in cue.MIRRORED_JOINTS else v) for j, v in enumerate(q_deg, 1)]


class DashboardHead:
    """The head through the dashboard's hold (neck_state.py: POST /api/head/hold), settled on its own
    published angles (/head/joint_states against /head/joint_targets)."""

    def __init__(self, dashboard: str, ros):
        self.url, self.ros = dashboard.rstrip("/"), ros

    def _post(self, body: dict) -> None:
        import urllib.request
        req = urllib.request.Request(self.url + "/api/head/hold", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3.0) as r:
                r.read()
        except Exception as e:  # noqa: BLE001
            raise MotionRefused(f"the head did not take the hold ({e})") from None

    def hold(self, command: dict) -> None:
        self._post(command)

    def release(self) -> None:
        self._post({"op": "off"})

    def settle(self) -> None:
        end = time.monotonic() + HEAD_SETTLE_S
        while time.monotonic() < end:
            now, target = self.ros.head_now(), self.ros.head_target()
            if now and target and all(abs(now[k] - target[k]) <= HEAD_SETTLED_DEG for k in ("pan", "tilt")):
                return
            time.sleep(POLL_S)
        raise MotionRefused(f"the head did not settle in {HEAD_SETTLE_S:.0f} s")


class LoggedHead:
    """A head that moves nothing: each pose is logged and counts as reached at once. For a SIMULATED arm
    service, which has no neck (dev.sh gafro-sim)."""

    def __init__(self, log):
        self.log = log

    def hold(self, command: dict) -> None:
        self.log({"event": "head_not_moved", "would_hold": command})

    def release(self) -> None:
        self.log({"event": "head_not_moved", "would_release": True})

    def settle(self) -> None:
        pass


class _Goal:
    """An rclpy action goal as _run_goal wants it."""

    def __init__(self, client, goal, accept_s=GOAL_ACCEPT_S):
        sent = client.send_goal_async(goal)
        end = time.monotonic() + accept_s
        while not sent.done():
            if time.monotonic() > end:
                raise MotionRefused("the arm service did not answer the goal")
            time.sleep(0.02)
        self.handle = sent.result()
        if self.handle is None or not self.handle.accepted:
            raise MotionRefused("the arm service rejected the goal (another move running, or not active)")
        self.res = self.handle.get_result_async()

    def done(self) -> bool:
        return self.res.done()

    def result(self):
        return self.res.result().result

    def cancel(self) -> None:
        self.handle.cancel_goal_async()
        time.sleep(0.3)


class GafroRos:
    """The ROS side on the robot's domain: the arm service's two actions per arm, /joint_states, the
    head's angles and the hand tracker. Its own context and executor thread."""

    def __init__(self, sides=("right",), on_hand=None):
        import rclpy
        from control_msgs.action import FollowJointTrajectory, GripperCommand
        from opendubs_arm_interfaces.action import ArmCommand
        from rclpy.action import ActionClient
        from rclpy.context import Context
        from rclpy.executors import MultiThreadedExecutor
        from sensor_msgs.msg import JointState
        from std_msgs.msg import String
        self.ArmCommand, self.GripperCommand, self.FollowJointTrajectory = ArmCommand, GripperCommand, FollowJointTrajectory
        self.ctx = Context()
        rclpy.init(context=self.ctx, domain_id=int(os.environ.get("ROS_DOMAIN_ID", "0")))
        self.node = rclpy.create_node("architect_gafro", context=self.ctx)
        self.cmd = {s: ActionClient(self.node, ArmCommand, f"/{s}_arm/command") for s in sides}
        self.grip_c = {s: ActionClient(self.node, GripperCommand, f"/{s}_gripper_controller/gripper_cmd") for s in sides}
        self.traj = {s: ActionClient(self.node, FollowJointTrajectory, f"/{s}_controller/follow_joint_trajectory")
                     for s in sides}
        self.lock, self.q, self.head = threading.Lock(), {}, {}
        self.callback_errors = 0
        sub = self.node.create_subscription
        sub(JointState, "/joint_states", self._safe(self._on_joints), 20)
        sub(JointState, "/head/joint_states", self._safe(lambda m: self._on_head(m, "now")), 10)
        sub(JointState, "/head/joint_targets", self._safe(lambda m: self._on_head(m, "target")), 10)
        if on_hand is not None:
            sub(String, HAND_TOPIC, self._safe(lambda m: on_hand(json.loads(m.data))), 10)
        ex = MultiThreadedExecutor(context=self.ctx)
        ex.add_node(self.node)
        self.spinner = threading.Thread(target=ex.spin, daemon=True)
        self.spinner.start()

    def _safe(self, fn):
        """A callback that never raises: an exception escaping one ends the executor's thread, and with it
        every action reply (found in the simulation, 2026-10-28: a palm before the robot existed)."""
        def call(msg):
            try:
                fn(msg)
            except Exception:  # noqa: BLE001
                self.callback_errors += 1
        return call

    def alive(self) -> bool:
        return self.spinner.is_alive()

    def wait_for_servers(self, timeout_s: float = 10.0) -> list:
        """The actions not found within `timeout_s`."""
        return [n for c, n in [(c, f"/{s}_arm/command") for s, c in self.cmd.items()]
                + [(c, f"/{s}_gripper_controller/gripper_cmd") for s, c in self.grip_c.items()]
                + [(c, f"/{s}_controller/follow_joint_trajectory") for s, c in self.traj.items()]
                if not c.wait_for_server(timeout_sec=timeout_s)]

    def _on_joints(self, msg) -> None:
        with self.lock:
            for n, p in zip(msg.name, msg.position):
                self.q[n] = (time.monotonic(), p)

    def _on_head(self, msg, which) -> None:
        got = dict(zip(msg.name, msg.position))
        if "head_pan" in got and "head_tilt" in got:
            with self.lock:
                self.head[which] = (time.monotonic(), {"pan": math.degrees(got["head_pan"]),
                                                       "tilt": math.degrees(got["head_tilt"])})

    def _fresh(self, name, stale_s=1.0):
        v = self.q.get(name)
        return None if v is None or time.monotonic() - v[0] > stale_s else v[1]

    def joints_deg(self) -> dict:
        out = {}
        with self.lock:
            for side in self.cmd:
                q = [self._fresh(f"openarm_{side}_joint{j}") for j in range(1, 8)]
                if all(v is not None for v in q):
                    out[side] = [math.degrees(v) for v in q]
        if set(out) != set(self.cmd):
            raise MotionRefused("the arm's joints are not known (nothing fresh on /joint_states)")
        return out

    def fingers_m(self) -> dict:
        with self.lock:
            return {s: v for s in self.cmd if (v := self._fresh(f"openarm_{s}_finger_joint1")) is not None}

    def _head(self, which, stale_s=0.5):
        with self.lock:
            v = self.head.get(which)
        return None if v is None or time.monotonic() - v[0] > stale_s else v[1]

    def head_now(self):
        return self._head("now")

    def head_target(self):
        return self._head("target")

    def trajectory(self, side: str, points):
        """points: [(seconds from now, [7 signed degrees])] (arm_output.track_points), the model's radians."""
        if not self.alive():
            raise MotionRefused("the ROS executor has stopped: no goal can be followed")
        from builtin_interfaces.msg import Duration
        from trajectory_msgs.msg import JointTrajectoryPoint
        g = self.FollowJointTrajectory.Goal()
        g.trajectory.joint_names = [f"openarm_{side}_joint{j}" for j in range(1, 8)]
        for t, deg in points:
            pt = JointTrajectoryPoint()
            pt.positions = [math.radians(d) for d in deg]
            sec = int(t)
            pt.time_from_start = Duration(sec=sec, nanosec=int(round((t - sec) * 1e9)))
            g.trajectory.points.append(pt)
        return _Goal(self.traj[side], g)

    def command(self, side: str, name: str, params: str):
        if not self.alive():
            raise MotionRefused("the ROS executor has stopped: no goal can be followed")
        return _Goal(self.cmd[side], self.ArmCommand.Goal(name=name, params=params))

    def grip(self, side: str, metres: float):
        if not self.alive():
            raise MotionRefused("the ROS executor has stopped: no goal can be followed")
        g = self.GripperCommand.Goal()
        g.command.position, g.command.max_effort = float(metres), 0.0
        return _Goal(self.grip_c[side], g)


def make(config, deadman_held, stopped, dashboard: str = "http://127.0.0.1:8080", sides=("right",),
         head: str = "dashboard", log=None, sim_gripper: bool = False):
    """`head`: "dashboard" (the neck, through the dashboard's hold) or "log" (nothing moves: a simulation)."""
    """The robot as the program sees it on this backend, with everything it needs on the robot."""
    import cue
    from opendubs_architect import live_arms
    from opendubs_architect.perception import Perception
    from opendubs_architect.show_backend import _limits_path
    robot = None

    def on_hand(state):
        if robot is not None:                    # palms before the robot exists are dropped
            robot.on_hand_state(state)
    ros = GafroRos(sides, on_hand=on_hand)
    missing = ros.wait_for_servers()
    if missing:
        raise SystemExit(f"gafro backend: the arm service does not serve {', '.join(missing)} on ROS domain "
                         f"{os.environ.get('ROS_DOMAIN_ID', '0')} (is arm_service.py run up for {', '.join(sides)}?)")
    planner = live_arms.Planner(cue.load_limits(_limits_path()), config)
    perception = Perception.from_arm_config()

    def frames():
        h = ros.head_now()
        if h is None:
            raise RuntimeError("the head's angle is unknown (nothing fresh on /head/joint_states)")
        return perception.turned(h["pan"], h["tilt"]).T.tolist()

    heads = {"dashboard": lambda: DashboardHead(dashboard, ros), "log": lambda: LoggedHead(log or print)}
    robot = GafroRobot(ros, config, planner, deadman_held, stopped, head=heads[head](), frames=frames,
                       sides=sides, log=log, sim_gripper=sim_gripper)
    return robot
