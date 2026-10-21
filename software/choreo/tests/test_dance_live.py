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
check("... and a completed show sends no stop (the last target was home)", "stop" not in kinds, kinds[-3:])
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

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
