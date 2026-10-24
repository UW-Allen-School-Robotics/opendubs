#!/usr/bin/env python3
"""Drive one arm to its stow pose in POSITION mode (the driver's gravity feed-forward), under every gate the verified bring-up stages use.

It is not engage/park: those run the torque controller, which kicked the real arm and tripped the driver's speed limit (2026-10-23). This is the same
`go_to_joint_position` that `bringup.py move` and `lapse` ran on the arm, with the target set to the stow pose of /etc/opendubs/openarm-stow.conf.

Gates, in order: another controller or sender on the bus; CH5 MANUAL/AUTO, e-stop clear, --supervised (a person at the e-stop, head tracking OFF, neck centred);
the stow pose is configured (never guessed); the arm is within --max-distance-deg of it on every joint (a settle, not a long move; --allow-large lifts it); the
straight line is clear of the robot's body in the lane's twin (refuse under --min-clearance-mm); the plan is printed with the arm's real pose and you must type
`stow`; the deadman (CH6) must be HELD when it starts, and the permit is renewed only while CH6 and CH5 hold and the e-stop is clear, so letting go stops it
within the 0.5 s lease and the arm holds where it is.

    python goto_stow.py --side right --model <xml> --hardware-config <yaml> --assets <openarm_assets> --supervised

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
import engage  # noqa: E402
import guards  # noqa: E402
import motion_plan  # noqa: E402

TOLERANCE = 0.03          # rad: the arm ends this near the stow pose (the joints stop up to ~0.01 short, by friction)
MAX_SPEED = 0.3           # rad/s, the largest joint


def check_distance(start, target, max_deg: float) -> None:
    far = np.degrees(np.abs(np.asarray(start) - np.asarray(target)))
    if far.max() > max_deg:
        raise guards.Refused(f"joint {int(far.argmax()) + 1} is {far.max():.1f} deg from stow (the limit is {max_deg:g}): this settles an arm that is already near stow. "
                             "Put it nearer by hand, or --allow-large if you mean a long move and have checked the path.")


def plan_text(side, start, target, speed, closest, minimum_mm) -> str:
    delta = np.degrees(target - start)
    rows = "\n".join(f"  joint {j + 1}: {np.degrees(start[j]):+7.2f} deg  ->  {np.degrees(target[j]):+7.2f} deg   (moves {delta[j]:+6.2f} deg)" for j in range(7))
    near = (f"Closest approach along the straight line in the lane's twin: {closest[0]:.1f} mm ({closest[1]}); refused below {minimum_mm:g} mm. "
            "The twin was 6 deg optimistic on the one pose measured on the robot.") if closest else "The clearance was NOT checked (--skip-clearance)."
    return (f"The {side} arm goes to its stow pose in position mode, the largest joint at {speed:g} rad/s "
            f"(about {np.abs(target - start).max() / speed:.0f} s):\n{rows}\n{near}\n"
            "The head and the camera mount are not in the model: head tracking must be OFF with the neck centred, and you judge the path by eye.\n"
            "Hold CH6 from when you type `stow` until it ends; letting go stops it within about half a second and the arm holds where it is.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--side", choices=("left", "right"), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    p.add_argument("--hardware-config", default="")
    p.add_argument("--assets", default=None)
    p.add_argument("--sim-dir", default=None)
    p.add_argument("--stow-conf", default=None)
    p.add_argument("--speed", type=float, default=0.1, help="rad/s of the largest joint (at most 0.3)")
    p.add_argument("--max-distance-deg", type=float, default=25.0)
    p.add_argument("--allow-large", action="store_true")
    p.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM)
    p.add_argument("--skip-clearance", action="store_true")
    p.add_argument("--confirm", default=None, help="`stow`, for a script that has read the plan")
    p.add_argument("--supervised", action="store_true")
    p.add_argument("--dashboard", default=guards.DASHBOARD)
    p.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS)
    p.add_argument("--timeout", type=float, default=0.0, help="seconds to wait for the move (default: twice its duration + 5)")
    args = p.parse_args(argv)

    if not 0 < args.speed <= MAX_SPEED:
        print(f"refused: --speed must be above 0 and at most {MAX_SPEED:g} rad/s")
        return 1
    target = engage.stow_pose(args.side, args.stow_conf)
    if target is None:
        print(f"refused: no stow pose configured in {args.stow_conf or engage.STOW_CONF} (a line `{args.side.upper()}_STOW=<7 angles in degrees>`); it is deliberately not guessed")
        return 1
    if bringup.gate(args, motion=True):                         # the bus to itself; CH5, the e-stop and --supervised on a real bus; before anything is enabled
        return 1

    robot, indices, position = bringup.connect(args)
    keeper = bringup.keeper_for(args)
    try:
        start = position().copy()
        try:
            if not args.allow_large:
                check_distance(start, target, args.max_distance_deg)
            closest = None
            if not args.skip_clearance:
                clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
                closest = motion_plan.require_clearance(motion_plan.line_poses(start, target), clearance_of, args.min_clearance_mm)
            print(plan_text(args.side, start, target, args.speed, closest, args.min_clearance_mm))
            typed = args.confirm if args.confirm is not None else input("Is the arm clear for that? Type `stow` to move, anything else to stop: ").strip()
            if typed != "stow":
                raise guards.Refused("not confirmed")
        except guards.Refused as refused:
            print(f"refused: {refused}")
            print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
            return 1
        if not bringup.deadman_held(args):                       # CH6 held now, before the first permit is granted
            return 1

        finished = threading.Event()
        keeper.start()
        threading.Thread(target=bringup.go_to, args=(robot, args.side, target, args.speed, finished), daemon=True).start()
        began, limit = time.time(), args.timeout or (2 * np.abs(target - start).max() / args.speed + 5)
        while not finished.wait(0.5) and time.time() - began < limit:
            q = position()
            print(f"  [+{time.time() - began:4.1f}] q " + " ".join(f"{v:+.3f}" for v in q) + f" | farthest from stow {np.abs(q - target).max():.3f} rad", flush=True)
        end = position()
        error = float(np.abs(end - target).max())
        start_error = float(np.abs(start - target).max())
        print(f"ended {error:.4f} rad from stow (joint {int(np.abs(end - target).argmax()) + 1}); it started {start_error:.4f} rad away")
        ok = error <= TOLERANCE and (error < start_error or start_error <= TOLERANCE)
        return bringup.verdict(ok, f"within {TOLERANCE:g} rad of stow and nearer than it started" if ok else
                               f"{error:.4f} rad from stow, limit {TOLERANCE:g}: it stopped short (CH6 released, a trip, or friction)")
    finally:
        keeper.stop()
        robot.shutdown()


if __name__ == "__main__":
    sys.exit(main())
