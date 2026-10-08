#!/usr/bin/env python3
"""The arm speed test's planning and analysis (plan 6.1), off the robot: nothing here sends anything."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402
import speed_test as S  # noqa: E402

LIMITS = cue.load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


print("speed test: the plan")
runs, problems = S.plan("right", S.JOINT_ORDER, S.SPEEDS, LIMITS)
check("every joint's swing fits the right arm's limits", not problems, problems)
check("no speed at or over 80% of a velocity trip is ever planned",
      all(r["speed"] < S.TRIP_SHARE_MAX * cue.TRIP_DEG_S[r["joint"]] for r in runs))
check("... so the elbow and J3 stop below their 312 deg/s trip, and the rest reach 240",
      max(r["speed"] for r in runs if r["joint"] == 4) == 240 and max(r["speed"] for r in runs if r["joint"] == 7) == 240)
lruns, lproblems = S.plan("left", S.JOINT_ORDER, S.SPEEDS, LIMITS)
check("... and the left arm's (mirrored) swings fit its limits too", not lproblems, lproblems)

for arm in ("right", "left"):
    pts, knots = S.swing(arm, 4, 120)
    base = S.signed(arm, S.BASE)
    vmax = max(abs(b[1][3] - a[1][3]) / (b[0] - a[0]) for a, b in zip(pts, pts[1:]))
    others = all(p[1][k] == base[k] for p in pts for k in range(7) if k != 3)
    check(f"{arm}: the swing moves only the tested joint, at the asked speed and no faster",
          others and 118 <= vmax <= 121, (vmax, others))
    check(f"{arm}: ... within +-20 deg of the base, ending at the base",
          max(abs(p[1][3] - base[3]) for p in pts) <= S.AMPLITUDE_DEG + 1e-6 and pts[-1][1] == base, pts[-1])

print("\nspeed test: the analysis")
pts, knots = S.swing("right", 4, 120)
lag = 0.06
samples = [(t, [0, 30, 0, S.ref_at(knots, t - lag), 0, 0, 0], None, [0, 0, 0, 5.0, 0, 0, 0])
           for t in [i * 0.004 for i in range(int(pts[-1][0] / 0.004))]]
res = S.analyse(samples, knots, 4)
check("a path followed 60 ms late reads as a 60 ms lag with almost no error",
      abs(res["lag_ms"] - 60) <= 5 and res["max_error_deg"] < 0.5, res)
check("... its effort is reported as a share of the elbow's 20 N·m trip", res["effort_share"] == 0.25, res)
check("a run that passes has no verdict", S.verdict(res, 4) is None)
check("a goal the controller aborted fails, as a trip", "trip" in S.verdict(res, 6))
check("effort over 70% of the trip fails", "effort" in (S.verdict(dict(res, effort_share=0.75), 4) or ""))
check("tracking error over 5 deg fails", "tracking" in (S.verdict(dict(res, max_error_deg=6.0), 4) or ""))
check("no effort reported (the arm stack may not publish it) is not a failure",
      S.verdict(dict(res, effort_share=None), 4) is None)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
