#!/usr/bin/env python3
"""Render a show to a video file, offline: the robot twin, and (soon) the face, with the show's audio.

    python3 render.py <show> --out show.mp4                     # layout a: the robot, 1920x1080, 30 fps
    python3 render.py <show> --out clip.mp4 --from 60 --to 75   # a span, in show seconds
    opendubs-studio render <show> --out show.mp4                # the same, from the studio

THE ROBOT is the same twin the composer drives and the sweep checks: sim_preview's sample of the
show at each frame's time (arms on the path the robot is sent, lift at the measured speed, head at its
slew rate, grippers, the base), rendered off-screen at the requested size.

THE AUDIO is mixed offline and never played: the song from show time 0 (the device starts it there;
the grid's offset_s only says where beat 1 falls), the tablet's sound files at their cues, and the
show's DEVICE volume (its level at time 0, and each cue's level with its fade) as a gain on the mix,
as the head's media volume would. The Tab5's earcons have no audio files and are left out, said so.

ENCODING: ffmpeg from the imageio-ffmpeg package (a wheel for every platform — no system ffmpeg),
raw RGB frames piped straight in: H.264 (yuv420p, CRF 18) and AAC. No frame files are written.

LAYOUTS: a = the robot (this version). b (the robot with a face inset) and c (side by side) need the
face page's render mode, which the Tablet and Face & HRI lanes are adding.

Nothing here talks to a robot. An invalid show is refused unless --allow-invalid, which stamps
INVALID on every frame.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import cue  # noqa: E402
import library  # noqa: E402
import runner  # noqa: E402
import sim_preview as P  # noqa: E402

RATE = 48000                     # the audio mix's sample rate
CAMERA = {"azimuth": 128.0, "elevation": -10.0, "distance": 3.0, "lookat": (0.35, 0.0, 0.85)}   # as the twin


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ---- audio -----------------------------------------------------------------------------------------
def decode(path: str) -> np.ndarray:
    """Any audio file -> float32 stereo at RATE, (n, 2), through the bundled ffmpeg."""
    r = subprocess.run([ffmpeg(), "-v", "error", "-i", path, "-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2)


def media_file(url: str, show_path: str) -> str | None:
    """runner.audio_items' dashboard URL -> the file on this machine."""
    pre = "/api/choreo/media/"
    rel = url[len(pre):] if url.startswith(pre) else url
    if rel.startswith("library/"):
        p = os.path.join(library.root(), rel[len("library/"):])
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(show_path)), rel)
    return p if os.path.isfile(p) else None


def volume_at(tl: dict, t: np.ndarray) -> np.ndarray:
    """The head's media volume through the show: show-level `device.volume` at 0, then each DEVICE
    volume cue, faded over its fade_s; 1.0 where the show sets none (the head's own level)."""
    level = np.ones_like(t)
    dev = tl.get("device") or {}
    if dev.get("volume") is not None:
        level[:] = float(dev["volume"])
    prev = level[0] if len(level) else 1.0
    for e in sorted(tl.get("tracks", {}).get("DEVICE", []), key=lambda e: e["t"]):
        c = e["cue"]
        if c.get("do") != "volume":
            continue
        start, fade, to = float(e["t"]), float(c.get("fade_s") or 0.0), float(c["level"])
        ramp = np.clip((t - start) / fade, 0.0, 1.0) if fade > 0 else (t >= start).astype(float)
        cur = np.where(t >= start, prev + (to - prev) * ramp, level)
        level, prev = cur, to
    return level


def mix(show, tl: dict, t0: float, t1: float, notes: list) -> np.ndarray:
    n = int(round((t1 - t0) * RATE))
    out = np.zeros((n, 2), dtype=np.float32)
    items, skipped = runner.audio_items(show, tl)
    notes += [f"audio: {s}" for s in skipped]
    for it in items:
        f = media_file(it["url"], show.path)
        if not f:
            notes.append(f"audio: {it['url']} is not on this machine — left out")
            continue
        pcm = decode(f)
        a = int(round((float(it["at"]) - t0) * RATE))        # where it starts in the clip
        src0, dst0 = max(0, -a), max(0, a)
        k = min(len(pcm) - src0, n - dst0)
        if k > 0:
            out[dst0:dst0 + k] += pcm[src0:src0 + k]
    t = t0 + np.arange(n) / RATE
    out *= volume_at(tl, t)[:, None].astype(np.float32)
    return np.clip(out, -1.0, 1.0)


# ---- the robot --------------------------------------------------------------------------------------
class Robot:
    def __init__(self, width: int, height: int, assets=None):
        sys.path.insert(0, os.path.join(REPO, "sim"))
        import mujoco
        import scene
        self.mj, self.clearance = mujoco, __import__("clearance")
        self.model = scene.build(assets, face_screen=True)
        self.model.vis.global_.offwidth = max(self.model.vis.global_.offwidth, width)
        self.model.vis.global_.offheight = max(self.model.vis.global_.offheight, height)
        self.model.vis.headlight.ambient[:] = [0.55] * 3     # the lighting of sim/scene.py --render
        self.model.vis.headlight.diffuse[:] = [0.8] * 3
        self.data = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, height, width)
        self.cam = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(self.cam)
        self.cam.azimuth, self.cam.elevation = CAMERA["azimuth"], CAMERA["elevation"]
        wide = width / height > 1.2                 # a wide frame: closer, and centred higher on the body
        self.cam.distance = CAMERA["distance"] * (0.8 if wide else 1.0)
        self.cam.lookat[:] = (CAMERA["lookat"][0], CAMERA["lookat"][1], CAMERA["lookat"][2] + (0.06 if wide else 0.0))
        self.face_tex = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_TEXTURE, scene.FACE_TEXTURE)
        self.face_wh = (scene.FACE_TEX_W, scene.FACE_TEX_H)

    def frame(self, s: dict, face_rgb: np.ndarray | None = None) -> np.ndarray:
        """One RGB frame (h, w, 3) of the robot in sample `s`; `face_rgb` (FACE_TEX_H, FACE_TEX_W, 3)
        goes on the head's screen, as the live twin shows the face twin's picture."""
        m, d = self.model, self.data
        self.clearance.set_pose(m, d, P.joints_arg(s), s["lift_mm"], s["head_pan"] - P.HEAD_CENTRE_DEG,
                                s["head_tilt"] - P.HEAD_CENTRE_DEG)
        P.apply_base(m, d, s["base"])
        P.apply_grips(m, d, s)
        if face_rgb is not None and self.face_tex >= 0:
            adr, n = m.tex_adr[self.face_tex], self.face_wh[0] * self.face_wh[1] * 3
            m.tex_data[adr:adr + n] = face_rgb.reshape(-1)
            self.mj.mjr_uploadTexture(m, self.renderer._mjr_context, self.face_tex)
        self.renderer.update_scene(d, camera=self.cam)
        return self.renderer.render()


def stamp_invalid(img: np.ndarray) -> np.ndarray:
    from PIL import Image, ImageDraw
    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im)
    dr.rectangle([0, 0, im.width, 44], fill=(150, 20, 20))
    dr.text((16, 12), "INVALID SHOW — preview only: the robot would refuse it", fill=(255, 255, 255))
    return np.asarray(im)


# ---- the whole -----------------------------------------------------------------------------------------
def render(show_path: str, out: str, layout="a", fps=30.0, size=(1920, 1080), limits=None, t_from=0.0,
           t_to=None, allow_invalid=False, hashes=None, log=print) -> dict:
    if layout != "a":
        raise SystemExit(f"layout {layout!r} needs the face track (the face page's render mode, coming next): "
                         "use --layout a for now")
    show = cue.load_show(show_path)
    lim = limits or cue.DEFAULT_LIMITS
    if not limits and not os.path.isfile(lim):         # a laptop: the composer's TEST fixture, said so
        lim = os.path.join(HERE, "tests", "joint_limits_fixture.yaml")
        log("render: arm limits: the TEST fixture (no robot's file here; `opendubs-studio limits --from-robot`)")
    tl = show.export(cue.load_limits(lim), lim)
    if not tl["valid"] and not allow_invalid:
        for p in tl["problems"][:8]:
            log(f"FAIL: {p['msg']}")
        raise SystemExit("refusing to render an invalid show (pass --allow-invalid to render it, stamped INVALID)")
    t_end = tl["duration_s"] if t_to is None else min(float(t_to), tl["duration_s"])
    t_from = max(0.0, float(t_from))
    w, h = size
    n = int(round((t_end - t_from) * fps))
    notes = []
    with tempfile.TemporaryDirectory(prefix="opendubs-render-") as td:
        wav = os.path.join(td, "mix.f32")
        mix(show, tl, t_from, t_from + n / fps, notes).tofile(wav)
        enc = subprocess.Popen(
            [ffmpeg(), "-v", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-",
             "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", wav,
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", f"{n / fps:.3f}",
             "-movflags", "+faststart", out],
            stdin=subprocess.PIPE)
        robot = Robot(w, h)
        digest = []
        try:
            for i in range(n):
                t = t_from + i / fps
                img = robot.frame(P.sample(tl, t))
                if not tl["valid"]:
                    img = stamp_invalid(img)
                buf = np.ascontiguousarray(img, dtype=np.uint8).tobytes()
                if hashes is not None:
                    digest.append(hashlib.sha256(buf).hexdigest())
                enc.stdin.write(buf)
                if i % int(fps * 10) == 0:
                    log(f"render: {t:7.1f} s of {t_end:.1f} s")
        finally:
            enc.stdin.close()
            code = enc.wait()
        if code:
            raise SystemExit(f"ffmpeg failed (exit {code})")
    if hashes is not None:
        hashes.extend(digest)
    for m in notes:
        log(m)
    log(f"render: wrote {out} — {n} frames, {n / fps:.1f} s, {w}x{h} at {fps:g} fps, layout {layout}")
    return {"frames": n, "seconds": n / fps, "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("show")
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", choices=("a", "b", "c"), default="a",
                    help="a: the robot; b: the robot with a face inset; c: side by side (b and c: coming)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--size", default="1920x1080", help="WIDTHxHEIGHT, even numbers")
    ap.add_argument("--limits", default=None, help="the arm limits (default: as the composer: the TEST fixture "
                                                   "unless the robot's file is here)")
    ap.add_argument("--from", dest="t_from", type=float, default=0.0, help="start, show seconds")
    ap.add_argument("--to", dest="t_to", type=float, default=None, help="end, show seconds")
    ap.add_argument("--allow-invalid", action="store_true")
    a = ap.parse_args(argv)
    w, h = (int(x) for x in a.size.lower().split("x"))
    if w % 2 or h % 2:
        raise SystemExit("--size: width and height must be even (H.264)")
    render(a.show, a.out, a.layout, a.fps, (w, h), a.limits, a.t_from, a.t_to, a.allow_invalid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
