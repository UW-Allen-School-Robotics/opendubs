"""The show's projector video, for the twin's projection screen (notes/design/choreo-video.md).

The picture at a show time is chosen by the SHOW'S OWN CLOCK — the one that poses the twin and drives
the face — from the show's VIDEO track as the export resolves it: the video that plays then, `from_s`
into its file plus the time since its cue, black or its last frame after it ends, black before. No
projector latency is modelled: the twin shows the show's intended timing (the real projector's delay
is the per-robot video lead, set by ear at the robot).

Each library video is decoded ONCE, at the twin's size and rate (sim/scene.py PROJ_TEX_W/H, PROJ_FPS),
into a cache of JPEG frames — a 3-minute video is tens of MB this way, where raw frames would be
gigabytes — beside the user's other caches (~/.cache/opendubs/projection). Decoding needs ffmpeg (on
PATH, or imageio-ffmpeg); without it the screen stays black and says why. No MuJoCo here, so the
timing is tested on its own.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import threading

import numpy as np

CACHE = os.environ.get("OPENDUBS_PROJECTION_CACHE") or os.path.join(
    os.path.expanduser("~"), ".cache", "opendubs", "projection")
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def ffmpeg_exe() -> str | None:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def at(tracks: dict, t: float):
    """(video id, seconds into its file) to show at show time `t`, or (video id, None) for its last
    frame (`end: hold`), or None for black. `tracks`: the export's tracks (VIDEO events carry start,
    end and the cue)."""
    evs = [e for e in (tracks or {}).get("VIDEO") or [] if (e.get("cue") or {}).get("do") == "play"]
    stops = sorted(e["t"] for e in (tracks or {}).get("VIDEO") or [] if (e.get("cue") or {}).get("do") == "stop")
    cur = None
    for e in sorted(evs, key=lambda x: x["t"]):
        if e["t"] <= t:
            cur = e
    if cur is None:
        return None
    c = cur["cue"]
    later = [s for s in stops if s > cur["t"]] + [e["t"] for e in evs if e["t"] > cur["t"]]
    end = min([cur["end"] if cur.get("end", cur["t"]) > cur["t"] else float("inf")] + later)
    if t < end:
        return c["video"], float(c.get("from_s") or 0.0) + (t - cur["t"])
    if c.get("end", "black") == "hold" and not any(s <= t for s in later):
        return c["video"], None
    return None


class Frames:
    """One library video's decoded frames: JPEG bytes in one file and their offsets, read by index."""

    def __init__(self, path: str, w: int, h: int, fps: float):
        self.path, self.w, self.h, self.fps = path, w, h, fps
        self.blob, self.offs, self.error = None, None, None

    def key(self) -> str:
        st = os.stat(self.path)
        return hashlib.sha1(f"{os.path.abspath(self.path)}:{st.st_size}:{st.st_mtime_ns}:"
                            f"{self.w}x{self.h}@{self.fps}".encode()).hexdigest()[:16]

    def load(self) -> bool:
        """Decode (once; cached) and map the frames. False, with `error`, when it cannot."""
        try:
            k = self.key()
        except OSError as e:
            self.error = f"{os.path.basename(self.path)}: {e.strerror or e}"
            return False
        jpg, idx = os.path.join(CACHE, k + ".mjpg"), os.path.join(CACHE, k + ".idx.npy")
        if not (os.path.isfile(jpg) and os.path.isfile(idx)):
            ff = ffmpeg_exe()
            if not ff:
                self.error = "no ffmpeg to decode the video for the twin (install ffmpeg, or imageio-ffmpeg)"
                return False
            os.makedirs(CACHE, exist_ok=True)
            vf = (f"fps={self.fps},scale={self.w}:{self.h}:force_original_aspect_ratio=decrease,"
                  f"pad={self.w}:{self.h}:(ow-iw)/2:(oh-ih)/2:color=black")
            r = subprocess.run([ff, "-v", "error", "-i", self.path, "-an", "-vf", vf, "-q:v", "4",
                                "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True)
            if r.returncode != 0 or not r.stdout:
                self.error = f"ffmpeg could not decode {os.path.basename(self.path)}"
                return False
            data, offs, i = r.stdout, [], 0
            while True:                              # split the stream at each frame's start/end markers
                a = data.find(SOI, i)
                if a < 0:
                    break
                b = data.find(EOI, a + 2)
                if b < 0:
                    break
                offs.append((a, b + 2))
                i = b + 2
            tmp = jpg + ".part"
            with open(tmp, "wb") as fh:
                fh.write(data)
            np.save(idx, np.asarray(offs, dtype=np.int64))
            os.replace(tmp, jpg)
        with open(jpg, "rb") as fh:
            self.blob = fh.read()
        self.offs = np.load(idx)
        return len(self.offs) > 0

    def __len__(self):
        return 0 if self.offs is None else len(self.offs)

    def index(self, secs) -> int:
        """The frame for `secs` into the file (None: the last frame), clamped to the video."""
        n = len(self)
        return n - 1 if secs is None else max(0, min(n - 1, int(secs * self.fps)))

    def rgb(self, i: int) -> np.ndarray:
        from PIL import Image
        a, b = self.offs[i]
        return np.asarray(Image.open(io.BytesIO(self.blob[a:b])).convert("RGB"))


class Projection:
    """The twin's screen for one show: the picture at a show time, with the videos decoded in the
    background (so a viewer never stalls). `picture(t)` returns (key, rgb) — key changes only when
    the picture does — and None for nothing to draw yet."""

    def __init__(self, tracks: dict, library_root: str, w: int, h: int, fps: float, log=None):
        self.tracks, self.w, self.h, self.fps = tracks, w, h, fps
        self.log = log or (lambda msg: None)
        self.black = np.zeros((h, w, 3), dtype=np.uint8)
        self.frames, self.notes, self.lock = {}, [], threading.Lock()
        ids = []
        for e in (tracks or {}).get("VIDEO") or []:
            v = (e.get("cue") or {}).get("video")
            if v and v not in ids:
                ids.append(v)
        self.paths = {}
        try:
            import library
            idx = library.load_index(library_root) or {}
        except Exception as err:  # noqa: BLE001
            idx = {}
            self.notes.append(f"the show library can't be read: {err}")
        for v in ids:
            ent = idx.get(v)
            if ent and ent.get("kind") == "video" and os.path.isfile(os.path.join(library_root, ent.get("file", ""))):
                self.paths[v] = os.path.join(library_root, ent["file"])
            else:
                self.notes.append(f"video {v} is not in the library here ({library_root}): the screen stays black")
        self._thread = threading.Thread(target=self._decode, daemon=True, name="projection-decode")
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        """Until every video is decoded (or could not be): an offline render waits; a viewer never does."""
        self._thread.join(timeout)

    @property
    def wanted(self) -> bool:
        return bool((self.tracks or {}).get("VIDEO"))

    def _decode(self):
        for v, p in self.paths.items():
            fr = Frames(p, self.w, self.h, self.fps)
            if fr.load():
                with self.lock:
                    self.frames[v] = fr
            else:
                self.notes.append(f"video {v}: {fr.error}")
                self.log(f"projection: {fr.error}")

    def picture(self, t: float):
        want = at(self.tracks, t)
        if want is None:
            return ("black",), self.black
        vid, secs = want
        with self.lock:
            fr = self.frames.get(vid)
        if fr is None:
            return ("black", vid), self.black          # still decoding, or it could not be
        i = fr.index(secs)
        return (vid, i), fr.rgb(i)
