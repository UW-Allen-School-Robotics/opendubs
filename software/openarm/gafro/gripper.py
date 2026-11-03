"""One arm's gripper as a position command in metres of finger travel: what the old stack's `gripper_cmd` (GripperCommand) did, without ROS. The core of arm_service.py's slice 4.

The contract the show runner knows (software/choreo/arm_output.py): the goal is the finger joint's position in METRES (0 .. the joint's range, 0.044 m in the model) and a
max_effort; a close that stops on something before it gets there ABORTS, as stalled, and that abort is the runner's "something is in there" signal. RI's gripper is a closure,
0 open to 1 shut (`set_gripper`), read back as `opening` on the same scale with `grasped` and `moving`; the driver needs `use_gripper` and the two calibration angles of the
motor in the hardware config, and applies nothing while a permit is required and missing.

ASSUMED, to be measured on the robot with the runner's own calibration (software/choreo/grippers.py): that the finger joint is 0 shut and `travel` open, as in the model. The
effort is not enforced (the driver has `gripper_torque`; the old stack did not enforce it either).
"""
from __future__ import annotations

import dataclasses
import threading
import time

import numpy as np

import guards

TRAVEL = 0.044            # m, the model's finger joint range
TOLERANCE = 0.002         # m from the goal that counts as reached
STALL_SECONDS = 1.0       # still, short of the goal, for this long: stalled (the old stack's "~1 s")
START_GRACE = 0.3         # s after the command before "not moving" means anything
TIMEOUT = 6.0             # s


@dataclasses.dataclass
class Result:
    position: float = 0.0       # m, where the finger is
    effort: float = 0.0
    stalled: bool = False
    reached: bool = False
    message: str = ""
    cancelled: bool = False


def closure_of(metres: float, travel: float = TRAVEL) -> float:
    """RI's closure for a finger position: 0 m (shut) is closure 1, `travel` (open) is 0."""
    return float(np.clip(1.0 - float(metres) / travel, 0.0, 1.0))


def metres_of(closure: float, travel: float = TRAVEL) -> float:
    return float((1.0 - np.clip(closure, 0.0, 1.0)) * travel)


def name_of(robot, side: str) -> str:
    """The robot's gripper for `side`: the one gripper name that mentions it."""
    found = [n for n in robot.get_gripper_names() if side in n]
    if len(found) != 1:
        raise guards.Refused(f"the {side} gripper is not available (the robot's grippers: {list(robot.get_gripper_names())}); is `use_gripper` set and calibrated?")
    return found[0]


def position_of(reading, travel: float = TRAVEL) -> float:
    return metres_of(reading["opening"], travel)


def grip(robot, side: str, metres: float, lease, cancel: threading.Event, travel: float = TRAVEL, clock=time.monotonic, sleep=time.sleep) -> Result:
    """Move the finger to `metres` and say how it ended. `lease` is this command's share of the permit (started here, so the gate answers first, and released on every way out)."""
    try:
        name = name_of(robot, side)
        if not 0.0 <= metres <= travel + 1e-9:
            return Result(message=f"{metres:g} m is outside the finger's 0 to {travel:g} m")
        lease.start()
    except guards.Refused as refused:
        return Result(message=str(refused))
    try:
        sub = robot.subscribe_gripper(name)
        last = None
        for _ in range(50):                      # where it is now, before anything is sent
            last = sub.read() or last
            if last:
                break
            sleep(0.01)
        if last is None:
            return Result(message="the gripper reports no state")
        goal = closure_of(metres, travel)
        closing = goal > last["opening"]
        robot.set_gripper(name, goal)
        sent, still_since = clock(), None
        while clock() - sent < TIMEOUT:
            last = sub.read() or last
            here = position_of(last, travel)
            if cancel.is_set():
                robot.set_gripper(name, last["opening"])       # hold where it is
                return Result(here, message="cancelled; the gripper holds where it is", cancelled=True)
            if abs(here - metres) <= TOLERANCE and not last["moving"]:
                return Result(here, reached=True, message="reached")
            if clock() - sent > START_GRACE:
                if last["grasped"] or not last["moving"]:
                    still_since = still_since or clock()
                    if last["grasped"] or clock() - still_since >= STALL_SECONDS:
                        return Result(here, stalled=True, message=("closed on something" if closing else "stopped short") + f" at {here:.4f} m")
                else:
                    still_since = None
            sleep(0.02)
        return Result(position_of(last, travel), message="timed out")
    finally:
        lease.stop()
