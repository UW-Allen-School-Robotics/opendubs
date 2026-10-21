#!/usr/bin/env python3
"""Engage an arm: from where it is to the ready pose, then hand over to `track`.

    ~/coding/lab/venv/bin/python software/openarm/gafro/engage.py --model description/openarm/gafro/openarm_bimanual.xml --side right

The arm is wherever it was left -- stow, which is zero and singular, or frozen anywhere. `track` from there has its singular-value guard
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

This is the sequence, not the authority: it does not ask a permit of anything. The arm service calls it once an arm is permitted to move.
It runs against the simulator as written; against hardware only the `RobotInterface` configuration changes.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
MARGIN = 0.003          # how much nearer than at its closer end a pair may come along the path, m
CLEARANCE = 0.02        # a pair further apart than this along the whole path is clear, however much nearer than at its ends, m
JOINT_SPEED = 0.3       # rad/s, the largest joint's; RI's default for go_to_*
MIN_DURATION = 2.0      # s, however short the travel
SETTLE_TOLERANCE = 0.05  # rad, per joint, at the end of the move: joint friction stalls a wrist a few hundredths short, and `track` anchors where the arm is
HANDOVER_HOLD = 1.0     # s, held under `track` before it is reported engaged


class EngageRefused(RuntimeError):
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


def park(robot, side: str, model_path: str, conf: str | None = None, speed: float = JOINT_SPEED, controllers: dict | None = None, log=print):
    """Bring `side` ("left", "right" or "both") to its configured stow pose and leave `engage` holding it, ready to be de-energized.

    Unset is refused and not guessed, as `openarm-up park` does: a wrong pose drives the arm into the head.
    """
    system = robot.get_system()
    target_all = np.asarray(robot.get_state().get_position(), dtype=float).copy()

    for name in ("left", "right") if side == "both" else (side,):
        pose = stow_pose(name, conf)
        if pose is None:
            raise EngageRefused(f"{name}: no stow pose configured in {conf or STOW_CONF} (a line `{name.upper()}_STOW=<7 angles in degrees>`); it is "
                                "deliberately unset and not guessed, since a wrong pose drives the arm into the head")
        target_all[np.asarray(system.get_task_space(name).get_joint_indices())] = pose

    return engage(robot, side, model_path, "stow", speed, controllers, log, target_all=target_all, then="hold")


def engage(robot, side: str, model_path: str, keyframe: str = "ready", speed: float = JOINT_SPEED, controllers: dict | None = None,
           log=print, target_all: np.ndarray | None = None, then: str = "track"):
    """Bring `side` ("left", "right" or "both") to the keyframe. One arm is left under `track`, holding it, and its sources are returned,
    (reference, posture). Both arms are left under `engage`, holding the pose (None is returned): a RobotInterface runs one controller and a
    stage binds to one task space, so there is no `track` over both until gafro can bind a stage to a task space of its own.

    `target_all` is a whole-system configuration to go to instead of the keyframe's. `then` is "track" (the handover) or "hold" (stay under
    `engage`, for a pose `track` cannot start at, such as stow).

    Raises EngageRefused, with the arm held by whatever was running, for a target outside the joint ranges or a line through a collision.
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
    args = parser.parse_args()

    import gafro  # noqa: F401  -- before gafro_robot_interface
    import gafro_robot_interface as ri

    robot = ri.RobotInterface(ri.Configuration(args.model, args.backend, args.hardware_config))
    robot.initialize()
    try:
        if args.park:
            park(robot, args.side, args.model, args.stow_conf, args.speed)
        else:
            engage(robot, args.side, args.model, args.keyframe, args.speed)
        time.sleep(1.0)
    except EngageRefused as refused:
        print(f"refused: {refused}")
        return 1
    finally:
        robot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
