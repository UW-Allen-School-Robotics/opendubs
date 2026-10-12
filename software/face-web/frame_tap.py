"""The web face twin's picture, shared with the MuJoCo body twin (choreography plan 2.5).

The same file, in the same layout, as the Tab5 desktop face writes (firmware/tab5_idf/app_face/
frame_tap.h), so the body twin's reader (software/choreo/face_frames.py) cannot tell which face
drew it:

    offset 0   "ODFF"   4 u32 version (1)   8 u32 width   12 u32 height   16 u32 row stride
          20   u32 sequence: ODD while a frame is being written, EVEN when it is whole
          24   u32[2] reserved (0)
          32   width x height x 3 bytes, RGB, rows top to bottom

door_dev.py writes it when OPENDUBS_FACE_FRAMES names a file; the page sends the pixels. Written in
place, never replaced, so a reader's memory map stays valid (the reader re-maps if the file's inode
changes, which happens only when it is created at a different size).
"""
from __future__ import annotations

import mmap
import os
import struct

W, H = 320, 180            # the body twin's face texture (software/choreo scene.FACE_TEX_W/H)
HEADER = 32


class FrameFile:
    def __init__(self, path: str, width: int = W, height: int = H):
        self.w, self.h, self.size = width, height, width * height * 3
        total = HEADER + self.size
        # Recreate only if it is the wrong size: rewriting in place keeps a reader's map valid.
        if not os.path.exists(path) or os.path.getsize(path) != total:
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                # Sequence 1, odd: "no whole frame yet", so a reader shows nothing until the first.
                f.write(struct.pack("<4s7I", b"ODFF", 1, width, height, width * 3, 1, 0, 0))
                f.write(bytes(self.size))
            os.replace(tmp, path)
        self.f = open(path, "r+b")
        self.mm = mmap.mmap(self.f.fileno(), total)
        magic, ver, w, h, stride, seq = struct.unpack_from("<4s5I", self.mm, 0)
        if (magic, ver, w, h, stride) != (b"ODFF", 1, width, height, width * 3):
            struct.pack_into("<4s7I", self.mm, 0, b"ODFF", 1, width, height, width * 3, 1, 0, 0)
            seq = 1
        self.seq = seq + (seq & 1)          # a writer that died mid-frame left it odd: start even

    def write(self, rgb: bytes) -> bool:
        """One whole frame, `width x height x 3` bytes. Returns False (and writes nothing) if not."""
        if len(rgb) != self.size:
            return False
        struct.pack_into("<I", self.mm, 20, (self.seq + 1) & 0xFFFFFFFF)     # odd: writing
        self.mm[HEADER:HEADER + self.size] = rgb
        self.seq = (self.seq + 2) & 0xFFFFFFFF
        struct.pack_into("<I", self.mm, 20, self.seq)                        # even: whole
        return True

    def close(self):
        self.mm.close()
        self.f.close()
