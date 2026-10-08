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
check("a path followed 60 ms late reads as a 60 ms lag with almost no error, steady and at the reversals",
      abs(res["lag_ms"] - 60) <= 5 and res["steady_error_deg"] < 0.5 and res["reversal_error_deg"] < 0.5, res)
check("... its effort is a share of the elbow's limit, min(20 N·m trip, 27 rated) = 20",
      res["steady_effort_share"] == 0.25, res)
check("a run that passes has no verdict and no reversal finding", S.verdict(res, 4) == (None, None))
check("a goal the controller aborted fails, as a trip", "trip" in S.verdict(res, 6)[0])
check("no goal outcome is INCONCLUSIVE, never a pass", S.verdict(res, None)[0].startswith("INCONCLUSIVE"))
check("steady effort over 70% fails", "effort" in (S.verdict(dict(res, steady_effort_share=0.75), 4)[0] or ""))
check("steady tracking error over 5 deg fails", "tracking" in (S.verdict(dict(res, steady_error_deg=6.0), 4)[0] or ""))
check("lag over 100 ms fails (a fifth of a beat)", "lag" in (S.verdict(dict(res, lag_ms=120), 4)[0] or ""))
f, rev = S.verdict(dict(res, reversal_effort_share=0.9), 4)
check("a REVERSAL over the limit is reported as a finding, and does not fail the speed", f is None and "reversal" in rev,
      (f, rev))
check("no effort reported is not a failure", S.verdict(dict(res, steady_effort_share=None), 4) == (None, None))
check("the wrist's effort is measured against its 7 N·m motor rating, not its 15 N·m trip",
      S.EFFORT_LIMIT_NM[7] == 7 and S.EFFORT_LIMIT_NM[4] == 20)

pts, knots = S.swing("right", 7, 240)
fast = [(t, [0, 30, 0, 60, 0, 0, S.ref_at(knots, t - 0.03)], None, None)
        for t in [i * 0.004 for i in range(int(pts[-1][0] / 0.004))]]
r = S.analyse(fast, knots, 7)
check("at 240 deg/s the lag search stays under half a leg, so it cannot line up with the reversed swing",
      r["lag_searched_ms"] <= 80 and abs(r["lag_ms"] - 30) <= 5, r)


class LiveArms:
    """Follows the path exactly, but its effort climbs past the limit 0.3 s into the swing."""

    def __init__(self, knots, joint):
        self.knots, self.j, self.results, self.cancelled = knots, joint - 1, {}, 0

    def send(self, slot, pts):
        import time
        self.t0 = time.monotonic()

    def sample(self, side):
        import time
        now = time.monotonic()
        t = now - self.t0
        pos = [0, 30, 0, 60, 0, 0, 0]
        pos[self.j] = S.ref_at(self.knots, t)
        eff = [0.0] * 7
        eff[self.j] = 2.0 if t < 0.3 else 6.0          # 6 of the wrist's 7: over 70%
        return now, pos, None, eff

    def cancel_all(self):
        self.cancelled += 1


class OpenGate:
    def why_not(self, probe=False):
        return None


run = next(x for x in S.plan("right", (7,), (60,), LIMITS)[0])
arms = LiveArms(run["knots"], 7)
res, _ = S.run_one(arms, OpenGate(), "right", "ARM_R", run, None)
check("a swing heading for the limit is CANCELLED MID-SWING, before a trip, not run to its end",
      arms.cancelled == 1 and "effort" in res.get("stopped_live", "") and res["at_s"] < run["duration_s"], res)
check("... and that fails the speed", "stopped mid-swing" in S.verdict(res, res.get("status"))[0])

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
