#!/usr/bin/env python3
"""Move one arm's HAND by a small offset, orientation held: the target pose is the arm's current hand pose plus (dx, dy, dz), planned with gafro's iLQR
and driven in POSITION mode (the driver's gravity feed-forward), under every gate the verified bring-up stages use. Slice 1 of the pose controllers.

    python goto_hand.py --side right --supervised --offset-cm 3,0,0

The offset is in centimetres in the MODEL frame: +x forward (the way the face looks), +y the robot's left, +z up. Planning (hand_plan.py) is offline and
takes milliseconds; nothing is commanded until you have read the plan and typed `hand`. The plan is refused if iLQR cannot reach the target (a straight
arm cannot move its hand along its own axis, so most vertical moves from stow are refused), if any joint would leave its range, if the iLQR path strays
more than --max-bend-deg from a straight joint-space line (this slice replays a straight line, not the curve), if the twin finds the line within
--min-clearance-mm of the body, or if the move is bigger than --max-offset-cm or --max-joint-deg. CH6 must be HELD when it starts and the permit is
renewed only while it is held: letting go stops it within about half a second and the arm holds. The arm is left enabled and stiff.

"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import bringup  # noqa: E402
import guards  # noqa: E402
import hand_plan  # noqa: E402
import limits as robot_limits  # noqa: E402
import motion_plan  # noqa: E402

HAND_TOLERANCE_MM = 15.0      # the joints stop up to ~0.01 rad short by friction, which is 5 to 10 mm at the hand
JOINT_TOLERANCE = 0.03        # rad


def path_bend_deg(states: np.ndarray) -> float:
    """The farthest any state of the iLQR path is from the straight segment between its first and last, in degrees (geometry, not timing)."""
    a, b = states[:, 0], states[:, -1]
    axis = b - a
    length2 = float(axis @ axis)
    if length2 < 1e-12:
        return 0.0
    t = np.clip(((states - a[:, None]).T @ axis) / length2, 0.0, 1.0)
    nearest = a[:, None] + axis[:, None] * t[None, :]
    return float(np.degrees(np.linalg.norm(states - nearest, axis=0).max()))


def check_plan(plan: dict, lower, upper, max_bend_deg: float, max_joint_deg: float, max_offset_cm: float, offset) -> None:
    if np.linalg.norm(offset) * 100 > max_offset_cm:
        raise guards.Refused(f"the offset is {np.linalg.norm(offset) * 100:.1f} cm; the limit is {max_offset_cm:g} cm for this slice")
    states = plan["states"]
    if np.any(states < np.asarray(lower)[:, None]) or np.any(states > np.asarray(upper)[:, None]):
        raise guards.Refused("the planned path leaves a joint's range")
    moved = np.degrees(np.abs(states[:, -1] - states[:, 0]).max())
    if moved > max_joint_deg:
        raise guards.Refused(f"the largest joint would move {moved:.1f} deg; the limit is {max_joint_deg:g}")
    bend = path_bend_deg(states)
    if bend > max_bend_deg:
        raise guards.Refused(f"the iLQR path bends {bend:.1f} deg away from a straight joint-space line (limit {max_bend_deg:g}); this slice replays a straight line")


def plan_text(side, plan, speed, closest, minimum_mm) -> str:
    q0, q1 = plan["states"][:, 0], plan["states"][:, -1]
    rows = "\n".join(f"  joint {j + 1}: {np.degrees(q0[j]):+7.2f} deg  ->  {np.degrees(q1[j]):+7.2f} deg   (moves {np.degrees(q1[j] - q0[j]):+6.2f} deg)" for j in range(7))
    s, g_, e = plan["start"][:3] * 1000, plan["goal"][:3] * 1000, plan["end"][:3] * 1000
    near = (f"Closest approach along the straight line in the lane's twin: {closest[0]:.1f} mm ({closest[1]}); refused below {minimum_mm:g} mm. "
            "The twin was 6 deg optimistic on the one pose measured on the robot.") if closest else "The clearance was NOT checked (--skip-clearance)."
    return (f"The {side} hand moves by {np.round(g_ - s, 1)} mm (model frame: x forward, y left, z up), orientation held. iLQR planned it in milliseconds;\n"
            f"predicted hand error at the end {np.round(e - g_, 1)} mm, orientation drift {plan['orientation_drift']:.4f}{' (seeded from an elbow bend: a singular start)' if plan['seeded'] else ''}.\n"
            f"Driven in position mode as a straight joint-space line, the largest joint at {speed:g} rad/s "
            f"(about {np.abs(q1 - q0).max() / speed:.1f} s). The real arm stops up to ~10 mm short at the hand (joint friction).\n{rows}\n{near}\n"
            "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
            "Hold CH6 from when you type `hand` until it ends; letting go stops it within about half a second and the arm holds where it is.")


def over_torque(torque_peak: np.ndarray, limit: float) -> list:
    """The joints (numbered from 1) whose measured torque reached `limit` N m. In position mode the torque is kp times the lag behind the target, so a joint held by something
    the model does not have (the cables behind the right arm) draws more the further it is pushed: a cap says how hard this run may push."""
    return [int(j) + 1 for j in np.where(np.abs(torque_peak) >= limit)[0]]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--model", default=bringup.DEFAULT_MODEL, help="the robot's gafro model (default: the repo's own description/openarm/gafro/openarm_bimanual.xml)")
    p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    p.add_argument("--hardware-config", default=os.environ.get("ARM_HW_CONFIG", ""), help="the driver's config (default: $ARM_HW_CONFIG, which env.sh's arm_* functions use)")
    p.add_argument("--assets", default=None, help="the openarm_assets checkout the twin is built from (default: the repo's own description/openarm/mjcf)")
    p.add_argument("--sim-dir", default=None)
    p.add_argument("--offset-cm", required=True, help="dx,dy,dz in centimetres, model frame: +x forward, +y left, +z up")
    p.add_argument("--speed", type=float, default=0.05, help="rad/s of the largest joint (at most 0.2)")
    p.add_argument("--weight", type=int, default=10, help="terminal pose weight (the same term added this many times)")
    p.add_argument("--max-torque", type=float, default=12.0, help="N m: stop renewing the permit (the arm holds) if any joint's measured torque reaches this. In position mode it is "
                   "kp times the lag behind the target (kp 120 on joint 1: 0.09 rad is 10.8 N m); stiffer gains need a higher cap, on purpose. The driver's own trip is 27 N m on joint 1")
    p.add_argument("--max-offset-cm", type=float, default=6.0)
    p.add_argument("--max-joint-deg", type=float, default=15.0)
    p.add_argument("--max-bend-deg", type=float, default=1.0)
    p.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM)
    p.add_argument("--skip-clearance", action="store_true")
    p.add_argument("--confirm", default=None, help="`hand`, for a script that has read the plan")
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
    if bringup.gate(args, motion=True):                         # the bus to itself; CH5, the e-stop and --supervised on a real bus; before anything is enabled
        return 1

    robot, indices, position = bringup.connect(args)
    keeper = bringup.keeper_for(args)
    try:
        system = robot.get_system()
        task = system.get_task_space(args.side)
        start = position().copy()
        try:
            try:
                plan = hand_plan.plan_hand(system, task, start, offset, vmax=2.0, weight=args.weight)
            except ValueError as error:
                raise guards.Refused(str(error)) from None
            lower = np.asarray(system.get_joint_limits_min().coefficients())[np.asarray(task.get_joint_indices())]
            upper = np.asarray(system.get_joint_limits_max().coefficients())[np.asarray(task.get_joint_indices())]
            limits_note = robot_limits.NOTE
            check_plan(plan, lower, upper, args.max_bend_deg, args.max_joint_deg, args.max_offset_cm, offset)
            closest = None
            if not args.skip_clearance:
                clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
                closest = motion_plan.require_clearance(motion_plan.line_poses(plan["states"][:, 0], plan["states"][:, -1]), clearance_of, args.min_clearance_mm)
            print(limits_note)
            print(plan_text(args.side, plan, args.speed, closest, args.min_clearance_mm))
            typed = args.confirm if args.confirm is not None else input("Is the arm clear for that? Type `hand` to move, anything else to stop: ").strip()
            if typed != "hand":
                raise guards.Refused("not confirmed")
        except guards.Refused as refused:
            print(f"refused: {refused}")
            print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
            return 1
        if not bringup.deadman_held(args):                       # CH6 held now, before the first permit is granted
            return 1

        target_q = plan["states"][:, -1]
        finished = threading.Event()
        samples, recording = [], threading.Event()

        def record():
            while not recording.is_set():
                samples.append((time.time(), np.asarray(robot.get_state().get_torque(), dtype=float)[indices]))
                time.sleep(0.004)

        keeper.start()
        threading.Thread(target=record, daemon=True).start()
        threading.Thread(target=bringup.go_to, args=(robot, args.side, target_q, args.speed, finished), daemon=True).start()
        began = time.time()
        forced = None
        limit = 2 * np.abs(target_q - start).max() / args.speed + 5
        settled_since, last = None, position().copy()
        while not finished.wait(0.5) and time.time() - began < limit:
            q = position()
            moving = float(np.abs(q - last).max()) > 0.002
            last = q.copy()
            hand = hand_plan.hand_pose(task, q)
            recent = [x[1] for x in list(samples) if x[0] > time.time() - 0.5]
            peak = np.abs(np.array(recent)).max(axis=0) if recent else np.zeros(len(indices))
            print(f"  [+{time.time() - began:4.1f}] hand from start {np.round((hand[:3] - plan['start'][:3]) * 1000, 1)} mm | farthest joint from its target {np.abs(q - target_q).max():.3f} rad "
                  f"| largest torque {peak.max():.1f} N m (joint {int(peak.argmax()) + 1})", flush=True)
            over = over_torque(peak, args.max_torque)
            if over:
                forced = (f"FORCE LIMIT: joint(s) {over} reached {args.max_torque:g} N m (--max-torque). The permit is no longer renewed; the arm holds where it is. "
                          "If something holds the arm and you mean to push harder, raise --max-torque and the gains on purpose.")
                print(forced, flush=True)
                keeper.stop()
                break
            if not moving and np.abs(q - target_q).max() <= JOINT_TOLERANCE:
                settled_since = settled_since or time.time()
                if time.time() - settled_since > 1.0:
                    break
            else:
                settled_since = None
        recording.set()
        time.sleep(0.02)
        if samples:
            torques = np.abs(np.array([x[1] for x in list(samples)]))
            print("peak measured torque per joint (N m): " + "  ".join(f"{j + 1}: {torques[:, j].max():.1f}" for j in range(torques.shape[1])))
        end_q = position()
        hand = hand_plan.hand_pose(task, end_q)
        error = (hand[:3] - plan["goal"][:3]) * 1000
        joint_error = float(np.abs(end_q - target_q).max())
        moved = (hand[:3] - plan["start"][:3]) * 1000
        print(f"the hand moved {np.round(moved, 1)} mm (asked {np.round((plan['goal'][:3] - plan['start'][:3]) * 1000, 1)}); {np.round(error, 1)} mm from the target, "
              f"{joint_error:.4f} rad from the planned joints")
        ok = np.linalg.norm(error) <= HAND_TOLERANCE_MM and joint_error <= JOINT_TOLERANCE
        return bringup.verdict(ok, f"hand within {HAND_TOLERANCE_MM:g} mm of the target and joints within {JOINT_TOLERANCE:g} rad" if ok else
                               f"hand {np.linalg.norm(error):.1f} mm from the target, joints {joint_error:.3f} rad from the plan (stopped short: CH6 released, a trip, or friction)")
    finally:
        keeper.stop()
        robot.shutdown()


if __name__ == "__main__":
    sys.exit(main())
