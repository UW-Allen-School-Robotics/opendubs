#!/usr/bin/env python3
"""Engage an arm: from where it is to the ready pose, then hand over to `track`.

    $PY software/openarm/gafro/engage.py --model description/openarm/gafro/openarm_bimanual.xml --side right

The arm starts at stow, which is zero and singular. `track` from there has its singular-value guard
swing the arm, so the first controller is `engage` (a posture stage in joint space, gravity from the model) following a straight line in
joint space to the model's `ready` keyframe. The line is checked against the collision geometry first and refused if any pair of parts
comes nearer along it than it is at either end and within 2 cm (pairs that touch by construction, a hand on its wrist, are at the same distance at both
ends and do not count). The speed is a joint speed, and the move takes as long as its largest joint travel needs.

Then `track` takes over with the hand and the posture both anchored where the arm is, so the handover is a hold: nothing is commanded
that was not already the state. One arm per call, or both: the other arm of a one-arm call is not touched, and both arms end under `engage` (see `engage()`).

`--park` is the way back: the same move to the stow pose of `/etc/opendubs/openarm-stow.conf` (the file `openarm-up park` reads, degrees,
`LEFT_STOW=` / `RIGHT_STOW=`), ending under `engage` holding the pose and not under `track`, which cannot start at a singular stow. The numbers
are taken as the model's joint angles, which is what they are where the driver's direction and zero are the identity (opendubs2 stows at
zero); for a robot whose are not, the file is in the motors' terms and needs converting first.

This is the sequence, not the authority. Until the arm service exists, a move on a real bus (a `bus: can` group in the hardware config) is
refused unless the guards of guards.py pass, before anything is enabled: the existing stack is not running and no other sender is commanding
the motors, CH5 is MANUAL or AUTO with the e-stop clear, `--supervised` says a person is at the e-stop, and the arm starts near stow or ready (the
line to the target is checked against a model with no head). A `fake` bus and the simulator are not gated.

"Ready" is a working pose and not a resting one: an e-stop or a power drop there lets the forearm fall. Park before leaving the robot.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import threading
import time

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import guards  # noqa: E402
MARGIN = 0.003          # how much nearer than at its closer end a pair may come along the path, m
CLEARANCE = 0.02        # a pair further apart than this along the whole path is clear, however much nearer than at its ends, m
JOINT_SPEED = 0.3       # rad/s, the largest joint's; RI's default for go_to_*
MIN_DURATION = 2.0      # s, however short the travel
SETTLE_TOLERANCE = 0.05  # rad, per joint, at the end of the move: joint friction stalls a wrist a few hundredths short, and `track` anchors where the arm is
HANDOVER_HOLD = 1.0     # s, held under `track` before it is reported engaged


class EngageRefused(guards.Refused):
    """The sequence did not start, or did not finish: the message says why and nothing was left running."""


def load_controllers(path: str | None = None) -> dict:
    with open(path or os.path.join(HERE, "controllers.yaml")) as handle:
        return yaml.safe_load(handle)["controllers"]


def minimum_jerk(fraction: float) -> float:
    """0 to 1 with zero speed at both ends: the arm leaves and arrives at rest."""
    return fraction ** 3 * (10.0 - 15.0 * fraction + 6.0 * fraction ** 2)


def duration_for(start: np.ndarray, target: np.ndarray, speed: float) -> float:
    """Peak joint speed of a minimum-jerk move is 1.875 times its average, so the largest travel sets the time."""
    return max(MIN_DURATION, 1.875 * float(np.max(np.abs(target - start))) / speed)


class PathCheck:
    """Distances between the model's collision geometry along a joint-space line, by MuJoCo's signed distance."""

    def __init__(self, model_path: str, joint_names: list[str]):
        import itertools
        import mujoco

        self.mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(model_path)
        self.data = mujoco.MjData(self.model)
        ids = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in joint_names]
        self.addresses = self.model.jnt_qposadr[ids]
        self.rest = self.model.key_qpos[0].copy() if self.model.nkey else np.zeros(self.model.nq)

        excluded = set()
        for index in range(self.model.nexclude):
            signature = int(self.model.exclude_signature[index])
            excluded.add(tuple(sorted(((signature >> 16) - 1, (signature & 0xFFFF) - 1))))

        geoms = [g for g in range(self.model.ngeom) if self.model.geom_contype[g] and self.model.geom_conaffinity[g]]
        parent = self.model.body_parentid
        self.pairs = []
        for first, second in itertools.combinations(geoms, 2):
            a, b = int(self.model.geom_bodyid[first]), int(self.model.geom_bodyid[second])
            if a == b or parent[a] == b or parent[b] == a or tuple(sorted((a, b))) in excluded:
                continue
            self.pairs.append((first, second))

    def distances(self, configuration: np.ndarray) -> np.ndarray:
        self.data.qpos[:] = self.rest
        self.data.qpos[self.addresses] = configuration
        self.mujoco.mj_kinematics(self.model, self.data)
        return np.array([self.mujoco.mj_geomDistance(self.model, self.data, a, b, 0.3, None) for a, b in self.pairs])

    def refusal(self, start: np.ndarray, target: np.ndarray, steps: int = 60) -> str | None:
        """The pair that comes nearer along the line than at its closer end, or None."""
        at_start, at_target = self.distances(start), self.distances(target)
        allowed = np.minimum(np.minimum(at_start, at_target) - MARGIN, CLEARANCE)

        for fraction in np.linspace(0.0, 1.0, steps + 1)[1:-1]:
            here = self.distances(start + (target - start) * fraction)
            worse = np.where(here < allowed)[0]
            if worse.size:
                first, second = self.pairs[worse[np.argmin(here[worse] - allowed[worse])]]
                return (f"{self.model.geom(first).name} and {self.model.geom(second).name} come to {1000 * here[worse].min():.0f} mm "
                        f"({fraction:.0%} of the way), nearer than at either end")
        return None


def build(name: str, controllers: dict, system, side: str, configuration: np.ndarray, pose, reference, posture):
    from gafro.control import spec
    from gafro.robot import JointDomain

    task_space = system.get_task_space(side)
    description = controllers[name]
    controller, _ = spec.build(
        description, dict(description.get("inputs", {})),
        dict(system=system, task_space=task_space, task_space_name=side, domain=JointDomain.from_task_space(system, task_space),
             configuration=configuration, reference=pose, reference_source=reference, posture_source=posture,
             posture_initial=configuration.copy()),
        name)
    return controller


class PermitKeeper:
    """Keeps the driver's permit alive while the sequence runs: the arm holds unless a short permit is being renewed (`require_permit`), so
    a sequence that stops, dies or is interrupted leaves the arm holding within `lease` seconds. Revoked on exit.

    A no-op without `groups` (the simulator has no permit). The driver names its permit after its control group (`left_arm`, `right_arm`).

    `allowed`, if given, is asked before every renewal (guards.motion_gate: CH5 MANUAL or AUTO, the e-stop clear and the deadman, CH6, held): when it says no, or raises, the
    renewal is withheld and the arm holds within `lease`. So a sequence in progress stops when CH5 goes to OFF, the e-stop is pressed or the deadman is released, instead of
    finishing its move.
    `grant` and `revoke` replace the driver's calls, for tests.
    """

    def __init__(self, groups=(), lease: float = 0.5, period: float = 0.1, allowed=None, grant=None, revoke=None):
        self.groups, self.lease, self.period = tuple(groups), lease, period
        self.allowed = allowed
        self.withheld = 0                      # renewals not made because `allowed` said no: how often the sequence was cut short
        self._grant_fn, self._revoke_fn = grant, revoke
        self._stop = threading.Event()
        self._thread = None

    def _grant(self, group):
        if self._grant_fn is not None:
            return self._grant_fn(group, self.lease)
        import openarm_driver

        return openarm_driver.grant_permit(group, self.lease)

    def _revoke(self, group):
        if self._revoke_fn is not None:
            return self._revoke_fn(group)
        import openarm_driver

        return openarm_driver.revoke_permit(group)

    def _may_renew(self) -> bool:
        if self.allowed is None:
            return True
        try:
            return bool(self.allowed())
        except Exception:  # noqa: BLE001 -- a gate that cannot answer is a gate that says no
            return False

    def _run(self):
        while not self._stop.is_set():
            if self._may_renew():
                for group in self.groups:
                    self._grant(group)
            else:
                self.withheld += 1
            self._stop.wait(self.period)

    def start(self):
        if self.groups and self._thread is None:
            if not self._may_renew():
                raise guards.Refused("CH5 does not permit motion, or the e-stop is not clear, or the dashboard did not answer: no permit was granted")
            for group in self.groups:
                self._grant(group)   # before the loop, so an unusable driver fails here and not later
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        """Stop renewing and revoke now: the arms hold from the next cycle."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None

            for group in self.groups:
                self._revoke(group)

    def lapse(self):
        """Stop renewing and let the permit run out by itself, as it does when whatever renews it dies."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def permit_groups(side: str) -> tuple[str, ...]:
    return ("left_arm", "right_arm") if side == "both" else (f"{side}_arm",)


STOW_CONF = os.environ.get("OPENARM_STOW_CONF", "/etc/opendubs/openarm-stow.conf")


def stow_pose(side: str, path: str | None = None) -> np.ndarray | None:
    """One arm's stow pose in radians from the per-robot file (`LEFT_STOW=0,-20,0,95,0,15,0`, degrees), or None if the file or the line is not there."""
    try:
        with open(path or STOW_CONF) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None

    value = None
    for line in lines:
        key, _, rest = line.partition("=")
        if key.strip().upper() == f"{side.upper()}_STOW" and rest.strip():
            value = rest.strip()
    if value is None:
        return None

    degrees = [float(part) for part in value.replace(" ", "").split(",")]
    if len(degrees) != 7:
        raise EngageRefused(f"{side}: the stow pose has {len(degrees)} angles, an arm has 7")
    return np.radians(degrees)


def park(robot, side: str, model_path: str, conf: str | None = None, speed: float = JOINT_SPEED, controllers: dict | None = None, log=print,
         start_tolerance: float | None = None):
    """Bring `side` ("left", "right" or "both") to its configured stow pose and leave `engage` holding it, ready to be de-energized.

    Unset is refused and not guessed, as `openarm-up park` does: a wrong pose drives the arm into the head.
    """
    system = robot.get_system()
    target_all = np.asarray(robot.get_state().get_position(), dtype=float).copy()

    stows = []
    for name in ("left", "right") if side == "both" else (side,):
        pose = stow_pose(name, conf)
        if pose is None:
            raise EngageRefused(f"{name}: no stow pose configured in {conf or STOW_CONF} (a line `{name.upper()}_STOW=<7 angles in degrees>`); it is "
                                "deliberately unset and not guessed, since a wrong pose drives the arm into the head")
        target_all[np.asarray(system.get_task_space(name).get_joint_indices())] = pose
        stows.append(pose)

    return engage(robot, side, model_path, "stow", speed, controllers, log, target_all=target_all, then="hold",
                  known_starts={"stow": np.concatenate(stows)}, start_tolerance=start_tolerance)


def engage(robot, side: str, model_path: str, keyframe: str = "ready", speed: float = JOINT_SPEED, controllers: dict | None = None,
           log=print, target_all: np.ndarray | None = None, then: str = "track", known_starts: dict | None = None,
           start_tolerance: float | None = None):
    """Bring `side` ("left", "right" or "both") to the keyframe. One arm is left under `track`, holding it, and its sources are returned,
    (reference, posture). Both arms are left under `engage`, holding the pose (None is returned): a RobotInterface runs one controller and a
    stage binds to one task space, so there is no `track` over both until gafro can bind a stage to a task space of its own.

    `target_all` is a whole-system configuration to go to instead of the keyframe's. `then` is "track" (the handover) or "hold" (stay under
    `engage`, for a pose `track` cannot start at, such as stow).

    `start_tolerance` (rad, per joint) makes the move start only near stow or ready: the line to the target is checked against a model with no
    head, neck or lift, so from anywhere else it could pass through the head unseen. `known_starts` names the poses (this arm's joints, in the task
    space's order) it may start near besides the model's `ready`; the stow pose is the one a caller gives.

    Raises EngageRefused, with the arm held by whatever was running, for a target outside the joint ranges, a line through a collision, or
    a start that is not known.
    """
    from gafro import control

    controllers = controllers or load_controllers()
    system = robot.get_system()
    both = side == "both"
    task_space_name = "bimanual" if both else side
    task_space = system.get_task_space(task_space_name)
    indices = np.asarray(task_space.get_joint_indices())
    # The pose the controller is built around; `engage` has no pose stage, so the first arm's is only what the build needs.
    pose_space = system.get_task_space("right" if both else side)
    pose_indices = np.asarray(pose_space.get_joint_indices())
    state = lambda: np.asarray(robot.get_state().get_position(), dtype=float)  # noqa: E731

    if target_all is None:
        key = system.get_keyframe(keyframe)
        if key is None:
            raise EngageRefused(f"the model has no keyframe {keyframe!r}")
        target_all = np.asarray(key.qpos, dtype=float)[: len(state())]

    start_all = state().copy()
    start, target = start_all[indices], target_all[indices]

    if start_tolerance is not None:
        known = dict(known_starts or {})
        ready = system.get_keyframe("ready")
        if ready is not None:
            known["ready"] = np.asarray(ready.qpos, dtype=float)[: len(start_all)][indices]
        try:
            log(f"the arm starts near {guards.require_known_start(start, known, start_tolerance)}")
        except guards.Refused as refused:
            raise EngageRefused(str(refused)) from None

    lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
    upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
    if np.any(target < lower) or np.any(target > upper):
        raise EngageRefused(f"the {keyframe} pose is outside the {side} arm's joint limits")

    actuators = [a.joint_name for a in system.get_actuators()]
    refusal = PathCheck(model_path, actuators).refusal(start_all, _line_end(start_all, target_all, indices))
    if refusal:
        raise EngageRefused(f"the straight line to {keyframe} is not clear: {refusal}")

    duration = duration_for(start, target, speed)
    log(f"engage {side}: {np.round(start, 2)} -> {np.round(target, 2)} in {duration:.1f} s")

    posture = control.JointReferenceSource(start_all.copy(), horizon=0.5)
    posture.set_staleness(control.Staleness.Hold)
    posture.prime(2)
    pose = pose_space.compute_ee_motor(start_all[pose_indices])
    reference = control.MotorReferenceSource(pose, horizon=0.5)
    reference.set_staleness(control.Staleness.Hold)
    reference.prime(2)

    robot.control(build("engage", controllers, system, task_space_name, start_all, pose, reference, posture), False)
    time.sleep(0.5)  # holding where it is before anything moves

    knots = 40
    points = []
    for index in range(knots + 1):
        weight = minimum_jerk(index / knots)
        full = start_all.copy()
        full[indices] = start + (target - start) * weight
        points.append(full)
    # The chunk's second argument is the spacing between knots, not the length of the chunk.
    posture.publish(control.JointReferenceChunk(control.monotonic_now(), duration / knots, points))
    time.sleep(duration + 1.0)

    here = state()
    error = np.abs(here[indices] - target)
    if error.max() > SETTLE_TOLERANCE:
        raise EngageRefused(f"the arm stopped {error.max():.3f} rad short of {keyframe} (joint {int(error.argmax()) + 1}); `engage` is still holding")

    if both or then == "hold":
        log(f"engaged {side}: at {keyframe} within {error.max():.3f} rad, held by `engage`")
        return None

    # The handover: hand and posture both anchored where the arm is, so `track` is handed a hold and commands nothing new.
    pose = task_space.compute_ee_motor(here[indices])
    track_reference = control.MotorReferenceSource(pose, horizon=0.5)
    track_reference.set_staleness(control.Staleness.Hold)
    track_reference.prime(2)
    track_posture = control.JointReferenceSource(here.copy(), horizon=0.5)
    track_posture.set_staleness(control.Staleness.Hold)
    track_posture.prime(2)
    robot.control(build("track", controllers, system, side, here, pose, track_reference, track_posture), False)

    time.sleep(HANDOVER_HOLD)
    drift = np.abs(state()[indices] - here[indices]).max()
    log(f"engaged {side}: at {keyframe} within {error.max():.3f} rad, {drift:.4f} rad of joint drift in the first {HANDOVER_HOLD:g} s under track")
    return track_reference, track_posture


def _line_end(start_all: np.ndarray, target_all: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """The whole system's configuration with only this arm's joints at the target: the other arm does not move."""
    end = start_all.copy()
    end[indices] = target_all[indices]
    return end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="the robot's gafro model (description/openarm/gafro/openarm_bimanual.xml)")
    parser.add_argument("--side", choices=("left", "right", "both"), required=True)
    parser.add_argument("--keyframe", default="ready")
    parser.add_argument("--park", action="store_true", help="go to the stow pose of the stow file instead, and hold it under `engage`")
    parser.add_argument("--stow-conf", default=None, help=f"the per-robot stow file (default {STOW_CONF})")
    parser.add_argument("--speed", type=float, default=JOINT_SPEED, help="the largest joint's peak-average speed, rad/s")
    parser.add_argument("--backend", default="mujoco", choices=("mujoco", "hardware"))
    parser.add_argument("--hardware-config", default="", help="hardware.yaml, for --backend hardware")
    parser.add_argument("--supervised", action="store_true", help="a person is at the e-stop, head tracking is off and the neck centred (required for a move on a real bus)")
    parser.add_argument("--dashboard", default=guards.DASHBOARD, help="where CH5 and the e-stop are read (GET /api/choreo/robot)")
    parser.add_argument("--start-tolerance-deg", type=float, default=float(np.degrees(guards.START_TOLERANCE)),
                        help="on a real bus the arm must start this near stow or ready, per joint")
    parser.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS, help="how long to listen for another sender")
    args = parser.parse_args()

    import gafro  # noqa: F401  -- before gafro_robot_interface
    import gafro_robot_interface as ri

    groups = permit_groups(args.side) if args.backend == "hardware" else ()
    names = ("left", "right") if args.side == "both" else (args.side,)
    known, tolerance, gate = None, None, None

    if args.backend == "hardware":
        # Before the RobotInterface exists: initialize() enables the motors, which is already a command on the bus.
        try:
            interfaces, real = guards.buses_of(args.hardware_config, groups)
            if real:
                guards.require_exclusive(interfaces, args.listen_seconds)
                guards.require_motion_permitted(args.supervised, args.dashboard, deadman=True)   # CH6 held from the start: there is no typed-back step to hold it from
                stows = [stow_pose(name, args.stow_conf) for name in names]
                if any(pose is None for pose in stows):
                    raise guards.Refused(f"no stow pose configured in {args.stow_conf or STOW_CONF}: a move on a real bus starts near stow or ready")
                known, tolerance = {"stow": np.concatenate(stows)}, float(np.radians(args.start_tolerance_deg))
                gate = guards.motion_gate(args.dashboard)    # renew the permit only while CH5 and the e-stop still allow the move
        except guards.Refused as refused:
            print(f"refused: {refused}")
            return 1

        import openarm_driver  # noqa: F401  -- registers `driver: openarm`; after gafro_robot_interface

    robot = ri.RobotInterface(ri.Configuration(args.model, args.backend, args.hardware_config))
    robot.initialize()
    try:
        with PermitKeeper(groups, allowed=gate):
            if args.park:
                park(robot, args.side, args.model, args.stow_conf, args.speed, start_tolerance=tolerance)
            else:
                engage(robot, args.side, args.model, args.keyframe, args.speed, known_starts=known, start_tolerance=tolerance)
            time.sleep(1.0)
    except guards.Refused as refused:
        print(f"refused: {refused}")
        return 1
    finally:
        robot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
