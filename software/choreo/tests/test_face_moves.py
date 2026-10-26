#!/usr/bin/env python3
"""Eye MOVES in a show (head protocol v4, `MOVE <name>`): the vocabulary, the cue time being the
move's PEAK (the line sent early by the face's measured offset), the face bridge sending it once
and never replaying it, and the pins to the face's own published table."""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402
import face_bridge  # noqa: E402

LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
REPO = os.path.join(HERE, "..", "..", "..")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def show(cues: str):
    return cue.show_from_text("id: m\nslots: [FACE]\nrequires: { tab5: true }\ncues:\n" + cues, "m")


def face_track(s):
    return s.export(LIMITS, LIMITS_PATH)["tracks"]["FACE"]


print("moves: the vocabulary")
a = show("  - { t: 2.0, slot: FACE, do: move, name: wink_l }\n").analyse(LIMITS)
check("a known move validates", not a["problems"], [q.msg for q in a["problems"]])
a = show("  - { t: 2.0, slot: FACE, do: move, name: wink }\n").analyse(LIMITS)
check("an unknown move is refused, naming the ones there are",
      any("wink_l" in q.msg and "not one the face has" in q.msg for q in a["problems"]), [q.msg for q in a["problems"]])
a = show("  - { t: 2.0, slot: FACE, do: emote, name: SOFT }\n"
         "  - { t: 2.0, slot: FACE, do: move, name: bob }\n"
         "  - { t: 2.1, slot: FACE, do: skin, name: ring }\n").analyse(LIMITS)
check("a move layers over the held emote: no slot collision with an emote or a skin beside it",
      not a["problems"], [q.msg for q in a["problems"]])

print("\nmoves: the cue time is the PEAK")
tr = face_track(show("  - { t: 2.0, slot: FACE, do: move, name: blink2 }\n"))
mv = [e for e in tr if e["cue"].get("do") == "move"][0]
check("the event keeps the author's time (the peak) and is sent its offset early",
      mv["t"] == 2.0 and abs(mv["send_t"] - (2.0 - cue.FACE_MOVES["blink2"][0])) < 1e-6, mv)
check("blink2's offset is its SECOND shut (~0.333 s), not its first (~0.073 s)", cue.FACE_MOVES["blink2"][0] > 0.3)
a = show("  - { t: 0.05, slot: FACE, do: move, name: look_l }\n").analyse(LIMITS)
check("a move too early to be sent its lead ahead is a notice", any("cannot be sent" in n for n in a["notices"]),
      a["notices"])
a = show("  - { t: 1.0, slot: FACE, do: skin, name: cookie }\n"
         "  - { t: 2.0, slot: FACE, do: move, name: sparkle }\n").analyse(LIMITS)
check("a sparkle on a picture skin is a notice (the face answers OK and draws nothing)",
      any("sparkle" in n and "cannot draw" in n for n in a["notices"]), a["notices"])
a = show("  - { t: 2.0, slot: FACE, do: move, name: sparkle }\n").analyse(LIMITS)
check("... and on the standard eyes it is not", not any("sparkle" in n for n in a["notices"]), a["notices"])
# A CONTENT code skin (the content folder's face/skins/, through the head protocol's looks.py): a fixture
# folder here, never the content repository
_was = os.environ.get("OPENDUBS_SHOWS_DIR")
os.environ["OPENDUBS_SHOWS_DIR"] = os.path.join(HERE, "fixtures", "content")
try:
    a = show("  - { t: 1.0, slot: FACE, do: skin, name: fixture_skin }\n"
             "  - { t: 2.0, slot: FACE, do: move, name: sparkle }\n").analyse(LIMITS)
    check("a content code skin is a skin the face draws: valid with no library, and a sparkle on it is drawn",
          not a["problems"] and not any("sparkle" in n for n in a["notices"])
          and not any("fixture_skin" in n for n in a["notices"]), ([q.msg for q in a["problems"]], a["notices"]))
    check("... and it is not a library item to send to the robot",
          "fixture_skin" in cue.code_skins() and "fixture_skin" not in cue.CODE_SKINS)
finally:
    if _was is None:
        os.environ.pop("OPENDUBS_SHOWS_DIR", None)
    else:
        os.environ["OPENDUBS_SHOWS_DIR"] = _was

print("\nmoves: the face bridge")
tl = show("  - { t: 1.0, slot: FACE, do: emote, name: SOFT }\n"
          "  - { t: 2.0, slot: FACE, do: move, name: wink_l }\n").export(LIMITS, LIMITS_PATH)
f = face_bridge.Follower()
f.update(1, tl, 0.0, True)
sent = {}
t = 0.0
while t < 3.0:
    t = round(t + 0.02, 3)
    for line in f.update(1, tl, t, True):
        sent.setdefault(line, t)
lead = cue.FACE_MOVES["wink_l"][0]
check("MOVE wink_l is sent once, its offset before the beat", "MOVE wink_l" in sent
      and 2.0 - lead - 0.02 - 1e-9 <= sent["MOVE wink_l"] <= 2.0 - lead + 0.02 + 1e-9, sent)
f2 = face_bridge.Follower()
lines = f2.update(1, tl, 2.5, True)                     # joining after the move: a resync
check("joining or seeking past a move never replays it (a move is not state)",
      not any(x.startswith("MOVE") for x in lines) and "EMOTE SOFT" in lines, lines)

print("\nmoves: pinned to the face's own table")
hp_dir = os.path.join(REPO, "software", "head-protocol")
if os.path.isfile(os.path.join(hp_dir, "head_protocol.py")):
    sys.path.insert(0, hp_dir)
    import head_protocol  # noqa: E402
    if hasattr(head_protocol, "MOVES"):
        check("cue.FACE_MOVES is the head protocol's MOVES",
              set(head_protocol.MOVES) == set(cue.FACE_MOVES), head_protocol.MOVES)
    else:
        print("  skip  head_protocol.MOVES is not in this tree yet (protocol v4)")
face_js = os.path.join(REPO, "software", "face-web", "engine", "face.js")
src = open(face_js, encoding="utf-8").read() if os.path.isfile(face_js) else ""
table = dict((n, (int(p) / 1000.0, int(d) / 1000.0)) for n, d, p in
             re.findall(r"^\s*(\w+):\s*\{\s*dur:\s*(\d+),\s*peak:\s*(\d+)", src.split("export const MOVES", 1)[1], re.M)) \
    if "export const MOVES" in src else {}
if table:
    check("every move's peak offset and duration are the face's published, measured ones",
          table == {k: tuple(v) for k, v in cue.FACE_MOVES.items()}, table)
else:
    print("  skip  face-web's engine/face.js has no MOVES table in this tree yet")

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
