#!/usr/bin/env python3
"""First-run checks of one arm on the gafro path, one stage per subcommand, each printing its own verdict.

    PY=<venv>/bin/python
    $PY bringup.py hold  --side right --model <model.xml> --hardware-config <hardware.yaml>
    $PY bringup.py lapse --side right --joint 3 --delta +0.05 --speed 0.03 --after 0.3 ...
    $PY bringup.py move  --side right --joint 3 --delta +0.01 ...

Stage 3 `hold`: the motors are verified, enabled and held where the arm is, with no permit, so nothing is commanded and the arm must not
move. Stage 4 `lapse`: a slow move is under way on a permit; the permit then stops being renewed, as when whatever renews it dies, and the arm
must stop where it is. Stage 5 `move`: one joint a small step and back, on a permit.

A step has no default size or direction (`--delta` is signed and required; at most 5 deg unless `--allow-large`). Once the arm is held at its real pose and
before it moves, `lapse` and `move` check the clearance along the path in the Choreography lane's twin (sim/clearance.py: refuse under 20 mm, a floor
and not a margin, since the twin was 6 deg optimistic on the one pose measured on the robot), print the plan with that pose, and start only when the joint and the
direction are typed back (`3+`). A `lapse` that cannot pass, because the move would be over before the permit runs out, is refused before anything is
enabled. See motion_plan.py.

For the first run of an arm: supported, a person at the e-stop, one arm at a time, and `require_permit: true` in the hardware config. On a real bus every
stage first refuses if the existing arm stack is running or another sender is commanding the motors, and `lapse` and `move` also refuse unless CH5 is
MANUAL or AUTO with the e-stop clear and `--supervised` says a person is at the e-stop (guards.py). They also need the deadman, CH6, HELD: hold it from
the moment you type the joint and direction back until the stage ends. It is checked once before the first permit is granted and before every renewal, so
letting go stops the move within the 0.5 s lease, as flipping CH5 to OFF does. The
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

import guards  # noqa: E402
import motion_plan  # noqa: E402
from engage import PermitKeeper, permit_groups  # noqa: E402

HOLD_TOLERANCE = 0.02      # rad, how far an arm that is only held may drift in the hold stage
LAPSE_TOLERANCE = 0.01     # rad, how far an arm may still travel after its permit stops being renewed
MOVE_TOLERANCE = 0.03      # rad, how far from the target a small move may end
MOVE_TIMEOUT = 40.0        # s


def gate(args, motion: bool) -> int:
    """The guards of guards.py, before the RobotInterface exists (initialize() enables the motors, which is already a command on the bus).

    Every stage needs the bus to itself; a stage that moves an arm (`lapse`, `move`) also needs CH5 to permit it and `--supervised`. A `fake` bus
    and the simulator are not gated. Returns 0 to go on.
    """
    if args.backend != "hardware":
        return 0
    try:
        interfaces, real = guards.buses_of(args.hardware_config, permit_groups(args.side))
        if real:
            guards.require_exclusive(interfaces, args.listen_seconds)
            if motion:
                guards.require_motion_permitted(args.supervised, args.dashboard)
    except guards.Refused as refused:
        print(f"refused: {refused}")
        return 1
    return 0


def connect(args):
    import gafro  # noqa: F401  -- before gafro_robot_interface
    import gafro_robot_interface as ri

    if args.backend == "hardware":
        import openarm_driver  # noqa: F401  -- registers `driver: openarm`; after gafro_robot_interface

    robot = ri.RobotInterface(ri.Configuration(args.model, args.backend, args.hardware_config))
    robot.initialize()
    indices = np.asarray(robot.get_system().get_task_space(args.side).get_joint_indices())
    return robot, indices, lambda: np.asarray(robot.get_state().get_position(), dtype=float)[indices]


def keeper_for(args) -> PermitKeeper:
    """The permit keeper of a hardware stage: on a real bus it renews only while CH5 and the e-stop still allow motion (guards.motion_gate)."""
    if args.backend != "hardware":
        return PermitKeeper(())
    groups = permit_groups(args.side)
    _, real = guards.buses_of(args.hardware_config, groups)
    return PermitKeeper(groups, allowed=guards.motion_gate(args.dashboard) if real else None)


def authorise(args, start, away) -> bool:
    """With the arm held at its real pose and before it moves: the clearance along the path, the plan, and the operator's confirmation. True to go."""
    closest = None
    try:
        if not args.skip_clearance:
            clearance_of = motion_plan.twin_clearance(args.side, args.assets, args.sim_dir or os.environ.get("OPENDUBS_SIM_DIR"))
            closest = motion_plan.require_clearance(motion_plan.line_poses(start, away), clearance_of, args.min_clearance_mm)
        motion_plan.confirm(motion_plan.describe(args.side, args.joint, start, args.delta, args.speed, closest,
                                                 None if args.skip_clearance else args.min_clearance_mm),
                            args.joint, args.delta, args.confirm)
    except guards.Refused as refused:
        print(f"refused: {refused}")
        print("the arm is at (deg): " + " ".join(f"{np.degrees(v):+.2f}" for v in start))
        return False
    return True


def deadman_held(args) -> bool:
    """On a real bus the deadman (CH6) must be held when the arm is first told to move: asked once the move is confirmed, before the first permit is granted, so that
    a released deadman is a refusal and not a move that quietly never starts. The keeper asks again before every renewal (guards.motion_gate)."""
    if args.backend != "hardware":
        return True
    _, real = guards.buses_of(args.hardware_config, permit_groups(args.side))
    if not real:
        return True
    try:
        guards.require_deadman(args.dashboard)
    except guards.Refused as refused:
        print(f"refused: {refused}")
        return False
    return True


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
    keeper = keeper_for(args)
    try:
        start = position().copy()
        target = start.copy()
        target[args.joint - 1] += args.delta
        if not authorise(args, start, target):
            return 1
        if not deadman_held(args):
            return 1
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
    keeper = keeper_for(args)
    try:
        start = position().copy()
        away = start.copy()
        away[args.joint - 1] += args.delta
        if not authorise(args, start, away):
            return 1
        if not deadman_held(args):
            return 1
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
    parser.add_argument("--delta", type=float, default=None, help="lapse, move: the step, rad, with a sign (+0.01 or -0.01). Required: there is no default "
                        "direction, because the right one depends on what the arm stands next to")
    parser.add_argument("--speed", type=float, default=None, help="lapse, move: rad/s (default: move 0.1, lapse 0.03, slow enough for the move to outlast the permit)")
    parser.add_argument("--after", type=float, default=None, help="lapse: seconds into the move before the permit stops being renewed (default 0.3)")
    parser.add_argument("--max-step-deg", type=float, default=motion_plan.MAX_STEP_DEG, help="the largest step taken without --allow-large")
    parser.add_argument("--allow-large", action="store_true", help="take a step larger than --max-step-deg")
    parser.add_argument("--min-clearance-mm", type=float, default=motion_plan.DEFAULT_MIN_CLEARANCE_MM,
                        help="refuse if the path comes nearer than this to anything in the lane's twin")
    parser.add_argument("--skip-clearance", action="store_true", help="do not check the path in the twin (it says so in the plan)")
    parser.add_argument("--assets", default=None, help="the openarm_assets checkout the twin is built from (or $OPENARM_ASSETS)")
    parser.add_argument("--sim-dir", default=None, help="the sim/ directory of an opendubs checkout whose twin builds (or $OPENDUBS_SIM_DIR); "
                        "default: this checkout's own")
    parser.add_argument("--confirm", default=None, help="the joint and direction to start with (3+ or 3-), for a script that has read the plan; "
                        "without it the operator is asked")
    parser.add_argument("--supervised", action="store_true", help="lapse, move: a person is at the e-stop (required on a real bus)")
    parser.add_argument("--dashboard", default=guards.DASHBOARD, help="where CH5 and the e-stop are read (GET /api/choreo/robot)")
    parser.add_argument("--listen-seconds", type=float, default=guards.LISTEN_SECONDS, help="how long to listen for another sender")
    args = parser.parse_args()
    if not 1 <= args.joint <= 7:
        parser.error("--joint is 1 to 7")
    if args.stage in ("lapse", "move"):
        if args.speed is None:
            args.speed = 0.03 if args.stage == "lapse" else 0.1
        if args.after is None:
            args.after = 0.3
        if args.speed <= 0.0:
            parser.error("--speed must be positive")
        try:
            motion_plan.validate_step(args.joint, args.delta if args.delta is not None else 0.0, args.max_step_deg, args.allow_large)
            if args.stage == "lapse":
                motion_plan.require_lapse_can_pass(args.delta, args.speed, args.after)
        except guards.Refused as refused:
            print(f"refused: {refused}")
            return 1
    refused = gate(args, motion=args.stage in ("lapse", "move"))
    if refused:
        return refused
    try:
        return {"hold": hold, "lapse": lapse, "move": move}[args.stage](args)
    except guards.Refused as refused:       # e.g. CH5 went to OFF between the gate and the first permit
        print(f"refused: {refused}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
