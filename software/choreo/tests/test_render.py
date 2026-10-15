#!/usr/bin/env python3
"""The offline render (render.py): a short span of a show, rendered twice — every frame must be
identical (deterministic), the file must hold H.264 video and AAC audio of the right length, and the
audio mix must follow the show's DEVICE volume. Needs MuJoCo and imageio-ffmpeg; on Linux CI the
off-screen GL is osmesa (MUJOCO_GL), as the studio's selftest uses."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import render  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
SHOW = os.path.join(HERE, "..", "shows", "reach.yaml")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


print("render: deterministic, and a real file")
with tempfile.TemporaryDirectory() as td:
    h1, h2 = [], []
    a = render.render(SHOW, os.path.join(td, "a.mp4"), fps=10, size=(320, 180), limits=LIMITS, t_from=1.0,
                      t_to=3.0, hashes=h1, log=lambda *_: None)
    render.render(SHOW, os.path.join(td, "b.mp4"), fps=10, size=(320, 180), limits=LIMITS, t_from=1.0,
                  t_to=3.0, hashes=h2, log=lambda *_: None)
    check("two renders of the same span give identical frames, every one", h1 == h2 and len(h1) == 20,
          (len(h1), len(h2), sum(x != y for x, y in zip(h1, h2))))
    check("... and the robot moves within the span (not one frame repeated)", len(set(h1)) > 5, len(set(h1)))
    info = subprocess.run([render.ffmpeg(), "-i", os.path.join(td, "a.mp4")], capture_output=True, text=True).stderr
    check("the file holds H.264 video and AAC audio", "Video: h264" in info and "Audio: aac" in info, info[-400:])
    check("... 2.0 s long", "Duration: 00:00:02.0" in info, [l for l in info.splitlines() if "Duration" in l])
    said = []
    render.render(SHOW, os.path.join(td, "c.mp4"), fps=10, size=(320, 180), limits=LIMITS, t_from=1.0, t_to=1.5,
                  log=said.append)
    check("the default layout is side by side (or the robot alone, said so, where no browser can draw the face)",
          any("layout c" in m for m in said) or any("could not be drawn" in m for m in said), said[-2:])
    check("... and the framing is computed from the span's own extents", any("framed" in m for m in said), said)

print("\nlayouts, with a stand-in face (no browser needed)")


class StubFace:
    """A face source: a pattern that changes with show time and with each line the bridge sends."""

    def __init__(self):
        self.lines = []

    def frame(self, lines, t):
        self.lines += list(lines)
        px = np.zeros((90, 160, 4), dtype=np.uint8)
        px[..., 0] = int(t * 40) % 256
        px[..., 1] = (len(self.lines) * 37) % 256
        px[..., 2] = 200
        px[..., 3] = 255
        return px, ["OK"] * len(lines)

    def close(self):
        pass


with tempfile.TemporaryDirectory() as td:
    for lay in ("c", "b", "a"):
        hs = []
        for k in range(2):
            stub = StubFace()
            render.render(SHOW, os.path.join(td, f"{lay}{k}.mp4"), layout=lay, fps=10, size=(320, 180), limits=LIMITS,
                          t_from=0.5, t_to=1.5, hashes=hs, log=lambda *_: None, face=stub)
        check(f"layout {lay}: two renders with the same face give identical frames", hs[:10] == hs[10:] and len(hs) == 20,
              (len(hs), sum(x != y for x, y in zip(hs[:10], hs[10:]))))
    check("the face source is fed the live bridge's lines (the show's face state, at least once)", len(stub.lines) > 0,
          stub.lines[:3])
    p = render.plan("c", 1280, 720)
    check("layout c: the robot in a tall pane on the left, the face filling the rest, centred",
          p["robot"] == (576, 720, 0, 0) and p["face"][0] == 704 and p["face"][2] == 576
          and p["face"][3] == (720 - p["face"][1]) // 2, p)
    p = render.plan("b", 1280, 720)
    check("layout b: the robot full frame, the face inset bottom-right at 30% width inside a margin",
          p["robot"][:2] == (1280, 720) and p["face"][0] == 384 and p["face"][2] + p["face"][0] < 1280
          and p["face"][3] + p["face"][1] < 720, p)
    pc = render.plan("c", 320, 180)
    img = render.compose(pc, 320, 180, np.full((180, pc["robot"][0], 3), 50, np.uint8),
                         np.full((pc["face"][1], pc["face"][0], 3), 200, np.uint8))
    check("compose puts the robot left and the face right", img[90, 10, 0] == 50 and img[90, 300, 0] == 200
          and img[2, 300, 0] == 0, (img[90, 10], img[90, 300], img[2, 300]))

print("\nthe real face page, when a browser is here")
try:
    import playwright  # noqa: F401
    have = True
except ImportError:
    have = False
if not have:
    print("  skip  no Playwright here: the real-page check did not run")
else:
    with tempfile.TemporaryDirectory() as td:
        h1, h2, said = [], [], []
        try:
            render.render(SHOW, os.path.join(td, "p1.mp4"), fps=10, size=(320, 180), limits=LIMITS, t_from=0.0,
                          t_to=1.0, hashes=h1, log=said.append, seed=7)
            render.render(SHOW, os.path.join(td, "p2.mp4"), fps=10, size=(320, 180), limits=LIMITS, t_from=0.0,
                          t_to=1.0, hashes=h2, log=said.append, seed=7)
        except Exception as e:  # noqa: BLE001
            said.append(f"error {e}")
        if any("could not be drawn" in m for m in said):
            print("  skip  no browser for the face page here:", next(m for m in said if "could not be drawn" in m)[:120])
        else:
            check("the real face page, the same seed twice: identical frames, every one", h1 == h2 and len(h1) == 10,
                  (len(h1), sum(x != y for x, y in zip(h1, h2)), said[-2:]))

print("\nthe audio mix follows the show's volume")
t = np.linspace(0, 10, 1001)
tl = {"device": {"volume": 0.6}, "tracks": {"DEVICE": [{"t": 4.0, "cue": {"do": "volume", "level": 0.2, "fade_s": 2.0}},
                                                     {"t": 8.0, "cue": {"do": "brightness", "level": 0.5}}]}}
v = render.volume_at(tl, t)
check("the show-level volume from time 0, a cue's fade to its level, a brightness cue ignored",
      abs(v[0] - 0.6) < 1e-9 and abs(v[500] - 0.4) < 1e-6 and abs(v[700] - 0.2) < 1e-9 and abs(v[-1] - 0.2) < 1e-9,
      (v[0], v[500], v[700], v[-1]))
check("no device volume at all: full level (the head's own)", np.all(render.volume_at({"tracks": {}}, t) == 1.0))

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
