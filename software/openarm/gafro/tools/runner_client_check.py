#!/usr/bin/env python3
"""The show runner's OWN arm client (choreo/arm_output.RosArms) against the arm service: what the runner would do, minus the dashboard's preflight.

    python runner_client_check.py                       # joint 7 of the right arm, 6 degrees out and back in 6 s, then a runner-style cancel

It builds the goals the way the runner does (a dense path, one point per 20 ms from 0.02 s, joint names openarm_right_joint1..7, degrees in, RosArms.send), reads the arm
from /joint_states with RosArms.measured, and reads the result from RosArms.goal_status (4 reached, 5 cancelled, 6 aborted). Then a longer goal is cancelled with
RosArms.cancel_all, as the runner does when a show ends early: the arm must stop and hold, and the goal must end 5.

Needs the arm service running and SUPERVISED (`ctl supervise`), CH6 held for the whole of each goal, ROS sourced (/opt/gafro/setup.bash), a person at the e-stop.
Moves the right arm's joint 7 only, a few degrees. The runner's dashboard preflight is not involved: it needs the dashboard's `effectors` to know the arms are active.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "choreo")))

import arm_output  # noqa: E402

NAMES = {4: "SUCCEEDED", 5: "CANCELED", 6: "ABORTED"}


def wrist_path(start, joint, delta_deg, seconds):
    """The runner's dense format: [(seconds from now, [7 signed degrees])], one point per DENSE_S from DENSE_S, joint `joint` going out and back along a raised cosine."""
    points, k = [], 1
    while k * arm_output.DENSE_S <= seconds + 1e-9:
        t = k * arm_output.DENSE_S
        pose = list(start)
        pose[joint - 1] = start[joint - 1] + delta_deg * 0.5 * (1.0 - math.cos(2.0 * math.pi * t / seconds))
        points.append((round(t, 3), [round(v, 4) for v in pose]))
        k += 1
    return points


def wait_result(arms, gid, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if gid in arms.goal_status:
            return arms.goal_status[gid]
        time.sleep(0.05)
    return None


def run(arms, side="right", joint=7, delta_deg=6.0, seconds=6.0, cancel_after=3.0, out=print) -> bool:
    slot = {"left": "ARM_L", "right": "ARM_R"}[side]
    measured = None
    for _ in range(100):
        measured = arms.measured()
        if measured and slot in measured:
            break
        time.sleep(0.05)
    if not measured or slot not in measured:
        out(f"FAIL: /joint_states has no {side} arm (the runner would call it missing): {measured}")
        return False
    start = measured[slot]
    out(f"measured {side} arm (deg): " + " ".join(f"{v:+.1f}" for v in start))
    ranges = arms.controller_ranges()
    out(f"controller_ranges: {'none read (the old stack publishes them; the runner then sends unclamped and says so)' if ranges is None else 'read'}")

    ok = True
    gid = arms.send(slot, wrist_path(start, joint, delta_deg, seconds))
    out(f"goal {gid}: joint {joint} {delta_deg:+.0f} deg out and back in {seconds:g} s: hold CH6")
    status = wait_result(arms, gid, seconds + 10.0)
    for event in arms.drain_events():
        out(f"  event: {event}")
    out(f"  result: {NAMES.get(status, status)}")
    if status != 4:
        out("FAIL: the goal did not end SUCCEEDED (4)")
        ok = False

    measured = arms.measured() or {}
    start = measured.get(slot, start)
    gid = arms.send(slot, wrist_path(start, joint, 4.0 * delta_deg, 4.0 * seconds))
    out(f"goal {gid}: a longer one, cancelled after {cancel_after:g} s with cancel_all (what the runner does when a show ends early)")
    time.sleep(cancel_after)
    arms.cancel_all()
    status = wait_result(arms, gid, 10.0)
    for event in arms.drain_events():
        out(f"  event: {event}")
    out(f"  result: {NAMES.get(status, status)}")
    if status != 5:
        out("FAIL: the cancelled goal did not end CANCELED (5)")
        ok = False
    time.sleep(0.5)
    here = (arms.measured() or {}).get(slot)
    if here:
        moved = max(abs(a - b) for a, b in zip(here, start))
        out(f"  the arm is {moved:.1f} deg from where it started and should be holding there")
    out("PASS" if ok else "FAIL")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--side", choices=("left", "right"), default="right")
    parser.add_argument("--joint", type=int, default=7)
    parser.add_argument("--delta-deg", type=float, default=6.0)
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--cancel-after", type=float, default=3.0)
    args = parser.parse_args()
    arms = arm_output.RosArms("runner_client_check")
    return 0 if run(arms, args.side, args.joint, args.delta_deg, args.seconds, args.cancel_after) else 1


if __name__ == "__main__":
    sys.exit(main())
