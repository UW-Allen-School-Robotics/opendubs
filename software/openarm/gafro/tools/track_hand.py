#!/usr/bin/env python3
"""Move one arm's HAND by a small offset under a gafro CONTROLLER that takes the target POSE as its input. Slice 2 of the pose controllers (velocity);
the torque `track` controller of controllers.yaml uses the same runner (slice 3, not run on the arm yet: see engage_probe.py's kick).

    python track_hand.py --controller velocity --side right --model <xml> --hardware-config <yaml> --assets <assets> --supervised --offset-cm 4,0,0

What differs from goto_hand.py: there the arm replays a straight joint-space line in the driver's POSITION mode. Here a spec-built controller (the YAML spec of
gafro.control.spec, built the way engage.py builds `track`) runs in the interface's own loop. Its target pose is a MotorReferenceSource, which this publishes: the
hand path is iLQR's (hand_plan.py: geometry only), timed by a minimum-jerk law, as a pose reference and a joint (posture) reference. `velocity` is
type: velocity (pose -> velocity, resolved through the kinematics) in the driver's VELOCITY mode: kp = 0 and the command under kd, so the joint torque is
kd times the speed error. With kd 2 a 0.3 rad/s error is 0.6 N m, about the joint friction: the arm may stall. Raise `kd` (at most 5) in a COPY of the robot's
hardware config for this run.

Every gate of goto_hand.py applies (bus, CH5, e-stop, --supervised, joint range, straight-path and size limits, twin clearance, typed `hand`, CH6 held at the start and
for every permit renewal). The arm is left enabled and stiff.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import bringup  # noqa: E402
import engage  # noqa: E402
import goto_hand  # noqa: E402
import guards  # noqa: E402
import hand_plan  # noqa: E402
import motion_plan  # noqa: E402

KNOTS = 40


def velocity_controller(gain: float, linear: float, angular: float, posture_gain: float, posture_speed: float, limits: bool,
                        integral: float = 0.0, integral_limit: float = 0.02) -> dict:
    """The spec of a velocity controller with a pose reference: the hand follows `reference`, the posture stage uses the redundancy.

    `integral` > 0 makes the pose law a PID: v = gain * e + integral * (the integral of e), the integral bounded by `integral_limit` and the speed by `linear`
    (the spec takes one number for every row there, so the angular cap is the same number). A stuck joint's few-millimetre error then builds up speed until it breaks free.
    """
    if integral > 0:
        law = {"pid": {"stiffness": gain, "damping": 0.0, "integral": integral, "limits": {"integral": integral_limit, "output": linear}}}
    else:
        law = {"pd": {"gain": gain, "max_speed": {"linear": linear, "angular": angular}}}
    description = {
        "inputs": {"reference": "pose", "posture": "joints"},
        "type": "velocity",
        "regularization": 0.05,
        "stages": [
            {"name": "pose", "input": "pose", "objective": {"pose": {"target": "reference"}}, "law": law, "output": "velocity"},
            {"name": "posture", "input": "joints", "objective": {"posture": {"target": "posture"}},
             "law": {"pd": {"gain": posture_gain, "max_speed": posture_speed}}, "output": "velocity"},
        ],
    }
    if limits:
        description["limits"] = {"joint_velocity": "model"}
    return description


def reference_knots(path: np.ndarray, duration: float, knots: int = KNOTS):
    """The iLQR path (7, N+1) timed by minimum jerk: `knots`+1 joint vectors at spacing duration/knots, leaving and arriving at rest."""
    steps = np.linalg.norm(np.diff(path, axis=1), axis=0)
    cumulative = np.r_[0.0, np.cumsum(steps)]
    total = cumulative[-1] if cumulative[-1] > 0 else 1.0
    out = []
    for k in range(knots + 1):
        s = engage.minimum_jerk(k / knots) * total
        out.append(np.array([np.interp(s, cumulative, path[j]) for j in range(path.shape[0])]))
    return out


def duration_for_path(path: np.ndarray, vmax: float) -> float:
    return engage.duration_for(path[:, 0], path[:, -1], vmax)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--controller", choices=("velocity", "track"), default="velocity", help="`velocity` is built here; `track` is the torque controller of controllers.yaml")
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    p.add_argument("--hardware-config", default="")
    p.add_argument("--assets", default=None)
    p.add_argument("--sim-dir", default=None)
    p.add_argument("--offset-cm", required=True)
    p.add_argument("--speed", type=float, default=0.1, help="the largest joint's peak-average speed of the reference, rad/s (at most 0.2)")
    p.add_argument("--gain", type=float, default=2.0, help="velocity controller: pose error to speed, 1/s")
    p.add_argument("--hand-speed", type=float, default=0.1, help="velocity controller: the hand's speed cap, m/s")
    p.add_argument("--integral", type=float, default=0.0, help="velocity controller: integral gain (1/s^2); above 0 the pose law is a PID that pushes through stiction")
    p.add_argument("--integral-limit", type=float, default=0.02, help="velocity controller: bound of the integral of the pose error (m s)")
    p.add_argument("--weight", type=int, default=10)
    p.add_argument("--max-offset-cm", type=float, default=6.0)
    p.add_argument("--max-joint-deg", type=float, default=15.0)
    p.add_argument("--max-bend-deg", type=float, default=1.0)
    p.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM)
    p.add_argument("--skip-clearance", action="store_true")
    p.add_argument("--no-model-limits", action="store_true", help="build the velocity controller without limits: {joint_velocity: model}")
    p.add_argument("--confirm", default=None)
    p.add_argument("--supervised", action="store_true")
    p.add_argument("--dashboard", default=guards.DASHBOARD)
    p.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS)
    args = p.parse_args(argv)

    try:
        offset = np.array([float(v) for v in args.offset_cm.split(",")]) / 100.0
        assert offset.shape == (3,)
    except (ValueError, AssertionError):
        print("refused: --offset-cm wants three numbers, dx,dy,dz")
        return 1
    if not 0 < args.speed <= 0.2:
        print("refused: --speed must be above 0 and at most 0.2 rad/s")
        return 1
    if args.controller == "track":
        print("refused: the torque `track` controller kicked the real arm (engage, 2026-10-23); it is not run until that is understood. --controller velocity only.")
        return 1
    if bringup.gate(args, motion=True):
        return 1

    from gafro import control

    robot, indices, position = bringup.connect(args)
    keeper = bringup.keeper_for(args)
    try:
        system = robot.get_system()
        task = system.get_task_space(args.side)
        state = lambda: np.asarray(robot.get_state().get_position(), dtype=float)  # noqa: E731
        start_all = state().copy()
        start = start_all[indices]
        try:
            try:
                plan = hand_plan.plan_hand(system, task, start, offset, vmax=2.0, weight=args.weight)
            except ValueError as error:
                raise guards.Refused(str(error)) from None
            lower = np.asarray(system.get_joint_limits_min().coefficients())[indices]
            upper = np.asarray(system.get_joint_limits_max().coefficients())[indices]
            goto_hand.check_plan(plan, lower, upper, args.max_bend_deg, args.max_joint_deg, args.max_offset_cm, offset)
            closest = None
            if not args.skip_clearance:
                clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
                closest = motion_plan.require_clearance(motion_plan.line_poses(plan["states"][:, 0], plan["states"][:, -1]), clearance_of, args.min_clearance_mm)
            duration = duration_for_path(plan["states"], args.speed)
            print(goto_hand.plan_text(args.side, plan, args.speed, closest, args.min_clearance_mm).split("\nDriven in position mode")[0])
            print(f"Run by the `{args.controller}` controller (a spec-built, pose -> velocity controller in the driver's VELOCITY mode: kp = 0, the command under kd), its pose reference "
                  f"timed by minimum jerk over {duration:.1f} s, gain {args.gain:g} /s, hand speed cap {args.hand_speed:g} m/s"
                  f"{f', integral {args.integral:g} /s^2 (bound {args.integral_limit:g} m s)' if args.integral > 0 else ''}. It may stall short if kd is too low for the joint friction.\n"
                  "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
                  "Hold CH6 from when you type `hand` until it ends; letting go stops it within about half a second and the arm holds where it is.")
            typed = args.confirm if args.confirm is not None else input("Is the arm clear for that? Type `hand` to move, anything else to stop: ").strip()
            if typed != "hand":
                raise guards.Refused("not confirmed")
        except guards.Refused as refused:
            print(f"refused: {refused}")
            print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
            return 1
        if not bringup.deadman_held(args):
            return 1

        controllers = engage.load_controllers()
        controllers["velocity"] = velocity_controller(args.gain, args.hand_speed, 0.5, 1.0, 0.3, not args.no_model_limits, args.integral, args.integral_limit)
        pose0 = task.compute_ee_motor(start)
        posture = control.JointReferenceSource(start_all.copy(), horizon=0.5)
        posture.set_staleness(control.Staleness.Hold)
        posture.prime(2)
        reference = control.MotorReferenceSource(pose0, horizon=0.5)
        reference.set_staleness(control.Staleness.Hold)
        reference.prime(2)

        keeper.start()
        robot.control(engage.build(args.controller, controllers, system, args.side, start_all, pose0, reference, posture), False)
        time.sleep(0.5)                                          # holding where it is before anything moves

        q_ref = reference_knots(plan["states"], duration)
        motors, joints = [], []
        for q in q_ref:
            motors.append(task.compute_ee_motor(q))
            full = start_all.copy()
            full[indices] = q
            joints.append(full)
        began = time.time()
        now = control.monotonic_now()
        reference.publish(control.MotorReferenceChunk(now, duration / KNOTS, motors))     # the spacing between knots, not the length of the chunk
        posture.publish(control.JointReferenceChunk(now, duration / KNOTS, joints))
        while time.time() - began < duration + 1.5:
            time.sleep(0.5)
            hand = hand_plan.hand_pose(task, state()[indices])
            print(f"  [+{time.time() - began:4.1f}] hand from start {np.round((hand[:3] - plan['start'][:3]) * 1000, 1)} mm | farthest joint from its target "
                  f"{np.abs(state()[indices] - plan['states'][:, -1]).max():.3f} rad", flush=True)
        end_q = state()[indices]
        hand = hand_plan.hand_pose(task, end_q)
        error = (hand[:3] - plan["goal"][:3]) * 1000
        joint_error = float(np.abs(end_q - plan["states"][:, -1]).max())
        print(f"the hand moved {np.round((hand[:3] - plan['start'][:3]) * 1000, 1)} mm (asked {np.round((plan['goal'][:3] - plan['start'][:3]) * 1000, 1)}); "
              f"{np.round(error, 1)} mm from the target, {joint_error:.4f} rad from the planned joints")
        ok = np.linalg.norm(error) <= goto_hand.HAND_TOLERANCE_MM and joint_error <= goto_hand.JOINT_TOLERANCE
        return bringup.verdict(ok, "hand within tolerance under the velocity controller" if ok else
                               f"hand {np.linalg.norm(error):.1f} mm from the target, joints {joint_error:.3f} rad from the plan (a stall: kd too low for the friction, or CH6 released, or a trip)")
    finally:
        keeper.stop()
        robot.shutdown()


if __name__ == "__main__":
    sys.exit(main())
