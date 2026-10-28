"""Plan a small hand move for one arm with gafro's iLQR: pose in, joint trajectory out (positions and velocities), offline, nothing commanded.

Single-integrator iLQR (state = the arm's joints, control = joint velocity), a terminal pose objective on the hand (orientation held) and a control-effort
cost along the way. The horizon is the smallest that keeps every joint under `vmax`. At a singular start (the straight stow arm cannot move its hand along
its own axis to first order) the first solve stalls, and the plan is retried from a seed that bends the elbow.
"""
from __future__ import annotations

import numpy as np

import gafro as g
import gafro.objective as obj
import gafro.optimization as opt

KNOTS = 30


def hand_pose(task, q) -> np.ndarray:
    """(px, py, pz, qw, qx, qy, qz) of the hand for the arm's seven joints, in the model frame (+x forward, +y left, +z up)."""
    return np.asarray(task.compute_ee_motor(np.asarray(q, dtype=float)).to_position_quaternion())


def _solve(system, task, q0, target, horizon_s, effort, weight, seed):
    dynamics = opt.SingleIntegratorSystem(7, 7)
    configuration = opt.IterativeLQRConfiguration()
    configuration.set_regularization(1e-2).set_maximum_iterations(100)
    problem = opt.Problem(dynamics, KNOTS, horizon_s / KNOTS)
    problem.set_initial_state(np.asarray(q0, dtype=float))
    problem[0, KNOTS - 1] += opt.control_effort(effort)
    term = g.bind(system, task, obj.PoseObjective(target))
    for _ in range(int(weight)):                                 # a bound objective cannot be scaled: the same term added `weight` times is that weight
        problem[KNOTS] += term
    solver = opt.IterativeLQR(configuration)
    solution = solver.solve(problem) if seed is None else solver.solve(problem, seed)
    return np.asarray(solution.get_state_trajectory()), np.asarray(solution.get_control_trajectory())


def plan_hand(system, task, q0, offset, vmax=0.2, effort=1e-6, weight=1, min_horizon=2.0, max_horizon=30.0, tolerance_mm=2.0):
    """Returns dict(states (7, N+1), controls (7, N), horizon, dt, error_mm (3,), orientation_drift, peak_speed, seeded). Raises ValueError if no plan reaches the target."""
    q0 = np.asarray(q0, dtype=float)
    start = hand_pose(task, q0)
    goal = start + np.r_[np.asarray(offset, dtype=float), np.zeros(4)]
    target = g.Motor.from_position_quaternion(*goal)
    seeds = [None]
    for sign in (1.0, -1.0):                                    # an elbow bend either way, for a singular start
        seed = np.zeros((7, KNOTS)); seed[3, :] = 0.05 * sign
        seeds.append(seed)

    best = None
    for seed_index, seed in enumerate(seeds):
        horizon = min_horizon
        while True:
            states, controls = _solve(system, task, q0, target, horizon, effort, weight, seed)
            peak = float(np.abs(controls).max())
            if peak <= vmax or horizon >= max_horizon:
                break
            horizon = min(max_horizon, horizon * max(1.2, peak / vmax))
        end = hand_pose(task, states[:, -1])
        error = (end[:3] - goal[:3]) * 1000
        result = dict(states=states, controls=controls, horizon=horizon, dt=horizon / KNOTS, error_mm=error, peak_speed=peak,
                      orientation_drift=float(np.linalg.norm(end[3:] - start[3:])), seeded=seed_index > 0, start=start, goal=goal, end=end)
        if best is None or np.linalg.norm(error) < np.linalg.norm(best["error_mm"]):
            best = result
        if np.linalg.norm(error) <= max(tolerance_mm, 0.1 * np.linalg.norm(offset) * 1000):
            return result
    if np.linalg.norm(best["error_mm"]) > 0.5 * np.linalg.norm(offset) * 1000 + tolerance_mm:
        raise ValueError(f"no plan reaches the target: the best leaves the hand {np.linalg.norm(best['error_mm']):.1f} mm away (unreachable, or a singular start)")
    return best
