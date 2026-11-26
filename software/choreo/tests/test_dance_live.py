#!/usr/bin/env python3
"""DANCE on the real base (base_output.py; notes/design/choreo-dance-live.md): the targets the runner
streams to the Thor's base tracker — the same path the twin draws, on the show's clock, ending at home —
the origin latched once at t0, a stop on every end but a completion, and a tracker that is not ready
(the show runs without its base moves, or is refused when it requires them). A fake link stands in for
ROS: no tracker, no robot."""
from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output as AO  # noqa: E402
import base_output as B  # noqa: E402
import cue  # noqa: E402
import runner as R  # noqa: E402
import sim_preview as P  # noqa: E402

FAILS = []
LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
R.REQUIRE_SWEEP = False
TMP = tempfile.mkdtemp(prefix="opendubs-dance-live-")


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def show(cues: str, dance="dance: { floor_radius_m: 2.0 }\n") -> str:
    p = os.path.join(TMP, f"s{len(os.listdir(TMP))}.yaml")
    open(p, "w").write("id: d\nname: d\nrequires: { rc_authority: AUTO }\nslots: [DANCE]\n" + dance + "cues:\n" + cues)
    return p


class FakeLink:
    """The tracker's topics, recorded. `status`: what /dance/status says (None: no tracker)."""

    def __init__(self, status=None):
        self.st = {"ready": True, "why": None, "guard": "clear"} if status is None else status
        self.sent = []

    def status(self):
        return self.st or None

    def origin(self):
        self.sent.append(("origin", time.monotonic()))

    def target(self, c):
        self.sent.append(("target", dict(c)))

    def stop(self):
        self.sent.append(("stop",))


print("dance on the base: the targets")
ev = [{"start": 1.0, "cue": {"move": "box", "secs": 2.0}}]
check("before a move, nothing is sent (the tracker holds still)", B.dance_command(ev, 0.5) is None)
c = B.dance_command(ev, 1.3)
twin = P._dance_pose(ev, 1.3)
check("mid-move, the target is the pose the twin draws (same model, same speeds)",
      c and abs(c["x"] - twin[0]) < 1e-9 and abs(c["y"] - twin[1]) < 1e-9 and abs(math.degrees(c["yaw"]) - twin[2]) < 1e-9,
      (c, twin))
check("... and its feed-forward is the BODY velocity of the move's segment (box: forward, then left)",
      c["vx"] > 0 and c["vy"] == 0 and B.dance_command(ev, 1.8)["vy"] > 0, (c, B.dance_command(ev, 1.8)))
end = B.dance_command(ev, 2.999)
check("every move ends at home (it integrates to zero)", math.hypot(end["x"], end["y"]) < 0.01, end)
s = B.dance_command(ev, 3.5)
check(f"after it, the home target is held still for {B.SETTLE_S:g} s, so the tracker closes the residual",
      s["settle"] and s["x"] == s["y"] == s["vx"] == s["w"] == 0.0, s)
check("then nothing (the tracker stops)", B.dance_command(ev, 3.0 + B.SETTLE_S + 0.01) is None)

print("\ndance on the base: is the tracker ready?")
rd = lambda st: B.BaseOutput(FakeLink(st)).readiness()  # noqa: E731
check("no status: not running, saying so", "not running" in rd(False)["why"], rd(False))
check("not ready: its own reason", rd({"ready": False, "why": "odom stale"})["why"].endswith("odom stale"))
check("ready", rd({"ready": True})["ready"])
check("no origin latched yet does not matter: the run latches its own at t0 (`origin` is not `ready`)",
      rd({"ready": True, "origin": False, "guard": "clear"})["ready"])
check("a blind guard is not ready", not rd({"ready": False, "why": "scan /scan_front 0.8 s old", "guard": "blind"})["ready"])

print("\ndance on the base: a live run")


def live(path, link, stop_after=None, base=True):
    log = io.StringIO()
    r = R.Runner(path, LIMITS_PATH, R.FakeRobotState(), log=log, start_lead=0.3, arms=AO.FakeArms(),
                 live=True, base=B.BaseOutput(link) if base else None)
    if stop_after:
        threading.Timer(stop_after, r.request_stop).start()
    end = r.run()
    time.sleep(B.SETTLE_S + 0.3)                  # a completed run's last move settles after the show
    return r, end, [json.loads(x) for x in log.getvalue().splitlines()]


p = show("  - { t: 0.5, slot: DANCE, move: sway, secs: 1 }\n  - { t: 3.5, slot: FACE, do: emote, name: SOFT }\n"
         .replace("slot: FACE, do: emote, name: SOFT", "slot: DANCE, move: twist, secs: 0.5"))
link = FakeLink()
r, end, lines = live(p, link)
kinds = [x[0] for x in link.sent]
tg = [x[1] for x in link.sent if x[0] == "target"]
check("the run plays: the origin latched ONCE, before any target", end[0] == "COMPLETED" and kinds.count("origin") == 1
      and kinds.index("origin") < kinds.index("target"), (end, kinds[:4]))
check("... targets stream at ~20 Hz through each move and its settle — the last one too, though the show ends "
      "inside it", len(tg) > 0.8 * B.RATE_HZ * (1 + 0.5 + 2 * B.SETTLE_S) and tg[-1]["settle"], (len(tg), tg[-1:]))
check("... nothing is sent between moves (the tracker stops)", any(kinds[i:i + 1] == ["target"] for i in range(len(kinds)))
      and not [x for x in tg if x is None], len(tg))
check("... and a completed show stops once, AFTER its last settle, which hands the base back",
      kinds.count("stop") == 1 and kinds[-1] == "stop" and kinds[-2] == "target" and link.sent[-2][1]["settle"],
      kinds[-3:])
link = FakeLink()
r, end, _ = live(show("  - { t: 0.3, slot: DANCE, move: rock, secs: 4 }\n"), link, stop_after=1.5)
check("an operator stop mid-move: the run aborts and the tracker is told to STOP at once",
      end[0] == "ABORTED" and link.sent[-1] == ("stop",), (end, link.sent[-2:]))

link = FakeLink({"ready": False, "why": "the LIDARs are not both up"})
r, end, lines = live(p, link)
check("the tracker not ready: the show runs WITHOUT its base moves, saying why",
      end[0] == "COMPLETED" and not link.sent and "LIDARs" in (r.base_note or ""), (end, r.base_note))
r, end, _ = live(show("  - { t: 0.5, slot: DANCE, move: sway, secs: 1 }\n",
                      dance="dance: { floor_radius_m: 2.0, required: true }\n"), FakeLink({"ready": False, "why": "odom stale"}))
check("... unless the show requires them: refused, SHOW_BASE_NOT_READY, with the tracker's reason",
      end[:2] == ("REFUSED", "SHOW_BASE_NOT_READY") and "odom stale" in end[2], end)
link = FakeLink()
r, end, _ = live(p, link, base=False)
check("a runner with no base output (--base none): the base never moves, the twin plays it",
      end[0] == "COMPLETED" and not link.sent)

print("\ndance on the base: the show")
bad = cue.show_from_text("id: d\nname: d\nslots: [DANCE]\ndance: { floor_radius_m: 2.0, required: yes please }\n"
                         "cues:\n  - { t: 0.5, slot: DANCE, move: sway, secs: 1 }\n", "").export(LIMITS)
check("dance: required must be true or false", any("required must be true or false" in q["msg"] for q in bad["problems"]))
ok = cue.show_from_text("id: d\nname: d\nslots: [DANCE]\ndance: { floor_radius_m: 2.0 }\n"
                        "cues:\n  - { t: 0.5, slot: DANCE, move: sway, secs: 1 }\n", "").export(LIMITS)
check("a DANCE show says how to stage it: away from glass, an out-and-back first on a polished floor",
      any("glass" in n and "out-and-back" in n for n in ok["notices"]), ok["notices"])

print("\nBASE: the base goes somewhere (a pose in the show's starting frame), on the same tracker")


def probs(text):
    return [q["msg"] for q in cue.show_from_text(text, "").export(LIMITS)["problems"]]


HEAD_ = "id: b\nname: b\nrequires: { rc_authority: AUTO }\nslots: [BASE, DANCE]\n"
TRAVEL = ("  - { t: 12, slot: BASE, x: 1.0, y: 0.5, yaw: 30 }\n"     # 1.1 m and 30 deg over 12 s
          "  - { t: 14, slot: DANCE, move: sway, secs: 1 }\n")
check("a travel inside the floor and the tracker's budget is a valid show",
      probs(HEAD_ + "dance: { floor_radius_m: 2.5 }\ncues:\n" + TRAVEL) == [])
fast = probs(HEAD_ + "dance: { floor_radius_m: 2.5 }\ncues:\n  - { t: 1, slot: BASE, x: 1.0 }\n")
check("a move too fast for the tracker's budget is refused, with the time it needs",
      any("of the show cap at its peak" in q and "give it at least" in q for q in fast), fast)
far = probs(HEAD_ + "dance: { floor_radius_m: 1.0 }\ncues:\n" + TRAVEL)
check("a pose outside the declared floor radius is refused", any("outside the declared floor radius" in q for q in far), far)
both_ = probs(HEAD_ + "dance: { floor_radius_m: 2.5 }\ncues:\n  - { t: 12, slot: BASE, x: 1.0 }\n"
              "  - { t: 3, slot: DANCE, move: sway, secs: 1 }\n")
check("a DANCE while a BASE move runs is refused: the base has one move at a time",
      any("while a BASE move runs" in q for q in both_), both_)
nofloor = probs(HEAD_ + "cues:\n  - { t: 12, slot: BASE, x: 1.0 }\n")
check("a show that travels must declare its floor space", any("declares no floor space" in q for q in nofloor), nofloor)

ev = cue.show_from_text(HEAD_ + "dance: { floor_radius_m: 2.5 }\ncues:\n" + TRAVEL, "").export(LIMITS)["tracks"]
segs = P.base_segments_of(ev["BASE"])
mid, end_ = B.base_command(ev["DANCE"], segs, 6.0), B.base_command(ev["DANCE"], segs, 12.5)
check("mid-move the target is on the way, moving forward in the body frame",
      0.2 < mid["x"] < 0.8 and mid["vx"] > 0 and not mid["settle"], mid)
check("after it arrives the pose is held for the settle (never home), then nothing is sent",
      end_["settle"] and abs(end_["x"] - 1.0) < 1e-9 and abs(end_["yaw"] - math.radians(30)) < 1e-9
      and B.base_command([], segs, 12.0 + B.SETTLE_S + 0.1) is None, end_)
d = B.base_command(ev["DANCE"], segs, 14.5)
sway_home = B.dance_command(ev["DANCE"], 15.2)
check("a DANCE after it plays around where BASE put the base, and settles there, not at the show's start",
      math.hypot(d["x"] - 1.0, d["y"] - 0.5) < 0.3 and sway_home["settle"]
      and abs(B.base_command(ev["DANCE"], segs, 15.2)["x"] - 1.0) < 1e-9, (d, B.base_command(ev["DANCE"], segs, 15.2)))
tw = P.base_pose(ev["DANCE"], 13.0, None, ev["BASE"])
check("the twin draws the same: at the BASE pose after the move (x, y, yaw in degrees)",
      abs(tw[0] - 1.0) < 1e-6 and abs(tw[1] - 0.5) < 1e-6 and abs(tw[2] - 30) < 1e-6, tw)

pb = os.path.join(TMP, "travel.yaml")
open(pb, "w").write(HEAD_ + "dance: { floor_radius_m: 2.5 }\ncues:\n  - { t: 2.5, slot: BASE, x: 0.3 }\n")
link = FakeLink()
r, end, _ = live(pb, link)
tg = [x[1] for x in link.sent if x[0] == "target"]
kinds = [x[0] for x in link.sent]
check("a live run with only BASE: origin once, the targets stream to the pose, one stop after its settle",
      end[0] == "COMPLETED" and kinds.count("origin") == 1 and abs(tg[-1]["x"] - 0.3) < 1e-9 and tg[-1]["settle"]
      and kinds.count("stop") == 1 and kinds[-1] == "stop", (end, kinds[:3], tg[-1:] if tg else None))

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
