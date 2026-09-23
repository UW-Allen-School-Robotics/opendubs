#!/usr/bin/env python3
"""Checks on the checks. Runs anywhere: no ROS, no robot.

    python3 tests/test_cue.py

Each case asserts that a specific BAD show is caught, because a validator that only ever says
"ok" is worse than none — it buys confidence it has not earned. The limits fixture is the arm
software's own joint_limits.yaml, copied for testing only; the robot reads the real one.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cue import Show, load_limits, load_show  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIMITS = load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))

fails = []


def check(name, cues, expect, slots=None):
    """expect: a substring of the problem we require, or None meaning "must pass"."""
    show = Show({"id": "t", "slots": slots if slots is not None else
                 sorted({c["slot"] for c in cues}), "cues": cues}, "test")
    problems = show.validate(LIMITS)
    if expect is None:
        ok = not problems
        detail = "; ".join(problems)
    else:
        ok = any(expect in p for p in problems)
        detail = "; ".join(problems) or "(no problems reported)"
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(f"{name}: expected {expect!r}, got: {detail}")


print("cue sheet validation")

check("plain pose passes",
      [{"t": 3.0, "slot": "ARM_R", "joints": {4: 55}}], None)

check("joint past its limit is caught",
      [{"t": 10.0, "slot": "ARM_R", "joints": {4: 200}}], "outside usable")

check("elbow moving too fast is caught",
      [{"t": 1.0, "slot": "ARM_R", "joints": {4: 120}}], "deg/s")

check("same speed over enough time passes",
      [{"t": 6.0, "slot": "ARM_R", "joints": {4: 120}}], None)

check("shoulder near the head is caught even though the arm controller allows it",
      [{"t": 5.0, "slot": "ARM_R", "joints": {4: 100}},
       {"t": 12.0, "slot": "ARM_R", "joints": {2: 170}}], "HEAD")

check("shoulder high with an EXTENDED arm is caught",
      [{"t": 8.0, "slot": "ARM_R", "joints": {2: 150}}], "fold the elbow")

check("shoulder high with a folded elbow passes",
      [{"t": 4.0, "slot": "ARM_R", "joints": {4: 100}},
       {"t": 10.0, "slot": "ARM_R", "joints": {2: 150}}], None)

check("the mirror is applied: the same show is legal for the left arm",
      [{"t": 4.0, "slot": "ARM_L", "joints": {4: 100}},
       {"t": 10.0, "slot": "ARM_L", "joints": {2: 150}}], None)

check("gripper beyond its side's range is caught",
      [{"t": 1.0, "slot": "GRIP_R", "pos": -0.030}], "outside")

check("the OTHER side's range is genuinely different",
      [{"t": 1.0, "slot": "GRIP_L", "pos": -0.030}], None)

check("two cues owning one slot at once is caught",
      [{"t": 1.0, "slot": "HEAD", "pan": 60, "hold_s": 4.0},
       {"t": 2.0, "slot": "HEAD", "pan": 120}], "still busy")

check("a beep the speaker cannot carry is caught",
      [{"t": 1.0, "slot": "SOUND", "do": "beep", "hz": 700}], "does not carry")

check("a beep in the audible band passes",
      [{"t": 1.0, "slot": "SOUND", "do": "beep", "hz": 3000}], None)

check("a show may not acknowledge the pinch guard",
      [{"t": 1.0, "slot": "LIFT", "do": "down", "secs": 4, "ack_pinch": True}],
      "human's decision")

check("an over-long lift command is caught",
      [{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 30}], "outside")

check("the base is refused outright",
      [{"t": 1.0, "slot": "DRIVE", "do": "strafe"}], "unknown slot",
      slots=["DRIVE"])

check("a show may be WRITTEN grouped by limb, out of time order",
      [{"t": 5.0, "slot": "HEAD", "pan": 60},
       {"t": 2.0, "slot": "HEAD", "pan": 120}], None)

check("... and the speed check still uses real elapsed time when it does",
      [{"t": 9.0, "slot": "ARM_R", "joints": {4: 120}},
       {"t": 1.0, "slot": "ARM_R", "joints": {4: 10}}], None)

check("using an undeclared slot is caught",
      [{"t": 1.0, "slot": "SOUND", "do": "chime"}], "did not declare", slots=["HEAD"])

print("\nthe real show")
show = load_show(os.path.join(HERE, "..", "shows", "hello.yaml"))
problems = show.validate(LIMITS)
print(f"  {'ok  ' if not problems else 'FAIL'}  shows/hello.yaml validates")
for p in problems:
    print("        ", p)
if problems:
    fails.append("shows/hello.yaml does not validate")

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
