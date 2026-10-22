#!/usr/bin/env python3
"""First-run checks of one arm on the gafro path, one stage per subcommand, each printing its own verdict.

    PY=<venv>/bin/python
    $PY bringup.py hold  --side right --model <model.xml> --hardware-config <hardware.yaml>
    $PY bringup.py lapse --side right ...
    $PY bringup.py move  --side right --joint 3 --delta 0.05 ...

Stage 3 `hold`: the motors are verified, enabled and held where the arm is, with no permit, so nothing is commanded and the arm must not
move. Stage 4 `lapse`: a slow move is under way on a permit; the permit then stops being renewed, as when whatever renews it dies, and the arm
must stop where it is. Stage 5 `move`: one joint a small step and back, on a permit.

For the first run of an arm: supported, a person at the e-stop, one arm at a time, and `require_permit: true` in the hardware config. The
verdicts are the thresholds below, which are starting values like everything else. The same commands run against the simulator with
`--backend mujoco` (no permit there, so `lapse` is skipped), which is how the script itself was tried.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from engage import PermitKeeper, permit_groups  # noqa: E402

HOLD_TOLERANCE = 0.02      # rad, how far an arm that is only held may drift in the hold stage
LAPSE_TOLERANCE = 0.01     # rad, how far an arm may still travel after its permit stops being renewed
MOVE_TOLERANCE = 0.03      # rad, how far from the target a small move may end
MOVE_TIMEOUT = 40.0        # s


def connect(args):
    import gafro  # noqa: F401  -- before gafro_robot_interface
    import gafro_robot_interface as ri

    if args.backend == "hardware":
        import openarm_driver  # noqa: F401  -- registers `driver: openarm`; after gafro_robot_interface

    robot = ri.RobotInterface(ri.Configuration(args.model, args.backend, args.hardware_config))
    robot.initialize()
    indices = np.asarray(robot.get_system().get_task_space(args.side).get_joint_indices())
    return robot, indices, lambda: np.asarray(robot.get_state().get_position(), dtype=float)[indices]


def verdict(ok: bool, text: str) -> int:
    print(("PASS: " if ok else "FAIL: ") + text)
    return 0 if ok else 1


def hold(args) -> int:
    """Enabled and held, nothing commanded: the arm does not move."""
    robot, _, position = connect(args)
    try:
        start, worst = position().copy(), 0.0
        print(f"start {np.round(start, 3)}; watching {args.seconds:g} s with no permit")
        end = time.time() + args.seconds
        while time.time() < end:
            time.sleep(1.0)
            worst = max(worst, float(np.abs(position() - start).max()))
            print(f"  max drift {worst:.4f} rad   now {np.round(position(), 3)}")
        return verdict(worst <= HOLD_TOLERANCE, f"drifted at most {worst:.4f} rad (limit {HOLD_TOLERANCE}) while held")
    finally:
        robot.shutdown()


def go_to(robot, side, target, speed, finished):
    try:
        robot.go_to_joint_position(side, target, speed)
    finally:
        finished.set()


def lapse(args) -> int:
    """A move on a permit, the permit stops being renewed: the arm stops where it is."""
    if args.backend != "hardware":
        print("SKIP: the simulator has no permit")
        return 0

    robot, indices, position = connect(args)
    keeper = PermitKeeper(permit_groups(args.side))
    try:
        target = position().copy()
        target[args.joint - 1] += args.delta
        finished = threading.Event()
        keeper.start()
        threading.Thread(target=go_to, args=(robot, args.side, target, args.speed, finished), daemon=True).start()
        time.sleep(args.after)
        moved = position().copy()
        print(f"permit stops being renewed after {args.after:g} s; joint {args.joint} at {moved[args.joint - 1]:.3f}, target {target[args.joint - 1]:.3f}")
        keeper.lapse()
        time.sleep(keeper.lease + 0.5)               # the permit runs out
        at_lapse = position().copy()
        time.sleep(2.0)
        travelled = float(np.abs(position() - at_lapse).max())
        return verdict(travelled <= LAPSE_TOLERANCE and not finished.is_set(),
                       f"travelled {travelled:.4f} rad in 2 s after the permit ran out (limit {LAPSE_TOLERANCE}); the move was "
                       f"{'not ' if not finished.is_set() else ''}finished")
    finally:
        keeper.stop()
        robot.shutdown()


def move(args) -> int:
    """One joint a small step and back, on a permit."""
    robot, indices, position = connect(args)
    keeper = PermitKeeper(permit_groups(args.side) if args.backend == "hardware" else ())
    try:
        start = position().copy()
        away = start.copy()
        away[args.joint - 1] += args.delta
        keeper.start()
        worst = 0.0
        for name, target in (("out", away), ("back", start)):
            finished = threading.Event()
            threading.Thread(target=go_to, args=(robot, args.side, target, args.speed, finished), daemon=True).start()
            began = time.time()
            while not finished.wait(0.5) and time.time() - began < MOVE_TIMEOUT:
                print(f"  {name}: joint {args.joint} at {position()[args.joint - 1]:.3f} (target {target[args.joint - 1]:.3f})")
            error = float(np.abs(position() - target).max())
            worst = max(worst, error)
            print(f"{name}: ended {error:.4f} rad from the target, {'returned' if finished.is_set() else 'did not return'}")
        return verdict(worst <= MOVE_TOLERANCE, f"ended at most {worst:.4f} rad from a target (limit {MOVE_TOLERANCE})")
    finally:
        keeper.stop()
        robot.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("hold", "lapse", "move"))
    parser.add_argument("--side", choices=("left", "right"), required=True)
    parser.add_argument("--model", required=True, help="the robot's gafro model (description/openarm/gafro/openarm_bimanual.xml)")
    parser.add_argument("--backend", choices=("hardware", "mujoco"), default="hardware")
    parser.add_argument("--hardware-config", default="", help="the robot's copy of hardware.example.yaml")
    parser.add_argument("--seconds", type=float, default=20.0, help="hold: how long to watch")
    parser.add_argument("--joint", type=int, default=3, help="lapse, move: the joint, 1 to 7")
    parser.add_argument("--delta", type=float, default=0.05, help="lapse, move: the step, rad")
    parser.add_argument("--speed", type=float, default=0.1, help="lapse, move: rad/s")
    parser.add_argument("--after", type=float, default=1.0, help="lapse: seconds into the move before the permit stops being renewed")
    args = parser.parse_args()
    if not 1 <= args.joint <= 7:
        parser.error("--joint is 1 to 7")
    return {"hold": hold, "lapse": lapse, "move": move}[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
