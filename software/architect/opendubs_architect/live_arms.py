"""Live arm motion for ARCHITECT on OpenDubs: one checked move at a time, behind the runner's gates.

A generated program asks for a move: a point for the gripper, a step from where it is, a named pose,
a gripper opening. Each request becomes ONE move, checked here before anything is sent and watched
while it runs.

  BEFORE. The gates the show runner applies (runner.preflight): the e-stop clear, no fault, CH5 in
  AUTO, the arms active, and the deadman HELD. Held now, at the request; this is not the runner's
  fresh press after arming. Then the target must lie inside the arm's workspace box, which comes from
  this robot's config (ArmConfig), never from the program. The twin's IK (ik.Kin, the solver shows
  use, inside each joint's usable range and the validator's shoulder rules) must reach it within
  ik.REACH_TOL_M. And the whole move, from where the arm is MEASURED to be, must stay
  ik.CLEAR_MIN_MM from the robot's body and the other arm (ik.Kin.clearance_along).
  WHILE. runner.monitor every TICK_S: the e-stop, the deadman, a fault, CH5. The moment one fails, or
  stops being KNOWN, every goal is cancelled and the controllers hold the arms where they are.
  AFTER. The arm must be measured where it was sent.

TIMING. No joint goes faster than LIVE_SPEED_FRACTION of its measured speed cap (cue.py), nor than
MAX_DEG_S, at the move's peak, along motion.py's smoothed path, sent as one FollowJointTrajectory goal through
arm_output, the runner's own output code.

A refusal raises Refused(code, reason): the robot service answers REFUSED and the program raises
MotionRefused and stops. A move that was sent and then went wrong raises MoveFailed.

THE FALLBACK. ShowRecorder plans exactly the same moves and writes each one as a cue instead of
sending it, so an approved program can become a show the runner plays (show_backend.py). A move that
passes here passes there.

NOT BUILT YET, and why live motion is not yet allowed on the robot:
  * The arm lease (plan 5.3). Nothing stops the runner and this service from sending goals at once.
    Until the lease exists, run one or the other, never both; the Arms and Choreography lanes sign off
    on a second motion source first.
  * The head's pan/tilt and the lift height are not in the robot state the dashboard serves. So the
    clearance check assumes the config's head poses (default: centred) and lift (default: down).
  * Orientation. The twin's IK places the gripper's tool point and, when asked, the direction its
    fingers point (axis_ik.py, e.g. straight down into a bucket); the roll about that axis is free.
  * The twin is about 6 degrees optimistic at the shoulder (sim/clearance.py). The shoulder rules in
    cue.py, from measured constants, are what keep the arm off the head, and the IK keeps to them.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "software" / "choreo", _REPO / "sim"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import arm_output  # noqa: E402  the runner's arm output: the only code that sends arm goals
import cue  # noqa: E402
import runner  # noqa: E402  its gates: preflight and monitor

from opendubs_architect import axis_ik  # noqa: E402

#: What every move needs, beyond what runner.preflight checks for any run.
REQUIRES = {"rc_authority": "AUTO", "arms": "active"}
TICK_S = runner.TICK_S
ARM_ACK_S = runner.ARM_ACK_S
#: PLACEHOLDER: half the measured caps, with children in front of the robot. A show may use all of them.
LIVE_SPEED_FRACTION = 0.5
#: A ceiling for every joint, whatever its cap: the arm service on gafro (arms/gafro-layer2,
#: trajectory.Limits) refuses a path over 1.0 rad/s (57 deg/s), the driver's top speed on the arm
#: (2026-10-26). Half the caps let joints 3 to 7 reach 68 to 90 deg/s, and the bucket show's right arm
#: 1.04 rad/s; 50 deg/s keeps every leg 13% under the limit.
MAX_DEG_S = 50.0
MIN_MOVE_S = 0.5
#: A routed waypoint is kept this much further inside the margin than the validator asks: its cue is
#: written to one decimal, and rounding must not take it back out.
ROUND_DEG = 0.1
#: PLACEHOLDER: how far from its goal an arm may be measured once its goal reports success.
ARRIVAL_TOL_DEG = 3.0
#: How long after its planned end a move's goal may take to report.
RESULT_GRACE_S = 2.0
GRIP_TIMEOUT_S = 3.0
#: How long a recorded show gives a gripper cue before the next move.
GRIP_S = 1.5
#: FollowJointTrajectory and GripperCommand final statuses (action_msgs/GoalStatus).
SUCCEEDED, ABORTED = 4, 6

SIDES = ("left", "right")
ARM_SLOT = {"left": "ARM_L", "right": "ARM_R"}
GRIP_SLOT = {"left": "GRIP_L", "right": "GRIP_R"}
DEFAULT_CONFIG = "/etc/opendubs/architect-arms.yaml"

CONFIG_TEMPLATE = """\
# /etc/opendubs/architect-arms.yaml: this robot's arm config for ARCHITECT (live_arms.py).
# Measured on this robot, never committed. Metres in the chassis frame (x forward, y left, z up).
workspace:            # the box each gripper's tool point may be sent into; no box, no Cartesian moves
  # Proposed by tools/workspace_box.py from the twin (2026-10-23, test-fixture limits): every 5 cm
  # point clean from stow, every move between its corners and centre clean (closest 36.8 mm). In
  # front of the arm column and head, above the base, below the head. Re-run with this robot's limits.
  left:  {x: [0.60, 0.80], y: [0.15, 0.45], z: [0.75, 1.10]}
  right: {x: [0.60, 0.80], y: [-0.45, -0.15], z: [0.75, 1.10]}
named:                # joint poses by name, 7 outward degrees (the shows' sense); "stow" is always rest
  offer: {right: [20, 10, 0, 60, 0, 10, 0]}
head: [[0, 0]]        # the head poses (pan, tilt; degrees off centre) the clearance check assumes
lift_mm: 0            # the lift height the clearance check assumes
"""


class Refused(Exception):
    """A gate said no before or during a move. Nothing was sent, or what was sent was stopped."""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


class MoveFailed(RuntimeError):
    """A move was sent and did not end where it was sent: rejected, unfinished, or off its goal."""


def _sign(side: str, j: int) -> int:
    return -1 if side == "left" and j in cue.MIRRORED_JOINTS else 1


def to_outward(side: str, signed: list) -> list:
    """7 signed degrees (the controller's sense) as 7 outward degrees (the shows' sense)."""
    return [float(v) * _sign(side, j) for j, v in enumerate(signed, 1)]


def to_signed(side: str, outward: list) -> list:
    return to_outward(side, outward)            # the mirror is its own inverse


@dataclass
class ArmConfig:
    """What this robot allows, read from its own file (CONFIG_TEMPLATE), never from a program."""

    workspace: dict = field(default_factory=dict)
    named: dict = field(default_factory=dict)
    head: list = field(default_factory=lambda: [(0.0, 0.0)])
    lift_mm: float = 0.0

    @classmethod
    def load(cls, path: str | None = None) -> "ArmConfig":
        """The robot's config; an absent file is the empty config: stow only, no Cartesian moves."""
        import yaml
        path = path or os.environ.get("OPENDUBS_ARCHITECT_ARMS") or DEFAULT_CONFIG
        if not os.path.isfile(path):
            return cls()
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        cfg = cls(workspace=doc.get("workspace") or {}, named=doc.get("named") or {},
                  head=[tuple(map(float, h)) for h in doc.get("head") or [(0.0, 0.0)]],
                  lift_mm=float(doc.get("lift_mm") or 0.0))
        for name, pose in cfg.named.items():
            for side, joints in pose.items():
                if side not in SIDES or len(joints) != 7:
                    raise ValueError(f"{path}: named pose {name!r}: {side!r} needs 7 outward degrees")
        return cfg


@dataclass
class Move:
    """One checked move of one arm, in signed degrees."""

    side: str
    start: list
    goal: list
    seconds: float
    clearance_mm: float
    #: A routed move (routing.Router): the waypoints it passes on the way, in signed degrees, and the
    #: seconds of each leg (one more than the waypoints). Empty for a straight move.
    via: list = field(default_factory=list)
    legs: list = field(default_factory=list)

    def points(self) -> list:
        if self.via:                            # a routed move is recorded as a show, never sent live
            raise RuntimeError("a routed move is for a recorded show only; the live service sends straight moves")
        kf = {"start": 0.0, "t": self.seconds, "pose_signed": {str(j): self.goal[j - 1] for j in range(1, 8)}}
        return arm_output.track_points([kf], 0.0, self.start)


class Planner:
    """The twin, posed as the robot is, solving and checking one move at a time."""

    def __init__(self, limits: dict, config: ArmConfig, router=None):
        """`router` (routing.Router), for a recorded show only: a move refused as TOO_CLOSE is
        planned around the body instead, and every leg of the route checked as a straight move is."""
        import ik  # MuJoCo: imported here so the rest of this module loads without it
        self.ik, self.limits, self.config, self.router = ik, limits, config, router
        self.kin = ik.Kin(limits)

    def _pose(self, pose: dict, head) -> None:
        """Place both arms (pose: {slot: 7 signed degrees}), the head and the lift."""
        import clearance
        joints = ",".join(f"{side}:{j}={v}" for side in SIDES
                          for j, v in enumerate(to_outward(side, pose[ARM_SLOT[side]]), 1))
        clearance.set_pose(self.kin.m, self.kin.d, joints, self.config.lift_mm, head[0], head[1])

    def tool(self, side: str, pose: dict) -> tuple:
        """Where `side`'s tool point is, in the chassis frame: (position [x, y, z], quaternion {x, y, z, w})."""
        mj, np = self.kin.mj, self.kin.np
        self._pose(pose, self.config.head[0])
        site = mj.mj_name2id(self.kin.m, mj.mjtObj.mjOBJ_SITE, self.ik.TOOL_SITE.format(side=side))
        body = mj.mj_name2id(self.kin.m, mj.mjtObj.mjOBJ_BODY, "chassis")
        rot = self.kin.d.xmat[body].reshape(3, 3)
        pos = rot.T @ (self.kin.d.site_xpos[site] - self.kin.d.xpos[body])
        q = np.zeros(4)
        mj.mju_mat2Quat(q, (rot.T @ self.kin.d.site_xmat[site].reshape(3, 3)).flatten())
        return [round(float(v), 4) for v in pos], {"x": float(q[1]), "y": float(q[2]), "z": float(q[3]), "w": float(q[0])}

    def _usable(self, side: str, goal_signed: list, margin_deg: float) -> None:
        for j, v in enumerate(goal_signed, 1):
            lo, hi = self.limits[f"openarm_{side}_joint{j}"]
            lo_d, hi_d = math.degrees(lo) + margin_deg, math.degrees(hi) - margin_deg
            if not lo_d - 1e-6 <= v <= hi_d + 1e-6:
                raise Refused("JOINT_LIMIT", f"{side} J{j} would be {v:.1f} deg, outside its usable "
                                             f"{lo_d:.1f}..{hi_d:.1f}")

    def plan(self, side: str, pose: dict, point=None, goal_signed=None, margin_deg=cue.MARGIN_DEG,
             approach=None) -> Move:
        """A move of `side` from `pose` to a chassis-frame `point` (solved) or to `goal_signed`. The goal
        keeps `margin_deg` inside each joint's limits: the validator's margin, except for rest, which
        the validator takes exactly (cue.rest_pose). `approach`, with a point: the direction the fingers
        point at the goal (the tool's +z, chassis frame), held within axis_ik.AXIS_TOL_DEG."""
        np = self.kin.np
        start = [float(v) for v in pose[ARM_SLOT[side]]]
        if point is not None:
            self._pose(pose, self.config.head[0])
            goal = self.kin.point([float(v) for v in point], side)
            if approach is None:
                sol, err, _ = self.kin.solve(side, "reach", goal)
            else:
                mj = self.kin.mj
                chassis = self.kin.d.xmat[mj.mj_name2id(self.kin.m, mj.mjtObj.mjOBJ_BODY, "chassis")].reshape(3, 3)
                sol, err, miss_deg = axis_ik.solve(self.kin, side, goal, chassis @ np.array(axis_ik.unit(approach)),
                                                   self.ik.REACH_TOL_M)
                if err <= self.ik.REACH_TOL_M and miss_deg > axis_ik.AXIS_TOL_DEG:
                    raise Refused("OUT_OF_REACH", f"the {side} arm reaches {list(point)} but not with its fingers "
                                                  f"along {list(approach)}: its best is {miss_deg:.1f} deg off")
            if err > self.ik.REACH_TOL_M:
                raise Refused("OUT_OF_REACH", f"the {side} arm cannot reach {list(point)}"
                                              + (f" with its fingers along {list(approach)}" if approach is not None else "")
                                              + f": its best is {err * 100:.1f} cm off")
            goal_signed = to_signed(side, [sol[j] for j in range(1, 8)])
        goal = [round(float(v), 2) for v in goal_signed]
        self._usable(side, goal, margin_deg)
        worst = self._worst(side, pose, start, goal)
        if worst[0] >= self.ik.CLEAR_MIN_MM:
            return Move(side, start, goal, self._leg_seconds(start, goal), round(float(worst[0]), 1))
        refused = Refused("TOO_CLOSE", f"the move would bring the {side} arm within {worst[0]:.1f} mm "
                                       f"({worst[1]} / {worst[2]}); at least {self.ik.CLEAR_MIN_MM:.0f} mm")
        if self.router is None:
            raise refused
        via = self.router(side, start, goal)
        if not via:
            why = getattr(self.router, "last_error", None)
            raise Refused("TOO_CLOSE", f"{refused.reason}, and the planner found no way around"
                                       + (f" ({why})" if why else ""))
        # A waypoint is an ordinary cue to the validator, held cue.MARGIN_DEG inside every joint's limits
        # even on a move to rest (which alone it takes exactly): kept there, as the composer's bridge
        # keeps its own (on the twin a route back to rest passed J4 at +7.4 against a usable +7.9).
        chain = [start] + [self._inside(side, w) for w in via] + [goal]
        legs, closest = [], float("inf")
        for a, b in zip(chain, chain[1:]):
            self._usable(side, b, margin_deg if b is goal else cue.MARGIN_DEG)
            c = self._worst(side, pose, a, b)
            if c[0] < self.ik.CLEAR_MIN_MM:      # the route is held to the same check as a straight move
                raise Refused("TOO_CLOSE", f"{refused.reason}; the planner's route also comes within "
                                           f"{c[0]:.1f} mm ({c[1]} / {c[2]})")
            legs.append(self._leg_seconds(a, b))
            closest = min(closest, c[0])
        return Move(side, start, goal, round(sum(legs), 2), round(float(closest), 1),
                    via=[[round(v, 2) for v in w] for w in chain[1:-1]], legs=legs)

    def _worst(self, side: str, pose: dict, a: list, b: list) -> tuple:
        """The closest approach of `side` moving straight from `a` to `b`, over the config's head poses."""
        np = self.kin.np
        worst = (float("inf"), None, None)
        for head in self.config.head:
            self._pose(pose, head)
            c = self.kin.clearance_along(side, np.radians(a), np.radians(b))
            if c[0] < worst[0]:
                worst = c
        return worst

    def _inside(self, side: str, signed: list) -> list:
        """A waypoint moved inside each joint's usable range: cue.MARGIN_DEG in from the limits, and
        ROUND_DEG more, so the cue's one decimal cannot round it back out."""
        out, m = [], cue.MARGIN_DEG + ROUND_DEG
        for j, v in enumerate(signed, 1):
            lo, hi = self.limits[f"openarm_{side}_joint{j}"]
            out.append(min(math.degrees(hi) - m, max(math.degrees(lo) + m, float(v))))
        return out

    @staticmethod
    def _leg_seconds(a: list, b: list) -> float:
        need = max(abs(b[j - 1] - a[j - 1]) / min(cue.SPEED_CAP_DEG_S[j] * LIVE_SPEED_FRACTION, MAX_DEG_S)
                   for j in range(1, 8))
        return round(max(MIN_MOVE_S, need * cue.PEAK_FACTOR), 2)


def _check_side(side: str) -> str:
    if side not in SIDES:
        raise ValueError(f"arm must be 'left' or 'right', got {side!r}")
    return side


class _Arms:
    """The requests a program can make, planned the same way whatever carries them out."""

    def __init__(self, planner: Planner, config: ArmConfig, limits: dict):
        self.planner, self.config, self.limits = planner, config, limits

    # What a subclass provides: where the arms are, and how a checked move or grip is carried out
    def _pose(self) -> dict:
        raise NotImplementedError

    def _execute(self, move: Move) -> None:
        raise NotImplementedError

    def _grip(self, side: str, opening: float) -> None:
        raise NotImplementedError

    def _before(self) -> None:
        """Called before planning and again before carrying a move out."""

    def named_pose(self, side: str, name: str) -> list:
        """7 signed degrees for a named pose. "stow" is the shows' rest: every joint 0, inside the limits."""
        if name == "stow":
            rest = cue.rest_pose(self.limits, side)
            return to_signed(side, [rest[j] for j in range(1, 8)])
        pose = (self.config.named.get(name) or {}).get(side)
        if pose is None:
            have = ["stow"] + [n for n, p in self.config.named.items() if side in p]
            raise Refused("UNKNOWN_POSE", f"the {side} arm has no pose named {name!r} (it has: {', '.join(have)})")
        return to_signed(side, pose)

    def _in_workspace(self, side: str, point) -> None:
        box = self.config.workspace.get(side)
        if not box:
            raise Refused("NO_WORKSPACE", f"no workspace is configured for the {side} arm: "
                                          "it takes named poses only")
        for axis, v in zip("xyz", point):
            lo, hi = box[axis]
            if not lo <= v <= hi:
                raise Refused("WORKSPACE", f"{axis} = {v:.3f} m is outside the {side} arm's "
                                           f"workspace {lo:.2f}..{hi:.2f}")

    def _run(self, side: str, **target) -> Move:
        self._before()
        move = self.planner.plan(side, self._pose(), **target)
        self._before()                          # planning takes up to a second: check again
        self._execute(move)
        return move

    # ---- the requests ---------------------------------------------------------------------------
    def move_to_point(self, side: str, point, approach=None) -> Move:
        """`approach`: the direction the fingers point at the goal (chassis frame), or None for any."""
        point = [float(v) for v in point]
        self._in_workspace(_check_side(side), point)
        if approach is not None:
            approach = [float(v) for v in approach]
            return self._run(side, point=point, approach=approach)
        return self._run(side, point=point)

    def move_by(self, side: str, delta) -> Move:
        here, _ = self.planner.tool(_check_side(side), self._pose())
        return self.move_to_point(side, [here[i] + float(delta[i]) for i in range(3)])

    def move_to_named(self, side: str, name: str) -> Move:
        return self._run(_check_side(side), goal_signed=self.named_pose(side, name),
                         margin_deg=0.0 if name == "stow" else cue.MARGIN_DEG)

    def stow(self) -> None:
        for side in SIDES:
            self.move_to_named(side, "stow")

    def set_gripper(self, side: str, opening: float) -> None:
        opening = float(opening)
        if not 0.0 <= opening <= 1.0:
            raise ValueError(f"opening must be 0.0 (closed) to 1.0 (open), got {opening}")
        self._before()
        self._grip(_check_side(side), opening)

    def tool_poses(self) -> dict:
        pose = self._pose()
        out = {}
        for side in SIDES:
            pos, quat = self.planner.tool(side, pose)
            out[side] = {"position": dict(zip("xyz", pos)), "orientation": quat}
        return out

    def named_poses(self) -> list:
        return ["stow"] + sorted(self.config.named)


class LiveArms(_Arms):
    """Moves the real arms (arm_output.RosArms), or a fake for tests, behind the runner's gates."""

    def __init__(self, arms, state, planner: Planner, config: ArmConfig, limits: dict,
                 grip_cal=None, clock=time.monotonic, sleep=time.sleep):
        super().__init__(planner, config, limits)
        self.arms, self.state, self.grip_cal = arms, state, grip_cal
        self.clock, self.sleep = clock, sleep

    def _before(self) -> None:
        refusals = runner.preflight(REQUIRES, self.state.read())
        if refusals:
            raise Refused(refusals[0][0], "; ".join(r[1] for r in refusals))

    def _pose(self) -> dict:
        measured = self.arms.measured()
        if measured is None or any(measured.get(s) is None for s in ARM_SLOT.values()):
            raise Refused("UNKNOWN_STATE", "the arms' measured position is unknown (/joint_states)")
        return measured

    def _watch(self, gid, seconds: float, what: str):
        """Wait for goal `gid` to end, checking the gates every tick. Its final status."""
        t0, accepted = self.clock(), False
        while True:
            end = runner.monitor(REQUIRES, self.state.read())
            if end:
                self.arms.cancel_all()
                raise Refused(end[1], f"{end[2]}: {what} stopped, and the arm holds where it is")
            for ev in self.arms.drain_events():
                if ev.get("goal") != gid:
                    continue
                if ev.get("event") == "rejected":
                    self.arms.cancel_all()
                    raise MoveFailed(f"the controller rejected {what}")
                accepted = accepted or ev.get("event") == "accepted"
            status = self.arms.goal_status.get(gid)
            if status is not None:
                return status
            if not accepted and self.clock() - t0 > ARM_ACK_S:
                self.arms.cancel_all()
                raise MoveFailed(f"the controller did not accept {what} within {ARM_ACK_S:g} s")
            if self.clock() - t0 > seconds + RESULT_GRACE_S:
                self.arms.cancel_all()
                raise MoveFailed(f"{what} did not finish within {seconds + RESULT_GRACE_S:.1f} s")
            self.sleep(TICK_S)

    def _execute(self, move: Move) -> None:
        slot = ARM_SLOT[move.side]
        self.arms.drain_events()
        gid = self.arms.send(slot, move.points())
        what = f"the {move.side} arm's move"
        status = self._watch(gid, move.seconds, what)
        if status != SUCCEEDED:
            raise MoveFailed(f"{what} ended with status {status}")
        now = (self.arms.measured() or {}).get(slot)
        if now is None:
            raise MoveFailed(f"{what} finished, but the arm's position is unknown")
        off = max(abs(a - b) for a, b in zip(now, move.goal))
        if off > ARRIVAL_TOL_DEG:
            raise MoveFailed(f"{what} finished {off:.1f} deg from its goal (at most {ARRIVAL_TOL_DEG:g})")

    def _calibration(self) -> dict:
        import grippers
        if self.grip_cal is None:
            try:
                self.grip_cal = grippers.load()
            except grippers.CalibrationError as e:
                raise Refused("GRIPPER_UNCALIBRATED", str(e)) from None
        return self.grip_cal

    def _grip(self, side: str, opening: float) -> None:
        import grippers
        cal = self._calibration()
        if side in self.arms.grips_ready():     # the sides whose controller is NOT up
            raise MoveFailed(f"the {side} gripper controller is not up")
        self.arms.drain_events()
        gid = self.arms.send_grip(GRIP_SLOT[side], grippers.to_controller(cal, side, opening))
        status = self._watch(gid, GRIP_TIMEOUT_S, f"the {side} gripper")
        # ABORTED is a close that stalled: it met something, which is what a grasp wants
        if status not in (SUCCEEDED, ABORTED):
            raise MoveFailed(f"the {side} gripper ended with status {status}")

    def gripper_openings(self) -> dict:
        """{side: 0.0 (closed) .. 1.0 (open), or None when unknown}, from where the fingers really are."""
        import grippers
        cal = self._calibration()
        out = {}
        for side in SIDES:
            pos = self.arms.finger(side)
            out[side] = None if pos is None else round(grippers.from_controller(cal, side, pos), 3)
        return out

    def joints(self) -> dict:
        """{side: {"joint1": {position rad, velocity rad/s, effort Nm}, ...}}, signed (the controller's sense)."""
        out = {}
        for side in SIDES:
            sample = self.arms.sample(side) if hasattr(self.arms, "sample") else None
            if sample is None:
                pos, vel, eff = self._pose()[ARM_SLOT[side]], None, None
            else:
                _, pos, vel, eff = sample
            out[side] = {f"joint{j}": {"position": math.radians(pos[j - 1]),
                                       "velocity": math.radians(vel[j - 1]) if vel else None,
                                       "effort": eff[j - 1] if eff else None} for j in range(1, 8)}
        return out


class FollowingFakeArms(arm_output.FakeArms):
    """arm_output.FakeArms whose goals finish at once, the arm (or gripper) arriving where it was
    sent: for a desk with no arms, and for tests."""

    def __init__(self, measured=None):
        super().__init__(measured)
        self.grip_pos = {}

    def send(self, slot, points):
        gid = super().send(slot, points)
        if slot not in self.reject and slot not in self.silent:
            self._measured[slot] = list(points[-1][1])
            self.goal_status[gid] = SUCCEEDED
        return gid

    def send_grip(self, slot, metres):
        super().send_grip(slot, metres)
        self._seq += 1                          # FakeArms numbers grips apart from arm goals; one count here
        gid = self._seq
        self._events.append({"goal": gid, "slot": slot, "event": "accepted"})
        self.grip_pos[slot] = float(metres)
        self.goal_status[gid] = SUCCEEDED
        return gid

    def finger(self, side):
        return self.grip_pos.get(GRIP_SLOT[side], self.fingers.get(side))


class ShowRecorder(_Arms):
    """The fallback: plans the same moves and writes each as a cue of a show the runner can play.

    The show starts with every arm at zero, as the runner starts every show (runner.prep_moves), and
    each move begins where the last one ended. Perception is read once, while recording, so the
    show's targets are fixed: what the camera saw then is where the arm goes every time it plays.
    """

    def __init__(self, planner: Planner, config: ArmConfig, limits: dict, path: str,
                 show_id: str = "architect", name: str = "ARCHITECT program", palette: dict | None = None):
        super().__init__(planner, config, limits)
        self.path, self.show_id, self.name, self.palette = path, show_id, name, palette
        self.t = 0.0
        self.pose = {slot: [0.0] * 7 for slot in ARM_SLOT.values()}
        self.grips = {side: None for side in SIDES}
        self.cues: list = []

    def _pose(self) -> dict:
        return {k: list(v) for k, v in self.pose.items()}

    def _execute(self, move: Move) -> None:
        slot = ARM_SLOT[move.side]
        for waypoint, leg in zip(move.via, move.legs):     # a routed move: each waypoint a cue of its own
            self.t = round(self.t + leg, 2)
            self.cues.append({"t": self.t, "slot": slot,
                              "joints": {j: round(v, 1) for j, v in enumerate(to_outward(move.side, waypoint), 1)}})
        self.t = round(self.t + (move.legs[-1] if move.via else move.seconds), 2)
        self.cues.append({"t": self.t, "slot": slot,
                          "joints": {j: round(v, 1) for j, v in enumerate(to_outward(move.side, move.goal), 1)}})
        self.pose[slot] = list(move.goal)
        self.write()

    def move_to_named(self, side: str, name: str) -> Move:
        move = super().move_to_named(side, name)
        if name == "stow":                      # the validator takes rest exactly only when it is named
            self.cues[-1] = {"t": self.cues[-1]["t"], "slot": ARM_SLOT[side], "pose": "rest"}
            self.write()
        return move

    def wait(self, seconds: float) -> None:
        """Nothing moves for `seconds`: the next cue starts that much later."""
        self.t = round(self.t + float(seconds), 2)

    def _grip(self, side: str, opening: float) -> None:
        self.cues.append({"t": round(self.t, 2), "slot": GRIP_SLOT[side], "open": round(opening, 2)})
        self.grips[side] = opening
        self.t = round(self.t + GRIP_S, 2)
        self.write()

    def gripper_openings(self) -> dict:
        return dict(self.grips)

    def doc(self) -> dict:
        doc = {"id": self.show_id, "name": self.name}
        if self.palette:                        # the Shows page lists only a show with a palette block
            doc["palette"] = dict(self.palette)
        return {**doc,
                "slots": ["ARM_L", "ARM_R", "GRIP_L", "GRIP_R"],
                "requires": {"arms": "active", "rc_authority": "AUTO"},
                "notes": "Recorded from an approved ARCHITECT program (software/architect). "
                         "Its clearance stamp is written when it is published (show_backend.Publisher); "
                         "an edited copy needs sweep.py stamp again.",
                "cues": list(self.cues)}

    def write(self) -> None:
        """Rewrite the show after every cue, so a recording that stops part way still leaves a show."""
        import yaml
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self.doc(), fh, sort_keys=False, default_flow_style=None)
        os.replace(tmp, self.path)
