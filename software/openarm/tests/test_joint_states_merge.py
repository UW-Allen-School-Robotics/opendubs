"""joint_states_merge.Merger (software/openarm/gafro/joint_states_merge.py): the whole-robot /joint_states from per-arm messages, without ROS.
The cases are the ones the runner hit: two arms that publish apart must read as both known, a dead arm must read as missing, and a message must carry
velocity/effort for all of its joints or for none. Run: python software/openarm/tests/test_joint_states_merge.py"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gafro"))
from joint_states_merge import Merger  # noqa: E402

L = [f"openarm_left_joint{i}" for i in range(1, 8)]
R = [f"openarm_right_joint{i}" for i in range(1, 8)]
failures = []


def check(name, condition):
    print(("  ok    " if condition else "  FAIL  ") + name)
    if not condition:
        failures.append(name)


m = Merger(max_age=1.0)
m.update(0.0, L, [0.1] * 7, [0.0] * 7, [1.0] * 7)
names, pos, vel, eff = m.snapshot(0.1)
check("one arm heard: only its joints", names == sorted(L) and len(vel) == 7)
m.update(0.05, R, [0.2] * 7, [0.0] * 7, [2.0] * 7)
names, pos, vel, eff = m.snapshot(0.1)
check("both arms, published apart: all 14 joints in one snapshot", set(names) == set(L + R) and len(pos) == 14 and len(vel) == 14 and len(eff) == 14)
check("each joint has its own arm's value", pos[names.index(L[0])] == 0.1 and pos[names.index(R[0])] == 0.2)
m.update(0.1, ["openarm_left_finger_joint1", "openarm_left_finger_joint2"], [0.04, 0.04])
names, pos, vel, eff = m.snapshot(0.12)
check("fingers (no velocity) join: arm velocities are kept, the fingers' are NaN, lengths match names", len(vel) == len(names) == 16 and math.isnan(vel[names.index("openarm_left_finger_joint1")]) and vel[names.index(L[0])] == 0.0)
m.update(1.0, L, [0.3] * 7, [0.0] * 7, [1.0] * 7)
names, *_ = m.snapshot(1.5)
check("the right arm stops publishing: after max_age its joints are gone, the left's stay", set(R).isdisjoint(names) and set(L) <= set(names))
check("nothing heard within max_age: no snapshot", m.snapshot(10.0) is None)
m2 = Merger()
m2.update(0.0, ["a"], [1.0])
check("no velocity or effort anywhere: both lists empty (a message carries all or none)", m2.snapshot(0.1) == (["a"], [1.0], [], []))
print(f"{len(failures)} failed" if failures else "all passed")
sys.exit(1 if failures else 0)
