#!/usr/bin/env python3
"""Checks on the reader for the face twin's shared frames (plan 2.5). Writes the file layout that
firmware/tab5_idf/app_face/frame_tap.h publishes; no face twin and no MuJoCo needed.

    python3 tests/test_face_frames.py
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import face_frames as ff  # noqa: E402

W, H = 8, 4
fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


def write(path, seq, fill, w=W, h=H, magic=b"ODFF"):
    with open(path, "wb") as f:
        f.write(struct.pack("<4s5I2I", magic, 1, w, h, w * 3, seq, 0, 0))
        f.write(bytes([fill]) * (w * h * 3))


def set_seq(path, seq, fill=None):
    with open(path, "r+b") as f:
        f.seek(20)
        f.write(struct.pack("<I", seq))
        if fill is not None:
            f.seek(32)
            f.write(bytes([fill]) * (W * H * 3))


print("face frames: reading the face twin's shared picture")
d = tempfile.mkdtemp()
p = os.path.join(d, "frames.bin")
r = ff.FaceFrames(p, W, H)
check("no file yet: no frame, no error", r.poll() is None)
write(p, 2, 7)
px = r.poll()
check("a whole frame is returned once", px is not None and len(px) == W * H * 3 and px[0] == 7)
check("... and not again until the face twin writes another", r.poll() is None)
set_seq(p, 3, 9)
check("a frame being written (odd sequence) is never returned", r.poll() is None)
set_seq(p, 4)
px = r.poll()
check("once it is whole, the new frame is returned", px is not None and px[0] == 9)
write(p, 0, 5)                     # the face twin restarted: same file, sequence back to 0
px = r.poll()
check("a restarted face twin (sequence reset) is followed", px is not None and px[0] == 5)
os.unlink(p)
write(p, 2, 11)                    # replaced by a new file
px = r.poll()
check("a replaced file is re-mapped", px is not None and px[0] == 11)
q = os.path.join(d, "wrong.bin")
write(q, 2, 1, w=16, h=9)
w = ff.FaceFrames(q, W, H)
check("a frame file of another size is refused, with a reason", w.poll() is None and w.note and "not a" in w.note,
      w.note)
os.environ["OPENDUBS_FACE_FRAMES"] = "/x/y.bin"
check("face.sh's path is the default", ff.default_path() == "/x/y.bin")

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
