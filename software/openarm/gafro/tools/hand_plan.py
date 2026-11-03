"""Plan a small hand move for one arm with gafro's iLQR: pose in, joint trajectory out (positions and velocities), offline, nothing commanded.

iLQR over the arm's joints, a pose objective on the hand and a control-effort cost along the way. `plan_hand` and `plan_pose` default to a single integrator (state = the joints,
control = joint velocity, the pose at the last knot); `plan_pose(dynamics="double")` is a double integrator (state = joints and velocities from rest, control = acceleration, the pose
held over the last knots), whose plan starts and ends at rest and carries its own timing. The horizon is the smallest found that keeps every joint under `vmax`. At a singular start (the straight stow arm cannot move its hand along
its own axis to first order) the first solve stalls, and the plan is retried from a seed that bends the elbow.
"""
from __future__ import annotations

import numpy as np

import gafro as g
import gafro.objective as obj
import gafro.optimization as opt

KNOTS = 30
STOP_SPAN = 6        # knots at the end of a double-integrator plan that hold the pose: the arm arrives early and stands still


def hand_pose(task, q) -> np.ndarray:
    """(px, py, pz, qw, qx, qy, qz) of the hand for the arm's seven joints, in the model frame (+x forward, +y left, +z up)."""
    return np.asarray(task.compute_ee_motor(np.asarray(q, dtype=float)).to_position_quaternion())


def _solve(system, task, q0, target, horizon_s, effort, weight, seed, double=False):
    """iLQR for the hand reaching `target` at the end of `horizon_s`. Single integrator: state = the joints, control = joint velocity, a pose cost at the last knot. Double: state =
    (joints, joint velocity) from rest, control = joint acceleration, and the pose cost over the last `STOP_SPAN` of the knots: nothing in a problem addresses the velocity, so the
    arm is asked to be AT the pose for a while, which is to arrive and stand still. Returns (states (7 or 14, N+1), controls)."""
    q0 = np.asarray(q0, dtype=float)
    dynamics = opt.DoubleIntegratorSystem(14, 7) if double else opt.SingleIntegratorSystem(7, 7)
    configuration = opt.IterativeLQRConfiguration()
    configuration.set_regularization(1e-2).set_maximum_iterations(100)
    problem = opt.Problem(dynamics, KNOTS, horizon_s / KNOTS)
    problem.set_initial_state(np.r_[q0, np.zeros(7)] if double else q0)
    problem[0, KNOTS - 1] += opt.control_effort(effort)
    term = g.bind(system, task, obj.PoseObjective(target))
    place = problem[KNOTS - STOP_SPAN, KNOTS] if double else problem[KNOTS]
    for _ in range(int(weight)):                                 # a bound objective cannot be scaled: the same term added `weight` times is that weight
        place += term
    solver = opt.IterativeLQR(configuration)
    solution = solver.solve(problem) if seed is None else solver.solve(problem, seed)
    return np.asarray(solution.get_state_trajectory()), np.asarray(solution.get_control_trajectory())


def orientation_error(a, b) -> float:
    """The angle in radians between two hand poses' orientations (their (w, x, y, z) quaternions, sign-free)."""
    return float(2.0 * np.arccos(np.clip(abs(float(np.dot(a[3:], b[3:]))), 0.0, 1.0)))


def plan_pose(system, task, q0, goal, vmax=0.2, effort=1e-6, weight=1, min_horizon=2.0, max_horizon=30.0, tolerance_mm=2.0, orientation_tolerance=None, posture=None, dynamics="single"):
    """Plan the hand from the arm's joints `q0` to the pose `goal` (px, py, pz, qw, qx, qy, qz; the model frame).

    `posture`: the joints the plan should end at, from the self-motion arc of the goal (reach.Reach.best_posture). It seeds the first solve with the straight line there, so iLQR
    stays in that posture's basin instead of the one its zero seed falls into; the pose is still what is planned for. `orientation_tolerance` (rad): a plan whose end is further off
    than that is not accepted; None leaves the orientation to the cost, as `plan_hand` does.

    `dynamics`: "single" plans joint velocities (state = the joints; the path is then timed by the caller), "double" plans joint accelerations from rest to rest (state = joints and
    velocities), so the plan carries its own timing: `horizon` is its duration, `rates` the joint velocities at the knots, and `vmax` bounds the fastest joint of the plan.

    Returns dict(states (7, N+1), rates (7, N+1) or None, controls (7, N), horizon, dt, error_mm (3,), orientation_error, orientation_drift, peak_speed, seeded). Raises ValueError if no plan reaches the goal."""
    q0 = np.asarray(q0, dtype=float)
    start = hand_pose(task, q0)
    goal = np.asarray(goal, dtype=float)
    distance = float(np.linalg.norm(goal[:3] - start[:3]))
    target = g.Motor.from_position_quaternion(*goal)
    double = dynamics == "double"
    seeds = [None]
    if posture is not None:
        line = np.asarray(posture, dtype=float) - q0
        if double:      # rest to rest along the line: accelerate for half the horizon and brake for the other half
            def triangle(horizon):
                half = np.where(np.arange(KNOTS) < KNOTS // 2, 1.0, -1.0)
                return (4.0 * line / horizon**2)[:, None] * half[None, :]
            seeds.insert(0, triangle)
        else:
            seeds.insert(0, lambda horizon: np.tile((line / horizon)[:, None], (1, KNOTS)))
    for sign in (1.0, -1.0):                                    # an elbow bend either way, for a singular start
        seed = np.zeros((7, KNOTS))
        if double:
            seed[3, : KNOTS // 2], seed[3, KNOTS // 2 :] = 0.2 * sign, -0.2 * sign
        else:
            seed[3, :] = 0.05 * sign
        seeds.append(seed)

    def accepted(result):
        within = np.linalg.norm(result["error_mm"]) <= max(tolerance_mm, 0.1 * distance * 1000)
        return within and (orientation_tolerance is None or result["orientation_error"] <= orientation_tolerance)

    best = None
    for seed_index, seed in enumerate(seeds):
        horizon = min_horizon
        while True:
            states, controls = _solve(system, task, q0, target, horizon, effort, weight, seed(horizon) if callable(seed) else seed, double)
            peak = float(np.abs(states[7:] if double else controls).max())
            if peak <= vmax or horizon >= max_horizon:
                break
            horizon = min(max_horizon, horizon * max(1.2, peak / vmax))
        rates = states[7:].copy() if double else None
        states = states[:7]
        end = hand_pose(task, states[:, -1])
        error = (end[:3] - goal[:3]) * 1000
        result = dict(states=states, rates=rates, controls=controls, horizon=horizon, dt=horizon / KNOTS, error_mm=error, peak_speed=peak, orientation_error=orientation_error(end, goal),
                      orientation_drift=float(np.linalg.norm(end[3:] - start[3:])), seeded=seed_index > 0, start=start, goal=goal, end=end)
        if best is None or np.linalg.norm(error) < np.linalg.norm(best["error_mm"]):
            best = result
        if accepted(result):
            return result
    if np.linalg.norm(best["error_mm"]) > 0.5 * distance * 1000 + tolerance_mm:
        raise ValueError(f"no plan reaches the target: the best leaves the hand {np.linalg.norm(best['error_mm']):.1f} mm away (unreachable, or a singular start)")
    if orientation_tolerance is not None and best["orientation_error"] > orientation_tolerance:
        raise ValueError(f"no plan reaches the target's orientation: the best leaves it {np.degrees(best['orientation_error']):.1f} deg off")
    return best


def plan_hand(system, task, q0, offset, vmax=0.2, effort=1e-6, weight=1, min_horizon=2.0, max_horizon=30.0, tolerance_mm=2.0):
    """The hand moved by `offset` (metres, the model frame), orientation held: `plan_pose` to the current pose plus the offset."""
    goal = hand_pose(task, q0) + np.r_[np.asarray(offset, dtype=float), np.zeros(4)]
    return plan_pose(system, task, q0, goal, vmax, effort, weight, min_horizon, max_horizon, tolerance_mm)
