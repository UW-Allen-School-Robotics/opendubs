#!/usr/bin/env python3
"""The arm goals' answers on a live run (found at the robot 2026-10-12: the controllers REJECTED both
arms' goals and the show ran on, "live", with still arms). Every answer is logged; a rejected, an
unanswered or a controller-aborted goal ends the show, saying which and why."""
from __future__ import annotations

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output as AO  # noqa: E402
import runner as R  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
SHOW = os.path.join(HERE, "..", "shows", "celebrate.yaml")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


class SimClock:
    def __init__(self):
        self.now = 1000.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


def run(arms):
    sc, log = SimClock(), io.StringIO()
    end = R.Runner(SHOW, LIMITS, R.FakeRobotState(), log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5,
                   arms=arms, live=True).run()
    recs = [json.loads(x) for x in log.getvalue().splitlines()]
    return end, [r for r in recs if r["kind"] == "arm_goal"]


print("arm goals: accepted")
end, goals = run(AO.FakeArms())
check("a show whose goals are accepted runs to its end", end[0] == "COMPLETED", end)
check("every goal sent is logged, and every answer", any(g["event"] == "sent" for g in goals)
      and sum(g["event"] == "accepted" for g in goals) == sum(g["event"] == "sent" for g in goals), goals)

print("\narm goals: rejected")
a = AO.FakeArms()
a.reject = {"ARM_R"}
end, goals = run(a)
check("a REJECTED arm goal ends the show at once, naming the arm and pointing at the controller's log",
      end[0] == "ABORTED" and end[1] == "ARM_GOAL_REJECTED" and "right arm" in end[2] and "log" in end[2], end)
check("... and the rejection is in the log", any(g["event"] == "rejected" and g["slot"] == "ARM_R" for g in goals), goals)

print("\narm goals: never answered")
a = AO.FakeArms()
a.silent = {"ARM_L"}
end, goals = run(a)
check(f"a goal not accepted within {R.ARM_ACK_S:g} s ends the show (the arms are not following it)",
      end[0] == "ABORTED" and end[1] == "ARM_GOAL_UNANSWERED" and "left arm" in end[2], end)

print("\narm goals: aborted by the controller mid-way")


class AbortingArms(AO.FakeArms):
    def send(self, slot, points):
        gid = super().send(slot, points)
        if slot == "ARM_L" and points and points[-1][0] > 1.0:   # the routine, not a short start-pose move
            self._events.append({"goal": gid, "slot": slot, "event": "result", "status": 6,
                                 "error_code": -4, "error": "path tolerance violated"})
        return gid


end, goals = run(AbortingArms())
check("the arm's CURRENT goal aborted by its controller ends the show, with the controller's reason",
      end[0] == "ABORTED" and end[1] == "ARM_GOAL_ABORTED" and "path tolerance" in end[2], end)

print("\nwaypoints into the controller's own joint range")
# The controllers' admitted ranges (Arms, 2026-10-12: the URDF's limits widened 0.005 rad), signed degrees
COMMON = [(-90.29, 90.29), (-0.29, 140.29), (-90.29, 90.29), (-45.29, 45.29), (-90.29, 90.29)]
RANGES = {"ARM_L": [(-200.29, 80.29), (-190.29, 10.29)] + COMMON,
          "ARM_R": [(-80.29, 200.29), (-10.29, 190.29)] + COMMON}


def run_from(right_j4):
    # J1 30 deg away too: a start-pose move long enough that its first waypoints still sit near J4's start
    a = AO.FakeArms(measured={"ARM_L": [0.0] * 7, "ARM_R": [30.0, 0.0, 0.0, right_j4, 0.0, 0.0, 0.0]})
    a.ranges = RANGES
    sc, log = SimClock(), io.StringIO()
    end = R.Runner(SHOW, LIMITS, R.FakeRobotState(), log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5,
                   arms=a, live=True).run()
    return end, a, log.getvalue()


end, a, log = run_from(-0.4)        # an arm that hung limp: J4 rests a hair past its limit
first_r = next(p for s, p in a.sent if s == "ARM_R")
check("an arm resting 0.11 deg past its controller's J4 range: the start-pose waypoints are clamped in, "
      "and the show runs", end[0] == "COMPLETED" and min(pt[1][3] for pt in first_r) >= -0.29 - 1e-9, end)
check("... and the clamp is said, with how far", "clamped into the controller's range" in log and '"4": 0.109' in log)
end, a, log = run_from(-2.0)
check("2 deg outside: refused before anything is sent, naming the joint and the range",
      end[0] == "REFUSED" and end[1] == "ARM_OUT_OF_RANGE" and "J4" in end[2] and not any(s == "ARM_R" for s, _ in a.sent), end)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
