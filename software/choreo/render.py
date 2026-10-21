#!/usr/bin/env python3
"""Render a show to a video file, offline: the robot twin, and (soon) the face, with the show's audio.

    python3 render.py <show> --out show.mp4                     # 1280x720, 30 fps
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

LAYOUTS: c = the robot and the face side by side (the default), b = the robot with a face inset,
a = the robot alone. In every layout the face is also on the twin's head screen, as in the live twin.

THE FACE is the real face page in its render mode (?render=1, the Tablet lane), with the engine's
seeded randomness (Face & HRI), headless and muted, stepped on show time frame by frame and fed exactly
the lines the live face bridge sends (face_bridge.Follower). The same --seed gives the same video. A
library VIDEO skin follows the wall clock, so a show that uses one renders, but not repeatably, and
says so. With no browser the face is left out and the robot renders alone, said so.

FRAMING: the camera keeps the twin's angle but is aimed and placed from the show itself — the box
around everything visible over every sampled moment of the span (arms overhead, the lift at its
highest, the base where it moves), with a margin — so nothing is ever cut off and little is wasted.

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
        self.model = scene.build(assets, face_screen=True, projection=True)
        self.scene = scene
        self.model.vis.global_.offwidth = max(self.model.vis.global_.offwidth, width)
        self.model.vis.global_.offheight = max(self.model.vis.global_.offheight, height)
        self.model.vis.headlight.ambient[:] = [0.55] * 3     # the lighting of sim/scene.py --render
        self.model.vis.headlight.diffuse[:] = [0.8] * 3
        self.data = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, height, width)
        self.size = (width, height)
        self.cam = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(self.cam)
        self.cam.azimuth, self.cam.elevation = CAMERA["azimuth"], CAMERA["elevation"]
        self.cam.distance, self.cam.lookat[:] = CAMERA["distance"], CAMERA["lookat"]
        m = self.model
        self.visible = [i for i in range(m.ngeom) if m.geom_type[i] != mujoco.mjtGeom.mjGEOM_PLANE
                        and m.geom_group[i] < 3 and m.geom_rgba[i][3] > 0]
        self.face_tex = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_TEXTURE, scene.FACE_TEXTURE)
        self.face_wh = (scene.FACE_TEX_W, scene.FACE_TEX_H)
        # the projection screen behind the robot (hidden until a show with video): its texture, and
        # which picture is on it, so a picture is uploaded only when it changes
        self.proj_tex = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_TEXTURE, scene.PROJ_TEXTURE)
        self.proj_key = None

    def pose(self, s: dict) -> None:
        m, d = self.model, self.data
        self.clearance.set_pose(m, d, P.joints_arg(s), s["lift_mm"], s["head_pan"] - P.HEAD_CENTRE_DEG,
                                s["head_tilt"] - P.HEAD_CENTRE_DEG)
        P.apply_base(m, d, s["base"])
        P.apply_grips(m, d, s)

    def axes(self) -> tuple:
        """The camera's forward, right and up, at the twin's angle."""
        az, el = np.radians(self.cam.azimuth), np.radians(self.cam.elevation)
        fwd = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        right = np.cross(fwd, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        return fwd, right, np.cross(right, fwd)

    def outline(self) -> np.ndarray:
        """The robot's outline as seen from the camera's direction, posed now: the visible parts' box
        corners (each geom's own box, turned) that are furthest left, right, up and down in view."""
        m, d = self.model, self.data
        signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
        p = np.concatenate([d.geom_xpos[i] + (m.geom_aabb[i][:3] + signs * m.geom_aabb[i][3:])
                            @ d.geom_xmat[i].reshape(3, 3).T for i in self.visible])
        _, right, up = self.axes()
        u, v = p @ right, p @ up
        return p[[np.argmin(u), np.argmax(u), np.argmin(v), np.argmax(v)]]

    def fit(self, points, margin=0.05) -> None:
        """Aim and place the camera (the twin's angle) so every point is in frame, `margin` of the
        frame kept clear on every side. `points`: the outlines over the whole span."""
        fwd, right, up = self.axes()
        corners = np.asarray(points, float)
        w, h = self.size
        tv = np.tan(np.radians(self.model.vis.global_.fovy) / 2) * (1 - 2 * margin)
        th = tv * w / h
        look = corners.mean(axis=0)
        dist = 1.0
        for _ in range(6):                          # centre on the projected box, then just fit it
            rel = corners - look
            x, y, z = rel @ right, rel @ up, rel @ fwd
            dist = max(np.max(np.abs(x) / th - z), np.max(np.abs(y) / tv - z), 0.5)
            u, v = x / (dist + z), y / (dist + z)
            look = look + right * (u.max() + u.min()) / 2 * dist + up * (v.max() + v.min()) / 2 * dist
        self.cam.lookat[:], self.cam.distance = look, dist

    def frame(self, s: dict, face_rgb: np.ndarray | None = None, proj=None) -> np.ndarray:
        """One RGB frame (h, w, 3) of the robot in sample `s`; `face_rgb` (FACE_TEX_H, FACE_TEX_W, 3)
        goes on the head's screen, as the live twin shows the face twin's picture; `proj` (key, rgb)
        on the projection screen behind the robot."""
        m, d = self.model, self.data
        self.pose(s)
        if proj is not None and self.proj_tex >= 0 and proj[0] != self.proj_key:
            adr, n = m.tex_adr[self.proj_tex], self.scene.PROJ_TEX_W * self.scene.PROJ_TEX_H * 3
            m.tex_data[adr:adr + n] = proj[1].reshape(-1)
            self.mj.mjr_uploadTexture(m, self.renderer._mjr_context, self.proj_tex)
            self.proj_key = proj[0]
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


# ---- the face ----------------------------------------------------------------------------------------
FACE_WEB = os.path.join(REPO, "software", "face-web")
FACE_LEAD_S = 1.0                # the face is stepped from this long before the first kept frame (Tablet: the
                                 # eyes open from t=0, so a span starting later gets its own lead-in)


class PageFace:
    """The real face, drawn by the face page in its render mode (?render=1, Tablet #277; the engine's seeded
    randomness, Face & HRI #276): headless and muted, on its own port and profile, stepped frame by frame on
    show time. Gets exactly the lines the live face bridge would send (face_bridge.Follower)."""

    def __init__(self, size, seed=1, library_dir=None, log=print):
        import functools
        import http.server
        import threading
        from playwright.sync_api import sync_playwright

        lib = library_dir or library.root()

        class Serve(http.server.SimpleHTTPRequestHandler):
            def translate_path(self, path):
                p = path.split("?", 1)[0]
                if p.startswith("/library/"):
                    return os.path.join(lib, p[len("/library/"):])
                return super().translate_path(path)

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Serve, directory=FACE_WEB))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.pw = sync_playwright().start()
        args = ["--mute-audio", "--autoplay-policy=no-user-gesture-required"]
        try:
            self.browser = self.pw.chromium.launch(channel="chrome", args=args)      # an installed Chrome
        except Exception:  # noqa: BLE001  none: Playwright's own Chromium (`playwright install chromium`)
            self.browser = self.pw.chromium.launch(args=args)
        self.page = self.browser.new_page()
        w, h = size
        port = self.srv.server_address[1]
        self.page.goto(f"http://127.0.0.1:{port}/index.html?render=1&seed={int(seed)}&w={w}&h={h}&library=/library/")
        self.page.wait_for_function("() => window.OpenDubsRender && window.OpenDubsRender.ready", timeout=30000)
        self.info = self.page.evaluate("window.OpenDubsRender.ready")
        self.size = (int(self.info["w"]), int(self.info["h"]))

    def frame(self, lines, t_s) -> tuple:
        """Apply the lines due by t (show seconds), step, and return (RGBA (h, w, 4), the page's replies)."""
        import base64
        f, replies = self.page.evaluate(
            """async ([lines, t]) => { const R = window.OpenDubsRender, out = [];
                 for (const l of lines) out.push(await R.apply(l, t));
                 R.step(t); return [R.frame(), out]; }""", [list(lines), round(t_s * 1000.0, 3)])
        px = np.frombuffer(base64.b64decode(f["rgba"]), dtype=np.uint8).reshape(int(f["h"]), int(f["w"]), 4)
        return px, replies

    def close(self):
        for stop in (lambda: self.browser.close(), lambda: self.pw.stop(), self.srv.shutdown):
            try:
                stop()
            except Exception:  # noqa: BLE001  closing: nothing left to save
                pass


def video_skins(tl: dict) -> list:
    """Library skins the show uses that are VIDEOS: their frames follow the wall clock, so a render that
    shows them is not repeatable (Face & HRI's decision: no per-frame seek)."""
    names = {e["cue"]["name"] for e in tl.get("tracks", {}).get("FACE", []) if e["cue"].get("do") == "skin"}
    try:
        idx = library.load_index() or {}
    except Exception:  # noqa: BLE001
        return []
    return sorted(n for n in names if (idx.get(n) or {}).get("kind") == "skin" and (idx.get(n) or {}).get("type") == "video")


# ---- layouts ----------------------------------------------------------------------------------------------
def even(x) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def plan(layout: str, w: int, h: int) -> dict:
    """Where each part goes. c: the robot in a tall pane on the left, the face to its right; b: the robot
    full frame, the face inset bottom-right at 30% width; a: the robot alone (the face only on its head)."""
    if layout == "c":
        rw = even(h * 0.8)
        fw = w - rw
        fh = even(fw * 9 / 16)
        return {"robot": (rw, h, 0, 0), "face": (fw, fh, rw, (h - fh) // 2)}
    if layout == "b":
        fw = even(w * 0.30)
        fh = even(fw * 9 / 16)
        m = even(w * 0.025)
        return {"robot": (w, h, 0, 0), "face": (fw, fh, w - fw - m, h - fh - m), "border": 2}
    return {"robot": (w, h, 0, 0), "face": None}


def compose(lay: dict, w: int, h: int, robot_img: np.ndarray, face_rgb: np.ndarray | None) -> np.ndarray:
    out = np.zeros((h, w, 3), dtype=np.uint8)
    rw, rh, rx, ry = lay["robot"]
    out[ry:ry + rh, rx:rx + rw] = robot_img
    if lay["face"] is not None and face_rgb is not None:
        fw, fh, fx, fy = lay["face"]
        b = lay.get("border", 0)
        if b:
            out[fy - b:fy + fh + b, fx - b:fx + fw + b] = (90, 96, 110)
        out[fy:fy + fh, fx:fx + fw] = face_rgb
    return out


def to_rgb(rgba: np.ndarray, size: tuple) -> np.ndarray:
    """RGBA from the page -> RGB at `size` (w, h), on black."""
    from PIL import Image
    rgb = (rgba[..., :3].astype(np.float32) * (rgba[..., 3:4].astype(np.float32) / 255.0)).astype(np.uint8)
    if (rgb.shape[1], rgb.shape[0]) != tuple(size):
        rgb = np.asarray(Image.fromarray(rgb).resize(size, Image.BILINEAR))
    return rgb


# ---- the whole -----------------------------------------------------------------------------------------
def render(show_path: str, out: str, layout="c", fps=30.0, size=(1280, 720), limits=None, t_from=0.0,
           t_to=None, allow_invalid=False, hashes=None, log=print, seed=1, face=None) -> dict:
    """`face`: a face source with frame(lines, t_s) -> (RGBA, replies) and close() — PageFace by default;
    tests pass a stand-in. layout a still draws the face on the robot's head when a face source runs."""
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
    for v in video_skins(tl):
        log(f"render: WARNING the show uses the video skin {v!r}: its frames follow the wall clock, so this "
            "render is not repeatable frame for frame (image and code skins are)")
    t_end = tl["duration_s"] if t_to is None else min(float(t_to), tl["duration_s"])
    t_from = max(0.0, float(t_from))
    w, h = size
    lay = plan(layout, w, h)
    n = int(round((t_end - t_from) * fps))
    notes = []
    own_face = face is None
    if own_face:
        fsize = lay["face"][:2] if lay["face"] else (640, 360)
        try:
            face = PageFace(fsize, seed=seed, log=log)
        except Exception as e:  # noqa: BLE001  no browser, no render mode: the robot alone, said so
            log(f"render: the face could not be drawn ({type(e).__name__}: {str(e).splitlines()[0][:160]}) — "
                "rendering the robot alone (layout a)")
            face, lay = None, plan("a", w, h)
    import face_bridge
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
        rw, rh = lay["robot"][:2]
        robot = Robot(rw, rh)
        pts = []
        for t in np.arange(t_from, t_from + n / fps + 1e-9, 0.2):   # the robot's outline over the whole span
            robot.pose(P.sample(tl, float(t)))
            pts.append(robot.outline())
        proj = None
        if (tl.get("tracks") or {}).get("VIDEO"):     # the show's video on the screen behind the robot
            import library
            import projection
            sc = robot.scene
            proj = projection.Projection(tl["tracks"], library.root(), sc.PROJ_TEX_W, sc.PROJ_TEX_H, sc.PROJ_FPS)
            proj.wait()
            notes.extend(proj.notes)
            sc.set_projection_visible(robot.model, True)
            pts.append(np.asarray(sc.projection_corners(), float))   # the audience sees the robot in front of it
        robot.fit(np.concatenate(pts))
        log(f"render: framed the span's outline: camera {robot.cam.distance:.2f} m from "
            f"{np.round(robot.cam.lookat, 2).tolist()}")
        follow = face_bridge.Follower()
        refused = set()

        def face_at(t):
            px, replies = face.frame(follow.update(1, tl, t, True), t)
            for r in replies:
                if isinstance(r, str) and r.startswith("ERR") and r not in refused:
                    refused.add(r)
                    notes.append(f"face: the page answered {r!r}")
            return px

        digest = []
        try:
            if face is not None:                      # the lead-in: the face's own opening, before the first frame
                lead = max(0.0, t_from - FACE_LEAD_S)
                for k in range(int(round((t_from - lead) * fps))):
                    face_at(lead + k / fps)
            for i in range(n):
                t = t_from + i / fps
                face_rgb = head_rgb = None
                if face is not None:
                    px = face_at(t)
                    if lay["face"] is not None:
                        face_rgb = to_rgb(px, lay["face"][:2])
                    head_rgb = to_rgb(px, robot.face_wh)
                img = compose(lay, w, h, robot.frame(P.sample(tl, t), head_rgb,
                                                     proj.picture(t) if proj is not None else None), face_rgb)
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
            if own_face and face is not None:
                face.close()
        if code:
            raise SystemExit(f"ffmpeg failed (exit {code})")
    if hashes is not None:
        hashes.extend(digest)
    for m in notes:
        log(m)
    log(f"render: wrote {out} — {n} frames, {n / fps:.1f} s, {w}x{h} at {fps:g} fps, layout "
        f"{layout if face is not None or layout == 'a' else 'a (no face)'}")
    return {"frames": n, "seconds": n / fps, "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("show")
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", choices=("a", "b", "c"), default="c",
                    help="c: the robot and the face side by side (default); b: the robot with a face inset; "
                         "a: the robot alone (the face still on its head)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--size", default="1280x720", help="WIDTHxHEIGHT, even numbers (e.g. 1920x1080)")
    ap.add_argument("--limits", default=None, help="the arm limits (default: as the composer: the TEST fixture "
                                                   "unless the robot's file is here)")
    ap.add_argument("--from", dest="t_from", type=float, default=0.0, help="start, show seconds")
    ap.add_argument("--to", dest="t_to", type=float, default=None, help="end, show seconds")
    ap.add_argument("--allow-invalid", action="store_true")
    ap.add_argument("--seed", type=int, default=1, help="the face's randomness (blinks, glances): same seed, same video")
    a = ap.parse_args(argv)
    w, h = (int(x) for x in a.size.lower().split("x"))
    if w % 2 or h % 2:
        raise SystemExit("--size: width and height must be even (H.264)")
    render(a.show, a.out, a.layout, a.fps, (w, h), a.limits, a.t_from, a.t_to, a.allow_invalid, seed=a.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
