"""Reads the face twin's shared frames, for the MuJoCo body twin's head (plan 2.5).

The desktop face twin, started with OPENDUBS_FACE_FRAMES=<file>, writes its finished frame into
that file about 15 times a second (`firmware/tab5_idf/app_face/frame_tap.h` has the layout): a
32-byte header — "ODFF", version, width, height, stride, a sequence number that is ODD while a frame
is being written — then width x height RGB rows, top to bottom. This copies a frame only when the
sequence is even and did not change while it was copied, so a half-written frame is never shown.

No MuJoCo here, so it is tested on its own. Nothing here talks to the robot.
"""
from __future__ import annotations

import mmap
import os
import struct
import tempfile

HEADER = 32
MAGIC = b"ODFF"


def default_path() -> str:
    """Where face.sh tells the face twin to write, and where the body twin looks."""
    return os.environ.get("OPENDUBS_FACE_FRAMES") or os.path.join(tempfile.gettempdir(),
                                                                  "opendubs-face-frames.bin")


class FaceFrames:
    def __init__(self, path: str, width: int, height: int):
        self.path, self.w, self.h = path, width, height
        self.mm, self.ino, self.seq, self.note = None, None, None, None

    def _open(self):
        try:
            st = os.stat(self.path)
            if st.st_size < HEADER + self.w * self.h * 3:
                return False
            with open(self.path, "rb") as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except (OSError, ValueError):
            return False
        magic, ver, w, h, stride = struct.unpack_from("<4s4I", mm, 0)
        if magic != MAGIC or ver != 1 or (w, h, stride) != (self.w, self.h, self.w * 3):
            self.note = f"{self.path}: not a {self.w}x{self.h} face frame file"
            mm.close()
            return False
        self.mm, self.ino, self.seq = mm, st.st_ino, None
        return True

    def poll(self) -> bytes | None:
        """The newest whole frame, if there is one we have not returned yet; else None."""
        if self.mm is None and not self._open():
            return None
        try:
            if os.stat(self.path).st_ino != self.ino:      # the file was replaced: map the new one
                self.mm.close()
                self.mm = None
                return self.poll() if self._open() else None
        except OSError:
            return None
        s1 = struct.unpack_from("<I", self.mm, 20)[0]
        if s1 == self.seq or s1 & 1:
            return None
        px = self.mm[HEADER:HEADER + self.w * self.h * 3]
        if struct.unpack_from("<I", self.mm, 20)[0] != s1:
            return None                                   # torn: try again next time
        self.seq = s1
        return px
