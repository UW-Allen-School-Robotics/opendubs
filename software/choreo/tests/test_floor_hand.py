#!/usr/bin/env python3
"""The floor hand (hand_output.py): when a crawling hand may play a show on the robot, the real hand's
moves on the show's clock (surge_at.ShowPlayer), and a live run driving it — opened while the arms
travel, streamed from the show's start, and left OPEN on every end; and the can_skip fallback when it is
not there. A fake driver stands in for the hand: no port, no hardware."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output as AO  # noqa: E402
import cue  # noqa: E402
import effectors as E  # noqa: E402
import hand_output as H  # noqa: E402
import runner as R  # noqa: E402

FAILS = []
LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
R.REQUIRE_SWEEP = False
TMP = tempfile.mkdtemp(prefix="opendubs-floor-hand-")
PORT = os.path.join(TMP, "ttyRS485")              # a path that exists, as the adapter's would
open(PORT, "w").close()


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def show_text(cues: str, tool="surge_left", extra="", start=1.0) -> str:
    return ("id: fh\nname: floor hand\nrequires: { rc_authority: AUTO }\nslots: [CRAWL]\n"
            "music: { bpm: 120, offset_s: 0, beats_per_bar: 4 }\n"
            f"tools: {{ base: {{ type: {tool}, mode: crawl }} }}\n"
            "crawler: { start: [" + f"{start}" + ", 0.0, 0.0], keep_off_m: 0.25, tether: { anchor: base_front, length_m: 1.8 }, "
            "area: { x: [0.75, 2.6], y: [-1.1, 1.1] } }\n" + extra + "cues:\n" + cues)


def export(cues: str, **kw) -> dict:
    return cue.show_from_text(show_text(cues, **kw), "").export(LIMITS)


def write(cues: str, **kw) -> str:
    p = os.path.join(TMP, f"show{len(os.listdir(TMP))}.yaml")
    open(p, "w").write(show_text(cues, **kw))
    return p


TESTED = ("  - { t: 0.5, slot: CRAWL, do: walk, gait: bare_flexstroke, secs: 1 }\n"
          "  - { t: 2.0, slot: CRAWL, do: dance, pattern: ripple, secs: 1 }\n")


class FakeDriver:
    """drivers/surge_at.SurgeAT's interface, recorded. `answers`: the hand replies to queries."""
    calls: list = []
    answers = True

    def open(self, port, baud=115200, startup=True):
        FakeDriver.calls.append(("open", startup))
        a = 10.0 if FakeDriver.answers else None
        return {"angles": {j: a for j in "IMRPTX"}, "errors": {}}

    def start_stream(self):
        FakeDriver.calls.append(("stream",))

    def apply(self, t, joints):
        FakeDriver.calls.append(("apply", dict(joints)))
        return len(joints)

    def close(self, home=True):
        FakeDriver.calls.append(("close", home))


def hand(fitted=None, port=PORT):
    return H.FloorHand(port, driver=FakeDriver, fitted={"base": "surge_left"} if fitted is None else fitted)


print("floor hand: may it play this show?")
tl = export(TESTED)
check("a show of moves the real hand has run validates", tl["valid"], tl["problems"])
check("it NEEDS the floor_hand part (from its CRAWL cues)", "floor_hand" in tl["needs"], tl["needs"])
check("its moves have all run on the real hand", E.hand_untested(tl) == [], E.hand_untested(tl))
FakeDriver.calls = []
check("fitted, a driver, tested moves and the hand answering: ok",
      H.availability(tl, hand()) == {"state": "ok", "why": None} and FakeDriver.calls == [("open", False), ("close", False)],
      FakeDriver.calls)
check("... the probe only queries: it opens read-only and never sends the open pose (the hand does not move)",
      ("open", True) not in FakeDriver.calls and ("close", True) not in FakeDriver.calls)
why = lambda tl, h: H.availability(tl, h)["why"] or ""  # noqa: E731
check("no floor-hand output on this runner: unavailable, saying so", "--hand" in why(tl, None), why(tl, None))
check("the robot's tools file has no hand at base: unavailable, naming what it has",
      "no hand on the floor" in why(tl, hand(fitted={})), why(tl, hand(fitted={})))
check("the adapter unplugged: unavailable, naming the port", "not plugged in" in why(tl, hand(port=PORT + "-gone")))
FakeDriver.answers = False
check("the hand silent on its port: unavailable — is it powered, the e-stop clear?", "powered" in why(tl, hand()))
FakeDriver.answers = True
r_tl = export(TESTED, tool="surge_right")
check("a hand type with no driver (the right hand): unavailable, twin only", "no driver" in why(r_tl, hand()), why(r_tl, hand()))
un = export("  - { t: 0.5, slot: CRAWL, do: turn, by_deg: 30 }\n  - { t: 9, slot: CRAWL, do: dance, pattern: beckon, secs: 2 }\n",
            start=1.5)
check("a pivot and an untested dance are moves never run on the real hand: unavailable, naming them",
      un["valid"] and "pivot" in why(un, hand()) and "beckon" in why(un, hand()), why(un, hand()))

print("\nfloor hand: the real hand's moves on the show's clock")
sat = hand().mod()
seq = E.hardware_sequence(tl, "t")
check("every segment of the hardware sequence carries its show time", all("t" in s for s in seq["segments"]))
pl = sat.ShowPlayer(seq)
gait = next(s for s in seq["segments"] if s["type"] == "gait")
g = {**sat.GAIT_DEFAULTS, **gait["params"]}
mid = gait["t"] + 0.8
want = sat.gait_targets(g, mid - gait["t"] - min(sat.BLEND_S, gait["seconds"] / 2))
pl.targets(gait["t"] + 0.01)
got = pl.targets(mid)
check("the walk plays the gait at the SHOW's time, its blend inside the segment (nothing accumulates)",
      got and all(abs(got[j] - want[j]) < 1e-6 for j in want), (got, want))
check("before the show and after its end, nothing is sent", pl.targets(-1.0) is None and pl.targets(pl.end_s() + 1) is None)
try:
    sat.ShowPlayer({"segments": [{"type": "pose", "t": 0, "pose": 1}]})
    ok = False
except ValueError:
    ok = True
check("a closing preset is refused on the show's clock, as on the bench", ok)

print("\nfloor hand: a live run")


def live(path, h, skip=(), stop_after=None):
    FakeDriver.calls = []
    log = io.StringIO()
    r = R.Runner(path, LIMITS_PATH, R.FakeRobotState(), log=log, start_lead=0.3, arms=AO.FakeArms(), live=True,
                 hand=h, skip=skip)
    if stop_after:
        threading.Timer(stop_after, r.request_stop).start()
    end = r.run()
    time.sleep(0.3)                               # its thread opens the hand and closes the port
    return r, end, [json.loads(x) for x in log.getvalue().splitlines()]


p = write(TESTED)
r, end, lines = live(p, hand())
ops = [c[0] for c in FakeDriver.calls]
applied = [c for c in FakeDriver.calls if c[0] == "apply"]
check("the hand plays the show: probed, opened to work, streamed, and left OPEN at the end",
      end[0] == "COMPLETED" and ops[:2] == ["open", "close"] and ("open", True) in FakeDriver.calls
      and "stream" in ops and len(applied) >= 20 and FakeDriver.calls[-1] == ("close", True), (end, ops[:6], ops[-3:]))
check("... and it says so in the run's log", any(x["kind"] == "hand" and "open and stopped" in x.get("note", "") for x in lines))
r, end, _ = live(write(TESTED + "  - { t: 3.5, slot: CRAWL, do: walk, gait: bare_flexstroke, secs: 6 }\n"), hand(),
                 stop_after=2.5)
check("an operator stop mid-walk: the run aborts and the hand is left OPEN",
      end[0] == "ABORTED" and FakeDriver.calls[-1] == ("close", True), (end, FakeDriver.calls[-2:]))

r, end, _ = live(p, hand(port=PORT + "-gone"))
check("no hand, and the show cannot do without it: refused, EFFECTOR_UNAVAILABLE, saying why",
      end[:2] == ("REFUSED", "EFFECTOR_UNAVAILABLE") and "not plugged in" in end[2], end)
ps = write(TESTED, extra="can_skip: [floor_hand]\n")
r, end, _ = live(ps, hand(port=PORT + "-gone"))
check("... with floor_hand in can_skip: the operator is offered the show without it",
      end[:2] == ("REFUSED", "NEEDS_APPROVAL") and r.offer == ["floor_hand"], (end, r.offer))
r, end, _ = live(ps, hand(port=PORT + "-gone"), skip=("floor_hand",))
check("... and approved, it runs without the hand, which is never touched",
      end[0] == "COMPLETED" and r.hand_run is None and not any(c[0] == "apply" for c in FakeDriver.calls), end)
r, end, _ = live(write("  - { t: 0.5, slot: CRAWL, do: turn, by_deg: 30 }\n", start=1.5), hand())
check("a show with a pivot (never run on the real hand) is refused live, naming it",
      end[:2] == ("REFUSED", "EFFECTOR_UNAVAILABLE") and "pivot" in end[2], end)
clk = {"t": 1000.0}
r = R.Runner(p, LIMITS_PATH, R.FakeRobotState(), log=io.StringIO(), start_lead=0.3, hand=hand(),
             clock=lambda: clk["t"], sleep=lambda s: clk.__setitem__("t", clk["t"] + s))
check("a runner without arms (the twin) plays the crawl without asking the hand",
      r.hand_out is None and r.run()[0] in ("COMPLETED",))

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
