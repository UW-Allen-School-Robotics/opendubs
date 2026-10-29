#!/usr/bin/env python3
"""The runner's picture of where the arms are (arm_output.RosArms, from /joint_states), when each arm publishes on its own.

The old stack's one broadcaster put every joint of both arms in every message. The arm services (opendubs-arm@left|right) are one process per arm: each publishes its own seven joints
in one message and its two fingers in another, so a message carries one arm at most. The table was replaced by each message, held one arm at a time, and a live run, which needs both
arms known, was refused for whichever arm the last message was not about: UNKNOWN_STATE (/joint_states: ARM_L), opendubs2, 2026-10-29. The table is now by joint name, with a stamp per joint.
No ROS: the object is built without its constructor and fed messages shaped like the real ones.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output as AO  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def arms():
    a = AO.RosArms.__new__(AO.RosArms)
    a.lock = threading.Lock()
    a._js, a._js_at, a._js_t, a._jv, a._je = {}, 0.0, {}, {}, {}
    return a


def arm_message(side, base=0.1, velocity=True):
    names = AO.joint_names(side)
    return SimpleNamespace(name=names, position=[base * (i + 1) for i in range(7)], velocity=[0.01] * 7 if velocity else [], effort=[])


def fingers_message(side):
    return SimpleNamespace(name=[f"openarm_{side}_finger_joint1", f"openarm_{side}_finger_joint2"], position=[0.044, 0.044], velocity=[], effort=[])


def age(a, names, seconds):
    for n in names:
        a._js_t[n] -= seconds


# 1. what the arm services publish: one arm per message
a = arms()
a._on_js(arm_message("left"))
check("after the left arm's message, only the left is known", sorted(a.measured()) == ["ARM_L"], a.measured())
a._on_js(arm_message("right", base=0.2))
m = a.measured()
check("after the right arm's message, BOTH arms are known (the table was replaced before)", sorted(m) == ["ARM_L", "ARM_R"], m)
check("each keeps its own values", abs(m["ARM_L"][0] - 5.73) < 0.01 and abs(m["ARM_R"][0] - 11.46) < 0.01, m)
a._on_js(fingers_message("left"))
a._on_js(fingers_message("right"))
check("a message that carries only fingers does not drop the arms", sorted(a.measured()) == ["ARM_L", "ARM_R"], a.measured())

# 2. one message with everything (a single broadcaster), as before
b = arms()
both = SimpleNamespace(name=AO.joint_names("left") + AO.joint_names("right"), position=[0.1] * 14, velocity=[], effort=[])
b._on_js(both)
check("one message carrying both arms still works", sorted(b.measured()) == ["ARM_L", "ARM_R"], b.measured())

# 3. sample() and the gripper's finger
check("sample() has the arm's seven angles in degrees", a.sample("left") is not None and len(a.sample("left")[1]) == 7)
check("sample() has velocities while the arm's message carried them", a.sample("left")[2] is not None and a.sample("right")[2] is not None)
check("the finger is read from its own message", abs(a.finger("left") - 0.044) < 1e-9 and abs(a.finger("right") - 0.044) < 1e-9)

# 4. freshness is per joint: one arm going quiet does not hide the other, and never reads as current
age(a, AO.joint_names("left"), AO.JOINT_STATE_MAX_AGE_S + 1.0)
a._on_js(arm_message("right", base=0.2))
check("a stale left arm is left out while the right, still publishing, stays", sorted(a.measured()) == ["ARM_R"], a.measured())
check("sample() of the stale arm is None, of the other is not", a.sample("left") is None and a.sample("right") is not None)
age(a, ["openarm_left_finger_joint1"], AO.JOINT_STATE_MAX_AGE_S + 1.0)
check("a stale finger reads as unknown", a.finger("left") is None)

# 5. velocities: a message that says nothing about them must not leave old ones standing in
c = arms()
c._on_js(arm_message("left", velocity=True))
check("velocities are there when published", c.sample("left")[2] is not None)
c._on_js(arm_message("left", velocity=False))
check("a later message without velocities clears them", c.sample("left")[2] is None, c.sample("left"))

# 6. nothing at all for longer than the age: unknown, as before
d = arms()
d._on_js(arm_message("left"))
d._on_js(arm_message("right"))
d._js_at -= AO.JOINT_STATE_MAX_AGE_S + 1.0
age(d, list(d._js_t), AO.JOINT_STATE_MAX_AGE_S + 1.0)
check("no message for longer than the age: measured() is None", d.measured() is None)
check("and so is every arm's sample()", d.sample("left") is None and d.sample("right") is None)
check("before any message: unknown", arms().measured() is None and arms().sample("left") is None and arms().finger("left") is None)

print(f"{len(FAILS)} failure(s)" if FAILS else "all joint-state merge checks pass")
sys.exit(1 if FAILS else 0)
