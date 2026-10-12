#!/usr/bin/env python3
"""The web face twin's frame file, read by the body twin's own reader (software/choreo/face_frames.py).

Plain asserts, no pytest: CI's face-web job runs it with a bare python3 (conformance.sh).
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "choreo"))

from frame_tap import FrameFile, W, H, HEADER  # noqa: E402
from face_frames import FaceFrames  # noqa: E402

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "frames.bin")
    writer = FrameFile(path)
    reader = FaceFrames(path, W, H)
    check("a fresh file has no frame yet", reader.poll() is None)

    a = bytes([10, 20, 30]) * (W * H)
    check("a whole frame is written", writer.write(a))
    check("the reader gets it", reader.poll() == a)
    check("and not twice", reader.poll() is None)

    b = bytes([200, 100, 50]) * (W * H)
    ino = os.stat(path).st_ino
    writer.write(b)
    check("the next frame arrives", reader.poll() == b)
    check("written in place, so the reader's map stays valid", os.stat(path).st_ino == ino)

    check("a frame of the wrong size is refused", not writer.write(b"\0" * 10))
    check("and the reader sees nothing new", reader.poll() is None)

    # A writer stopped mid-frame leaves the sequence odd: the reader must not take the pixels.
    seq = struct.unpack_from("<I", writer.mm, 20)[0]
    struct.pack_into("<I", writer.mm, 20, seq + 1)
    writer.mm[HEADER:HEADER + 3] = b"\xff\xff\xff"
    check("a half-written frame is never shown", reader.poll() is None)
    writer.close()

    again = FrameFile(path)
    check("a new writer starts on an even sequence", again.seq % 2 == 0)
    again.write(a)
    check("and its frames read normally", reader.poll() == a)
    again.close()

print(f"\n{ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
