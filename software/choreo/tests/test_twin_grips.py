#!/usr/bin/env python3
"""The twin's grippers (plan item 3): a GRIP cue's target, reached at a finite speed."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import sim_preview as P  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


ev = [{"t": 1.0, "cue": {"do": "open"}}, {"t": 3.0, "cue": {"open": 0.4}}, {"t": 4.0, "cue": {"do": "close"}}]
S = P.GRIP_STROKE_S
print("twin: grippers")
check("closed until the first cue", P.grip_open(ev, 0.9) == 0.0)
check("opening travels at one stroke per GRIP_STROKE_S, not in an instant",
      abs(P.grip_open(ev, 1.0 + S / 2) - 0.5) < 1e-6 and P.grip_open(ev, 1.0 + S) == 1.0, P.grip_open(ev, 1.0 + S / 2))
check("`open: 0.4` goes to 40%", P.grip_open(ev, 3.0 + S) == 0.4, P.grip_open(ev, 3.0 + S))
check("a close mid-way starts from where the fingers are", 0.0 <= P.grip_open(ev, 4.1) < 0.4 and P.grip_open(ev, 5.0) == 0.0)
quick = [{"t": 1.0, "cue": {"do": "open"}}, {"t": 1.1, "cue": {"do": "close"}}]
check("a cue that arrives before the last move finished turns back from part-way",
      P.grip_open(quick, 1.1) == round(0.1 / S, 4) and P.grip_open(quick, 1.1 + 0.1 / S * S) == 0.0)
smp = P.sample({"tracks": {"GRIP_R": ev}}, 2.0)
check("the twin's sample carries both grippers", smp["grip_right"] == 1.0 and smp["grip_left"] == 0.0, smp)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
