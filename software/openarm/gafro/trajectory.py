"""Play a joint trajectory on one arm: what the old stack's `follow_joint_trajectory` did, without ROS. The core of arm_service.py's slice 3.

The path is played by the position controller of tools/position_play.py (a posture stage on its own anchor, the planned rate fed forward; the motor's PD and the
driver's gravity feed-forward close the loop), the way tools/play_show.py plays a show, with the same stops: the run ends and the arm holds if a joint strays from the path,
a measured torque reaches the limit, or a joint vibrates. A cancel does the same. What it does not do, that play_show.py does: the twin-clearance check and the typed
confirmation. The show runner validated the show before sending it; a path that arrives here is checked only against the model's joint ranges, the speed limit and where
the arm is.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

import guards  # noqa: E402

# control_msgs/FollowJointTrajectory.Result error codes
SUCCESSFUL, INVALID_GOAL, PATH_TOLERANCE_VIOLATED, GOAL_TOLERANCE_VIOLATED = 0, -1, -4, -5

PERIOD = 0.02          # s between the knots handed to the controller (what the choreography runner samples at)
VIBRATION_SAMPLES = 6


@dataclasses.dataclass
class Limits:
    """STARTING VALUES, the ones play_show.py has (none has been run on an arm for a show)."""
    max_speed: float = 1.3            # rad/s: a faster path is refused
    start_tolerance_deg: float = 3.0  # the arm must start this near the path's first point
    max_error: float = 0.2            # rad from the path: stop
    goal_tolerance: float = 0.05      # rad from the path's end at the end (the simulator ends 0.034 short: position mode has no integral action)
    max_torque: float = 12.0          # N m
    max_joint_speed: float = 2.5      # rad/s for ~12 ms: vibration, stop
    gain: float = 20.0
    max_lead: float = 0.02
    leash: float = 0.15
    offset_gain: float = 2.0
    max_command_speed: float = 0.5
    lead_in: float = 0.2              # s between publishing the path and its first knot: the show is this much later than its clock says


@dataclasses.dataclass
class Result:
    code: int
    message: str
    worst: float = 0.0
    cancelled: bool = False


def resample(times, positions, velocities=None, period: float = PERIOD):
    """The path on an even grid of `period`: (times, positions, rates). Cubic Hermite where `velocities` are given, linear otherwise."""
    times, positions = np.asarray(times, dtype=float), np.asarray(positions, dtype=float)
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("a trajectory needs at least two points, in increasing time")
    grid = np.arange(0.0, times[-1] - times[0] + 1e-9, period) + times[0]
    if grid[-1] < times[-1] - 1e-9:
        grid = np.append(grid, times[-1])
    k = np.clip(np.searchsorted(times, grid, side="right") - 1, 0, len(times) - 2)
    h = (times[k + 1] - times[k])[:, None]
    s = (grid - times[k])[:, None] / h
    p0, p1 = positions[k], positions[k + 1]
    if velocities is None or not np.any(velocities):
        q = p0 + (p1 - p0) * s
        qd = (p1 - p0) / h
    else:
        v0, v1 = np.asarray(velocities, dtype=float)[k], np.asarray(velocities, dtype=float)[k + 1]
        h00, h10, h01, h11 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
        q = h00 * p0 + h10 * h * v0 + h01 * p1 + h11 * h * v1
        d00, d10, d01, d11 = 6 * s**2 - 6 * s, 3 * s**2 - 4 * s + 1, -6 * s**2 + 6 * s, 3 * s**2 - 2 * s
        qd = (d00 * p0 + d10 * h * v0 + d01 * p1 + d11 * h * v1) / h
    return grid - times[0], q, qd


def follow(robot, side: str, times, positions, velocities, keeper, cancel: threading.Event, limits: Limits = Limits(), log=print, record: str | None = None) -> Result:
    """Play the path (`positions`: K rows of the arm's 7 joints, at `times` from now) and return how it ended. `keeper` is this goal's lease on the permit (arm_service.SharedPermit; started
    here, after the path passes its checks, and released on every way out). `cancel` is set to end the run early.
    `record`: a CSV path for the run's samples (about 500 Hz: measured position, velocity and torque, the path's position and the error), written however the run ends."""
    import play_show
    import position_play
    from gafro import control

    system = robot.get_system()
    task = system.get_task_space(side)
    indices = np.asarray(task.get_joint_indices())
    start_all = np.asarray(robot.get_state().get_position(), dtype=float).copy()
    start = start_all[indices]

    try:
        grid, joints, rates = resample(times, positions, velocities)
        lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
        upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
        why = play_show.start_refusal(start, joints[0], limits.start_tolerance_deg) or play_show.path_refusal(joints, rates, lower, upper, limits.max_speed)
    except (ValueError, IndexError) as error:
        why = str(error)
    if why:
        return Result(INVALID_GOAL, why)

    full = np.tile(start_all, (len(grid), 1))
    full[:, indices] = joints
    samples, done = [], threading.Event()
    vibrating = {"run": 0, "joints": []}

    def sample():
        while not done.is_set():
            s = robot.get_state()
            v = np.asarray(s.get_velocity(), dtype=float)[indices]
            samples.append((time.time(), np.asarray(s.get_position(), dtype=float)[indices], np.asarray(s.get_torque(), dtype=float)[indices], v))
            over = np.abs(v) > limits.max_joint_speed
            vibrating["run"] = vibrating["run"] + 1 if over.any() else 0
            if vibrating["run"] >= VIBRATION_SAMPLES and not vibrating["joints"]:
                vibrating["joints"] = [int(j) + 1 for j in np.where(over)[0]]
            time.sleep(0.002)

    stopped, worst = None, 0.0
    try:
        try:
            keeper.start()                       # the gate answers here: CH5, the e-stop and CH6 must say yes before the first permit
        except guards.Refused as refused:
            return Result(INVALID_GOAL, str(refused))
        posture, rate_source = position_play.start(robot, system, side, start_all, task.compute_ee_motor(start), limits.gain, limits.max_lead, limits.max_command_speed,
                                                   True, limits.leash, limits.offset_gain)
        time.sleep(0.1)                          # holding where it is before the path starts
        begin_clock, began = control.monotonic_now() + limits.lead_in, time.time() + limits.lead_in
        threading.Thread(target=sample, daemon=True).start()
        position_play.publish(posture, rate_source, begin_clock, PERIOD, full, rates)

        while time.time() - began < grid[-1] + 1.0 and not cancel.is_set():
            time.sleep(0.01)
            elapsed = time.time() - began
            if elapsed <= 0 or not samples:
                continue
            _, q, tau, _ = samples[-1]
            error = play_show.tracking_error(grid, joints, elapsed, q)
            worst = max(worst, error)
            if vibrating["joints"]:
                stopped = f"VIBRATION: joint(s) {vibrating['joints']} exceeded {limits.max_joint_speed:g} rad/s"
            elif error > limits.max_error:
                stopped = f"TRACKING: a joint is {error:.2f} rad from the path (limit {limits.max_error:g})"
            elif np.abs(tau).max() >= limits.max_torque:
                stopped = f"FORCE LIMIT: joint {int(np.abs(tau).argmax()) + 1} reached {limits.max_torque:g} N m"
            if stopped:
                break
        if cancel.is_set():
            return Result(SUCCESSFUL, "cancelled; the arm holds where it is", worst, cancelled=True)
        if stopped:
            log(stopped)
            return Result(PATH_TOLERANCE_VIOLATED, stopped + "; the arm holds where it is", worst)
        final = float(np.abs(np.asarray(robot.get_state().get_position(), dtype=float)[indices] - joints[-1]).max())
        if final > limits.goal_tolerance:
            return Result(GOAL_TOLERANCE_VIOLATED, f"{final:.3f} rad from the path's end (limit {limits.goal_tolerance:g})", worst)
        return Result(SUCCESSFUL, f"followed within {worst:.3f} rad", worst)
    finally:
        done.set()
        keeper.stop()
        if record and samples:
            try:
                write_recording(record, list(samples), grid, joints, began)
                log(f"recorded {record}")
            except OSError as error:
                log(f"could not write {record}: {error}")


def write_recording(path: str, samples, grid, joints, began: float) -> None:
    """The run's samples as a CSV: t (s from the path's start), q1..7, qd1..7, tau1..7, ref1..7 (where the path is), error (the largest joint's distance from it)."""
    import csv

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t"] + [f"q{j + 1}" for j in range(7)] + [f"qd{j + 1}" for j in range(7)] + [f"tau{j + 1}" for j in range(7)]
                        + [f"ref{j + 1}" for j in range(7)] + ["error"])
        for when, q, tau, v in samples:
            elapsed = when - began
            ref = np.array([np.interp(elapsed, grid, joints[:, j]) for j in range(joints.shape[1])])
            writer.writerow([f"{elapsed:.4f}"] + [f"{x:.5f}" for x in np.r_[q, v, tau, ref]] + [f"{np.abs(q - ref).max():.5f}"])
