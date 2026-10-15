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
    try:
        render.render(SHOW, os.path.join(td, "c.mp4"), layout="b", limits=LIMITS, log=lambda *_: None)
        check("a layout that needs the face track says so (until the face page's render mode lands)", False)
    except SystemExit as e:
        check("a layout that needs the face track says so (until the face page's render mode lands)",
              "face" in str(e), str(e))

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
