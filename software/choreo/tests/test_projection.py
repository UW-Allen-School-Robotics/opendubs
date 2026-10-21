#!/usr/bin/env python3
"""The twin's projection screen (projection.py): which picture shows at a show time — by the show's own
clock, from the export's VIDEO track — and the decoded frames' cache. The timing needs nothing; the
decode needs ffmpeg and skips, saying so, without it. No MuJoCo, no audio."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
TMP = tempfile.mkdtemp(prefix="opendubs-projection-")
os.environ["OPENDUBS_PROJECTION_CACHE"] = os.path.join(TMP, "cache")

import projection as PJ  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def ev(t, span_end, **cue):
    return {"t": t, "start": t, "end": span_end, "cue": cue}


print("projection: the picture at a show time")
tr = {"VIDEO": [ev(2.0, 8.0, do="play", video="a", from_s=10.0), ev(12.0, 15.0, do="play", video="b", end="hold"),
                ev(20.0, 20.0, do="stop")]}
check("before the first video: black", PJ.at(tr, 1.0) is None)
check("during it: from_s into its file plus the time since its cue", PJ.at(tr, 5.5) == ("a", 13.5), PJ.at(tr, 5.5))
check("after its end (end: black, the default): black", PJ.at(tr, 9.0) is None)
check("end: hold keeps its last frame after it ends ...", PJ.at(tr, 16.0) == ("b", None), PJ.at(tr, 16.0))
check("... until a stop", PJ.at(tr, 21.0) is None)
cut = {"VIDEO": [ev(1.0, 30.0, do="play", video="a"), ev(5.0, 9.0, do="play", video="b")]}
check("the next video cuts the one before (one projector)", PJ.at(cut, 6.0) == ("b", 1.0), PJ.at(cut, 6.0))
fr = PJ.Frames("/nonexistent.mp4", 64, 36, 15)
fr.offs = [(0, 1)] * 30
check("a frame index clamps to the video, and None is its last frame",
      fr.index(-1) == 0 and fr.index(1.0) == 15 and fr.index(99) == 29 and fr.index(None) == 29)

print("\nprojection: decoding once, into the cache")
ff = PJ.ffmpeg_exe()
try:
    import PIL  # noqa: F401  the frames are JPEG, read with Pillow
except ImportError:
    PIL = None
if not ff or PIL is None:
    print(f"  skip  no {'ffmpeg' if not ff else 'Pillow'} here")
else:
    clip = os.path.join(TMP, "two.mp4")
    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
                    "-pix_fmt", "yuv420p", clip], check=True)
    f1 = PJ.Frames(clip, 64, 36, 15)
    ok1 = f1.load()
    check("a video decodes at the twin's rate and size, letterboxed to 16:9",
          ok1 and abs(len(f1) - 30) <= 1 and f1.rgb(0).shape == (36, 64, 3), (ok1, len(f1), f1.error))
    cached = sorted(os.listdir(os.environ["OPENDUBS_PROJECTION_CACHE"]))
    f2 = PJ.Frames(clip, 64, 36, 15)
    check("... and is read back from the cache the next time (not decoded again)",
          f2.load() and len(f2) == len(f1) and sorted(os.listdir(os.environ["OPENDUBS_PROJECTION_CACHE"])) == cached)
    lib = os.path.join(TMP, "lib")
    os.makedirs(os.path.join(lib, "videos"))
    os.replace(clip, os.path.join(lib, "videos", "two.mp4"))
    with open(os.path.join(lib, "index.yaml"), "w") as fh:
        fh.write("- {id: two, kind: video, file: videos/two.mp4, sha256: x, bytes: 1, source: t, licence: t, "
                 "duration_s: 2, width: 320, height: 240}\n")
    p = PJ.Projection({"VIDEO": [ev(1.0, 3.0, do="play", video="two")]}, lib, 64, 36, 15)
    p.wait()
    k1, _ = p.picture(1.5)
    k2, _ = p.picture(2.5)
    kb, img = p.picture(0.5)
    check("a show's projection: frames by show time, black outside its video",
          k1 == ("two", 7) and k2 == ("two", 22) and kb == ("black",) and not img.any(), (k1, k2, kb))
    q = PJ.Projection({"VIDEO": [ev(1.0, 3.0, do="play", video="missing")]}, lib, 64, 36, 15)
    q.wait()
    check("a video not in the library: black, and a note says so",
          q.picture(1.5)[0][0] == "black" and any("missing" in n for n in q.notes), q.notes)

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
