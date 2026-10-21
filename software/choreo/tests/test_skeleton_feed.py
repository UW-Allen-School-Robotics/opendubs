#!/usr/bin/env python3
"""The live skeleton feed's adapter (skeleton_feed.py): the feed's joint names become the canonical set,
a missing neck or pelvis is made from its neighbours, the capture time lands on the Thor's monotonic
clock, and it imports nothing beyond the standard library (the relay runs on the host's ROS Python)."""
from __future__ import annotations

import ast
import os
import sys
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import skeleton_feed as SF  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


print("skeleton feed: the adapter")
c = SF.canonical({"left_shoulder": [-0.2, 1.4, 2.0, 0.9], "right_shoulder": [0.2, 1.4, 2.0, 0.8],
                  "LEFT_HIP": [-0.1, 0.9, 2.0], "right_hip": [0.1, 0.9, 2.0], "left_eye": [0, 1.6, 2]})
check("the feed's names map to the canonical set; unknown joints are dropped",
      set(c) == {"l_shoulder", "r_shoulder", "l_hip", "r_hip", "neck", "pelvis"}, sorted(c))
check("... a missing neck and pelvis are made from the shoulders and hips (the lower confidence kept)",
      c["neck"][:3] == [0.0, 1.4, 2.0] and c["neck"][3] == 0.8 and c["pelvis"][:3] == [0.0, 0.9, 2.0], c)
msg = NS(header=NS(stamp=NS(sec=1000, nanosec=500_000_000)),
         people=[NS(id=7, joints=[NS(name="head", position=NS(x=0, y=1.6, z=2.1), confidence=0.95)])])
f = SF.adapt(msg, received_monotonic=50.0)
check("a message becomes a canonical v1 frame, its people by id, joints [x, y, z, c]",
      f["v"] == 1 and f["status"] == "live" and f["people"][0]["id"] == 7
      and f["people"][0]["joints"]["head"] == [0.0, 1.6, 2.1, 0.95], f)
f2 = SF.adapt(NS(header=NS(stamp=NS(sec=1001, nanosec=0)), people=[]), received_monotonic=50.6)
check("... its capture time is on the Thor's monotonic clock (the ROS stamp, through the offset learnt at receipt)",
      abs(f["t"] - 50.0) < 1e-3 and abs(f2["t"] - 50.5) < 1e-3, (f["t"], f2["t"]))
check("nobody there: people is an empty list", f2["people"] == [])
check("the message type is not fixed yet (None: the relay idles)", SF.MSG_TYPE is None)
check("the synthetic person steps in and out (for tests and the twin; no camera, no person)",
      SF.synthetic(5.0)["people"] and not SF.synthetic(15.0)["people"])
tree = ast.parse(open(SF.__file__).read())
mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) and n.col_offset == 0} | \
       {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and n.col_offset == 0}
check("it imports only the standard library at the top (the relay's host Python)",
      mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names))

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
