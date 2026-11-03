"""Take one arm's hand to a pose and say whether it got there: the move behind the `reach_pose` command (commands.py).

The pose is in the arm model's frame, the one `<side>/target` and `compute_ee_motor` use (+x forward, +y the robot's left, +z up). The move is made of what the stack already has:

  1. reach.py says whether the pose is inside the arm's reach at all (a self-motion arc inside the joint box with a posture clear of the arm itself): a pose that is not is refused
     before anything is planned. Where ssik and self_motion_charts are not installed there is no such check and no posture choice.
  2. the posture: of the clear postures on that arc, the one nearest the arm's own with room to every joint limit (`Reach.best_posture`). It seeds the iLQR solve.
  3. tools/hand_plan.py's iLQR (`plan_pose`, double integrator: state = joints and their velocities, control = acceleration) plans the joint path to the pose, offline, from rest to rest,
     with its own timing: the shortest horizon in which no joint passes `speed` rad/s.
  4. the path is checked for SELF-collision along its length (`engage.PathCheck.path_refusal`: the model's own geometry, the arm, the other arm and the body; no pair nearer than at the
     path's ends; there is no environment in the model) and against the joint limits.
  5. `ArmService.play` plays it in position mode, under the gate and the stops every moving command has; the hand is then measured against the pose, and a move that stopped short is made again (`CORRECTIONS` times) from where the arm is.

Success means the hand is within `tolerance_mm` and `tolerance_deg` of the pose, measured after the arm has stopped. The arm is left enabled and holding where it is, whichever way it ends.
Not in the collision model: the head and the camera mount. A real arm stops up to ~10 mm short at the hand (joint friction), so the default position tolerance is 15 mm.
"""
from __future__ import annotations

import dataclasses
import json
import time

import numpy as np

import trajectory

# the result codes of opendubs_arm_interfaces/action/ArmCommand (robot_api_interfaces' ResultCode values)
SUCCESS, FAILURE, INVALID_INPUT, NOT_FOUND = 0, 1, 2, 5



def speed_cap(limits=None) -> float:
    """The fastest joint speed a move may be planned at, rad/s: 0.9 of what `trajectory.Limits` accepts, and under the speed at which its vibration stop ends a run (`max_joint_speed`),
    which a planned peak must stay clear of, since the arm overshoots the plan a little. `limits` are the service's (`ArmService.limits`: on a real bus the driver's `max_velocity`)."""
    limits = limits or trajectory.Limits()
    return min(0.9 * limits.max_speed, 0.8 * limits.max_joint_speed)


def default_speed(limits=None) -> float:
    return 0.75 * speed_cap(limits)


PROFILE_PEAK = 1.875         # the minimum-jerk profile's peak speed, in units of the mean
TOLERANCE_MM = 15.0
TOLERANCE_DEG = 6.0
SAMPLES = 61
MIN_DURATION = 0.6          # s
CORRECTIONS = 2              # extra moves to close what the arm stopped short of (a real arm stops up to ~10 mm short: joint friction; the position servo has no integral)
SETTLE = 0.5                 # s the arm is given to come to rest before the hand is measured


@dataclasses.dataclass
class Outcome:
    code: int
    message: str
    data: dict = dataclasses.field(default_factory=dict)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.code == SUCCESS


@dataclasses.dataclass
class Request:
    position: np.ndarray
    orientation: np.ndarray | None = None      # (w, x, y, z); None holds the hand's own
    speed: float = dataclasses.field(default_factory=default_speed)
    duration: float | None = None              # s: the plan's horizon, rest to rest; None is the shortest one that keeps every joint under `speed`
    tolerance_mm: float = TOLERANCE_MM
    tolerance_deg: float = TOLERANCE_DEG


def _numbers(value, names, what):
    """A list of numbers, or a dict of them under `names`, as an array."""
    try:
        if isinstance(value, dict):
            value = [value[n] for n in names]
        out = np.array([float(v) for v in value], dtype=float)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{what} is {len(names)} numbers, a list or an object with {', '.join(names)}") from None
    if out.shape != (len(names),) or not np.all(np.isfinite(out)):
        raise ValueError(f"{what} is {len(names)} finite numbers")
    return out


def parse(payload, limits=None) -> Request:
    """A request from its JSON text (or the dict): {"position": [x, y, z] (m), "orientation": [w, x, y, z] or {w, x, y, z} (optional: held), "speed": rad/s, "duration": s, "tolerance_mm", "tolerance_deg"}.
    A `pose` object holding the same, or ROS's own {position: {x, y, z}, orientation: {x, y, z, w}}, is read too. ValueError says what is wrong."""
    try:
        body = json.loads(payload) if isinstance(payload, str) and payload.strip() else (payload or {})
    except json.JSONDecodeError as error:
        raise ValueError(f"the request is not JSON: {error}") from None
    if not isinstance(body, dict):
        raise ValueError("the request is a JSON object")
    body = body.get("pose", body)
    if "position" not in body:
        raise ValueError("the request needs a position, [x, y, z] in metres in the arm model's frame")
    position = _numbers(body["position"], "xyz", "the position")
    orientation = None
    if body.get("orientation") is not None:
        raw = body["orientation"]
        orientation = _numbers(raw, "wxyz", "the orientation")
        norm = float(np.linalg.norm(orientation))
        if norm < 1e-6:
            raise ValueError("the orientation is a zero quaternion")
        orientation = orientation / norm
    speed = float(body.get("speed", default_speed(limits)))
    if not 0.0 < speed <= speed_cap(limits):
        raise ValueError(f"the speed is rad/s, above 0 and at most {speed_cap(limits):g}")
    duration = body.get("duration")
    if duration is not None:
        duration = float(duration)
        if not MIN_DURATION / 2 <= duration <= 60.0:
            raise ValueError(f"the duration is seconds, from {MIN_DURATION / 2:g} to 60")
    tolerance_mm, tolerance_deg = float(body.get("tolerance_mm", TOLERANCE_MM)), float(body.get("tolerance_deg", TOLERANCE_DEG))
    if tolerance_mm <= 0 or tolerance_deg <= 0:
        raise ValueError("the tolerances are positive")
    return Request(position, orientation, speed, duration, tolerance_mm, tolerance_deg)


def hand(robot, side: str):
    """(the arm's joints, the whole configuration, the hand's pose as px, py, pz, qw, qx, qy, qz)."""
    system = robot.get_system()
    task = system.get_task_space(side)
    indices = np.asarray(task.get_joint_indices())
    full = np.asarray(robot.get_state().get_position(), dtype=float).copy()
    pose = np.asarray(task.compute_ee_motor(full[indices]).to_position_quaternion(), dtype=float)
    return full[indices], full, pose


def errors(pose, goal):
    """(mm, deg) between two hand poses."""
    import hand_plan

    return float(np.linalg.norm(pose[:3] - goal[:3]) * 1000.0), float(np.degrees(hand_plan.orientation_error(pose, goal)))


def timed(states, speed: float, samples: int = SAMPLES):
    """(times, positions, rates) of the planned path `states` (7, N+1) on a minimum-jerk profile along its length, so that no joint is faster than `speed`.

    The path is measured by its largest joint step (so a joint's rate along it is at most 1) and not by iLQR's own knot times, which it spends where its cost likes."""
    states = np.asarray(states, dtype=float)
    steps = np.abs(np.diff(states, axis=1)).max(axis=0)
    length = float(steps.sum())
    arc = np.r_[0.0, np.cumsum(steps)]
    duration = max(PROFILE_PEAK * length / speed, MIN_DURATION)
    x = np.linspace(0.0, 1.0, samples)
    along = x**3 * (10.0 - 15.0 * x + 6.0 * x**2) * length
    positions = np.stack([np.interp(along, arc, row) for row in states], axis=1)
    times = x * duration
    return times, positions, np.gradient(positions, times, axis=0)       # the rates of the positions given, not the knots' slopes: those jump at a corner and the Hermite resample rings


def schedule(states, speed: float, limit: float):
    """The path timed so that no joint exceeds `speed`, and (as trajectory.follow will resample it) not `limit` either: slowed until the resampled path's own peak fits."""
    speed = min(speed, limit)
    for _ in range(6):
        times, positions, rates = timed(states, speed)
        peak = float(np.abs(trajectory.resample(times, positions, rates)[2]).max())
        if peak <= limit:
            break
        speed *= limit / peak * 0.95
    return times, positions, rates


class Failed(Exception):
    """A move that cannot be made, or that did not end well: `outcome` says why."""

    def __init__(self, code: int, message: str, data: dict | None = None):
        super().__init__(message)
        self.outcome = Outcome(code, message, data or {})


def path_check(service):
    """The collision check of the service's model (self-collision only: the model has no environment), built on first use."""
    import engage

    if service.paths is None:
        service.paths = engage.PathCheck(service.args.model, [a.joint_name for a in service.robot.get_system().get_actuators()])
    return service.paths


def goal_pose(request: Request, pose):
    return np.r_[request.position, pose[3:] if request.orientation is None else request.orientation]


def plan(service, request: Request, goal, q, full):
    """The path (hand_plan's dict) from the arm as it is to `goal`, checked against the joint limits and for self-collision. Raises Failed."""
    import gafro as g
    import hand_plan

    system = service.robot.get_system()
    task = system.get_task_space(service.args.side)
    indices = np.asarray(task.get_joint_indices())
    posture = None
    if service.reach is not None:
        posture = service.reach.best_posture(g.Motor.from_position_quaternion(*goal), q, full)
        if posture is None:
            raise Failed(NOT_FOUND, "the pose is outside the arm's reach: no posture holds it inside the joint limits and clear of the arm itself", {"goal": goal.tolist()})
    fixed = request.duration
    first = max(MIN_DURATION, 1.6 * float(np.abs(posture - q).max()) / request.speed) if posture is not None else MIN_DURATION     # the iLQR horizon only grows from here
    try:
        found = hand_plan.plan_pose(system, task, q, goal, vmax=request.speed, min_horizon=fixed or first, max_horizon=fixed or 30.0,
                                    orientation_tolerance=np.radians(request.tolerance_deg), posture=posture, dynamics="double")
    except ValueError as error:
        raise Failed(FAILURE, str(error), {"goal": goal.tolist()}) from None
    if fixed and found["peak_speed"] > speed_cap(service.limits):
        raise Failed(FAILURE, f"{fixed:g} s is too short: the fastest joint would need {found['peak_speed']:.2f} rad/s, over the {speed_cap(service.limits):g} a move is planned at; give a longer duration",
                     {"peak_speed": found["peak_speed"]})
    lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
    upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
    if np.any(found["states"] < lower[:, None]) or np.any(found["states"] > upper[:, None]):
        raise Failed(FAILURE, "the planned path leaves a joint's range")
    knots = []
    for column in found["states"].T:
        whole = full.copy()
        whole[indices] = column
        knots.append(whole)
    refusal = path_check(service).path_refusal(knots)
    if refusal:
        raise Failed(FAILURE, f"the path is not clear: {refusal}")
    return found


def play(service, times, positions, rates):
    """Play a joint path on the arm the caller holds (`begin`). Raises Failed unless it ends at the path's end."""
    result = service.play(times, positions, rates, name="reach_pose")
    if result.cancelled:
        raise Failed(FAILURE, "cancelled; the arm holds where it is")
    if result.code != trajectory.SUCCESSFUL:
        raise Failed(FAILURE, result.message, {"worst_joint_error_rad": result.worst})


def move_to(service, request: Request, progress=lambda text: None) -> Outcome:
    """Plan and play the hand to the pose, on an arm the caller holds (`begin`), and measure it there. A move that stopped short is made again from where the arm is."""
    robot, side = service.robot, service.args.side
    planned, begun = None, time.time()
    try:
        for attempt in range(1 + CORRECTIONS):
            q, full, pose = hand(robot, side)
            goal = goal_pose(request, pose)
            mm, deg = errors(pose, goal)
            if mm <= request.tolerance_mm and deg <= request.tolerance_deg:
                return Outcome(SUCCESS, "already there" if attempt == 0 else f"reached, {mm:.1f} mm and {deg:.1f} deg off",
                               {"error_mm": mm, "error_deg": deg, "hand": pose[:3].tolist(), "planned_seconds": planned, "moves": attempt, "seconds": time.time() - begun})
            if service.cancel.is_set():
                raise Failed(FAILURE, "cancelled; the arm holds where it is")
            progress("planning" if attempt == 0 else "correcting")
            found = plan(service, request, goal, q, full)
            planned = planned or float(found["horizon"])
            times = np.linspace(0.0, found["horizon"], found["states"].shape[1])             # the plan's own timing, from rest to rest
            service._log(f"reach_pose {side}: hand {np.round(pose[:3], 3)} -> {np.round(goal[:3], 3)} ({mm:.0f} mm, {deg:.0f} deg off), "
                         f"{np.degrees(np.abs(found['states'][:, -1] - found['states'][:, 0]).max()):.0f} deg at most in {times[-1]:.1f} s")
            progress("moving")
            play(service, times, found["states"].T, found["rates"].T)
            time.sleep(SETTLE)
        _, _, pose = hand(robot, side)
        mm, deg = errors(pose, goal)
        data = {"error_mm": mm, "error_deg": deg, "hand": pose[:3].tolist(), "planned_seconds": planned, "moves": 1 + CORRECTIONS, "seconds": time.time() - begun}
        if mm <= request.tolerance_mm and deg <= request.tolerance_deg:
            return Outcome(SUCCESS, f"reached, {mm:.1f} mm and {deg:.1f} deg off", data)
        raise Failed(FAILURE, f"stopped {mm:.1f} mm and {deg:.1f} deg from the pose after {1 + CORRECTIONS} moves (tolerance {request.tolerance_mm:g} mm, {request.tolerance_deg:g} deg); "
                     "the arm holds there", data)
    except Failed as failed:
        return failed.outcome


def move_to_joints(service, target, speed: float | None = None) -> Outcome:
    """The same for a joint target, on an arm the caller holds: the straight joint-space line there, checked along its length, on a minimum-jerk profile."""
    robot, side = service.robot, service.args.side
    q, full, _ = hand(robot, side)
    system = robot.get_system()
    indices = np.asarray(system.get_task_space(side).get_joint_indices())
    target = np.asarray(target, dtype=float)
    try:
        if np.any(target < np.asarray(system.get_joint_limits_min().coefficients())[indices]) or np.any(target > np.asarray(system.get_joint_limits_max().coefficients())[indices]):
            raise Failed(FAILURE, "the target is outside the arm's joint limits")
        if float(np.abs(target - q).max()) <= service.limits.goal_tolerance / 2:
            return Outcome(SUCCESS, "already there")
        end = full.copy()
        end[indices] = target
        refusal = path_check(service).path_refusal([full, end])
        if refusal:
            raise Failed(FAILURE, f"the line is not clear: {refusal}")
        play(service, *schedule(np.stack([q, target], axis=1), speed or default_speed(service.limits), speed_cap(service.limits)))
        return Outcome(SUCCESS, "reached")
    except Failed as failed:
        return failed.outcome
