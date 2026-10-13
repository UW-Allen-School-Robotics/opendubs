#!/usr/bin/env python3
"""The lift and the state light on live runs (show_outputs.py), against a fake dashboard and the
runner's simulated clock: renewal, stop on every end, the operator winning, light merging and release."""
from __future__ import annotations

import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output as AO  # noqa: E402
import runner as R  # noqa: E402
import show_outputs as SO  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


class FakeDash:
    def __init__(self, clock, fail=False):
        self.clock, self.fail, self.calls = clock, fail, []

    def mast(self, cmd):
        if self.fail:
            raise OSError("no dashboard")
        self.calls.append((round(self.clock(), 3), "mast", cmd))
        return {"ok": True}

    def light(self, body):
        if self.fail:
            raise OSError("no dashboard")
        self.calls.append((round(self.clock(), 3), "light", body))
        return {"ok": True}


class SimClock:
    def __init__(self, robot=None, at=None, change=None):
        self.now, self.robot, self.at, self.change, self.fired = 1000.0, robot, at, change, False

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.at is not None and not self.fired and self.now >= self.at:
            self.fired = True
            self.robot.set(**self.change)


SHOW = """id: lt
slots: [LIFT, LIGHT]
requires: { rc_authority: AUTO }
cues:
  - { t: 0.5, slot: LIFT, do: up, secs: 1.0 }
  - { t: 0.5, slot: LIGHT, do: color, rgb: [0, 200, 80], br: 80 }
  - { t: 0.6, slot: LIGHT, do: color, rgb: [0, 90, 255], br: 80 }
  - { t: 2.0, slot: LIGHT, do: release }
  - { t: 2.5, slot: LIFT, do: down, secs: 1.0 }
"""
tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
tmp.write(SHOW)
tmp.close()


def run(live=True, flip_after=None, change=None, fail=False, lift=True, light=True):
    robot = R.FakeRobotState()
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    dash = FakeDash(sc.clock, fail)
    log = io.StringIO()
    r = R.Runner(tmp.name, LIMITS, robot, log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5,
                 arms=AO.FakeArms() if live else None, live=live)
    r.lift = SO.LiftOutput(dash, r._log) if (live and lift) else None
    r.light = SO.LightOutput(dash, r._log) if (live and light) else None
    end = r.run()
    t0 = None
    for line in log.getvalue().splitlines():
        if '"RUNNING"' in line and t0 is None:
            t0 = True
    return end, dash.calls, log.getvalue()


def lifts(calls):
    return [(t, c) for t, kind, c in calls if kind == "mast"]


def lights(calls):
    return [(t, b) for t, kind, b in calls if kind == "light"]


print("lift: a live run")
end, calls, log = run()
L = lifts(calls)
ups = [t for t, c in L if c == "up"]
check("the run completes", end[0] == "COMPLETED", end)
check("an up cue is renewed about every 0.1 s for its 1.0 s (inside the StamPLC's 250 ms expiry)",
      9 <= len(ups) <= 12 and max(b - a for a, b in zip(ups, ups[1:])) < 0.25, ups)
stop_after_up = [t for t, c in L if c == "stop" and t >= ups[-1]]
check("... then a stop when its time is up", stop_after_up and stop_after_up[0] - ups[0] < 1.15, L)
check("the run's end sends a final stop", L[-1][1] == "stop", L[-3:])
check("never MAST ACK: the show cannot authorize a descent through the pinch zone",
      all(c in ("up", "down", "stop") for _, c in L))

print("\nlift: every end stops it now")
end, calls, log = run(flip_after=1.4, change={"deadman": False})     # the lift starts at ~1.0 s
L = lifts(calls)
check("a deadman release mid-lift aborts the run and STOPS the lift at once, nothing after it",
      end[0] == "ABORTED" and any(c == "up" for _, c in L) and L[-1][1] == "stop"
      and L[-1][0] <= 1000.0 + 1.4 + 0.05, L[-4:])
end, calls, log = run(flip_after=1.0, change={"mast_operator_seq": 1})
L = lifts(calls)
t_flip = 1000.0 + 1.0
check("an operator's lift command during the run: the show's lift stands down for the rest of the run",
      end[0] == "COMPLETED" and not any(c in ("up", "down") for t, c in L if t > t_flip + 0.05)
      and "operator wins" in log, L)
check("... and every live run says, at its start, that the head's RAISE/LOWER are NOT seen",
      "are NOT seen" in log and "deadman or STOP" in log, log[:600])
end, calls, log = run(fail=True)
check("no dashboard: the lift stands down, logged once, and the show plays on",
      end[0] == "COMPLETED" and log.count("lift output stands down") == 1, log[-400:])

print("\nlift and light: twin-only when not live")
end, calls, log = run(live=False)
check("a run that is not live sends nothing to the lift or the light", end[0] == "COMPLETED" and not calls, calls)

print("\nlight")
end, calls, log = run(lift=False)
G = lights(calls)
holds = [(t, b["rgb"]) for t, b in G if "rgb" in b]
check("two colours 0.1 s apart merge: the first now, the latest at least 0.33 s later, none between",
      holds and holds[0][1] == [0, 200, 80] and any(r == [0, 90, 255] for _, r in holds)
      and min(t for t, r in holds if r == [0, 90, 255]) - holds[0][0] >= 0.33 - 1e-6, holds[:4])
check("the colour's lease is renewed while it holds (every ~0.2 s, a 600 ms lease)",
      sum(1 for _, b in G if b.get("lease_ms") == 600) >= 5)
check("release ends the lease; the run's end releases again", G[-1][1] == {"release": True}
      and sum(1 for _, b in G if b.get("release")) >= 2, G[-3:])
changes = [t for i, (t, rgb) in enumerate(holds) if i == 0 or rgb != holds[i - 1][1]]
check("never more than one colour CHANGE per 0.33 s (the strip shares the ESCs' I2C)",
      all(b - a >= 0.33 - 1e-6 for a, b in zip(changes, changes[1:])), changes)

print("\nthe palette: separately armed")
pal = R.Palette(os.path.dirname(tmp.name), LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO(),
                arms=AO.FakeArms(), dashboard=FakeDash(lambda: 0.0), lift_live=True, light_live=False)
check("lift armed, light not: the palette arms only what it was told to", pal.lift_live and not pal.light_live)
pal = R.Palette(os.path.dirname(tmp.name), LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO(),
                arms=AO.FakeArms(), dashboard=None, lift_live=True, light_live=True)
check("no dashboard: neither is armed", not pal.lift_live and not pal.light_live)
check("show_outputs.base_of turns the robot-state URL into the dashboard's base",
      SO.base_of("http://127.0.0.1:8080/api/choreo/robot") == "http://127.0.0.1:8080")

os.unlink(tmp.name)
print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
