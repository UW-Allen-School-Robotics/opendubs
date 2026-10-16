#!/usr/bin/env python3
"""The Composer Quickstart's screen grabs, made the same way every time: the real composer, headless,
on a shipped demo show, each view annotated with highlight boxes and numbered callouts.

    python3 software/choreo/tools/doc_shots.py                    # -> docs/assets/composer/
    python3 software/choreo/tools/doc_shots.py --out /tmp/shots --only 01,04

Needs: pyyaml, numpy, pillow, playwright (with an installed Chrome, or `playwright install chromium`),
and for the twin and render shots mujoco and imageio-ffmpeg — the studio's own environment has them all.

ISOLATED BY CONSTRUCTION. Everything runs in a temporary folder on ports the OS picks: its own composer
(on a COPY of the demo show, with the demo's generated music), its own headless browser with audio muted,
and for the face the face page in its render mode on its own server (render.PageFace), closed at the end.
It never attaches to a running studio, twin, face page or robot.

Every shot writes `<name>.png`, and `shots.json` lists each shot's callouts (number -> what it points at),
so the page's captions can quote them. One accent colour for every box and number (ACCENT).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CHOREO = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(CHOREO))
sys.path.insert(0, CHOREO)

ACCENT = (25, 209, 196)          # #19d1c4: the dashboard's and the composer's --accent
INK = (4, 32, 29)                # the dark text the dashboard puts on its accent
DEMO = "wave-and-nod.yaml"       # a shipped show: arms, head and face, with generated music
VIEW = (1920, 1080)
# An example note, added to the demo's COPY only, so the NOTES shot has something to show
DEMO_NOTE = ('  - { id: n1, at: "3.1", to: "4.4", slots: [ARM_R], status: open, '
             'text: "Make the right arm\'s wave bigger here, and finish it on beat 1 of bar 5." }')


# ---- annotation ------------------------------------------------------------------------------------------
def font(size):
    from PIL import ImageFont
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.isfile(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def annotate(png: str, marks: list, offset=(0, 0)) -> None:
    """Draw each mark — {"box": (x, y, w, h) in page pixels, "n": number} — as a rounded outline in the
    accent colour with a numbered disc at its top-left corner. `offset`: the shot's own origin on the page."""
    from PIL import Image, ImageDraw
    im = Image.open(png).convert("RGB")
    d = ImageDraw.Draw(im)
    f = font(17)
    ox, oy = offset
    for m in marks:
        x, y, w, h = m["box"]
        x, y = x - ox, y - oy
        pad = 4
        d.rounded_rectangle([x - pad, y - pad, x + w + pad, y + h + pad], radius=8, outline=ACCENT, width=3)
        r = 13
        cx = max(r + 1, min(im.width - r - 1, x - pad))
        cy = max(r + 1, min(im.height - r - 1, y - pad))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT, outline=INK, width=2)
        t = str(m["n"])
        tw = d.textlength(t, font=f)
        d.text((cx - tw / 2, cy - 11), t, fill=INK, font=f)
    im.save(png)


def box(page, selector, nth=0, pad=0):
    loc = page.locator(selector).nth(nth)
    b = loc.bounding_box()
    if b is None:
        raise SystemExit(f"doc_shots: {selector!r} is not on the page — has the composer changed?")
    # only what is on screen: a timeline row is far wider than the window
    x0, y0 = max(0.0, b["x"] - pad), max(0.0, b["y"] - pad)
    x1, y1 = min(VIEW[0] - 6.0, b["x"] + b["width"] + pad), min(VIEW[1] - 6.0, b["y"] + b["height"] + pad)
    return (x0, y0, x1 - x0, y1 - y0)


def union(*bs):
    x0 = min(b[0] for b in bs)
    y0 = min(b[1] for b in bs)
    x1 = max(b[0] + b[2] for b in bs)
    y1 = max(b[1] + b[3] for b in bs)
    return (x0, y0, x1 - x0, y1 - y0)


def clip_around(b, margin, view=VIEW):
    x, y, w, h = b
    x0, y0 = max(0, x - margin), max(0, y - margin)
    return {"x": x0, "y": y0, "width": min(view[0] - x0, w + 2 * margin), "height": min(view[1] - y0, h + 2 * margin)}


# ---- the isolated composer ---------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stage(tmp: str) -> str:
    """The demo show's COPY, its generated music and an example note, in a temporary shows folder."""
    shows = os.path.join(tmp, "shows")
    os.makedirs(os.path.join(shows, "audio"))
    gen = os.path.join(shows, "audio", "make_test_audio.py")
    shutil.copy(os.path.join(CHOREO, "shows", "audio", "make_test_audio.py"), gen)
    subprocess.run([sys.executable, gen], check=True, capture_output=True)       # writes beside itself
    text = open(os.path.join(CHOREO, "shows", DEMO)).read()
    assert "timeline_notes:" not in text, "the demo show grew notes: update doc_shots.py"
    text = text.replace("\ncues:\n", "\ntimeline_notes:\n" + DEMO_NOTE + "\ncues:\n", 1)
    assert "timeline_notes:" in text
    open(os.path.join(shows, DEMO), "w").write(text)
    return shows


@contextlib.contextmanager
def composer(shows: str, tmp: str):
    port = free_port()
    log = open(os.path.join(tmp, "composer.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(CHOREO, "composer", "server.py"), "--port", str(port),
                          "--shows", shows, "--library", os.path.join(tmp, "library"),
                          "--limits", os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml")],
                         stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("doc_shots: the composer did not start — see " + log.name)
        yield f"http://127.0.0.1:{port}/?show={DEMO}"
    finally:
        p.terminate()
        p.wait(10)
        log.close()


def browser(pw):
    args = ["--mute-audio", "--autoplay-policy=no-user-gesture-required"]
    try:
        return pw.chromium.launch(channel="chrome", args=args)
    except Exception:  # noqa: BLE001  no installed Chrome: Playwright's own
        return pw.chromium.launch(args=args)


# ---- the shots -------------------------------------------------------------------------------------------
def shots_composer(page, out: str, want, meta: dict) -> None:
    def at(t):
        page.evaluate(f"() => {{ S.t = {t}; render(); }}")
        page.wait_for_timeout(250)

    def shot(name, clip, marks, what):
        if want and name[9:11] not in want:
            return
        path = os.path.join(out, name + ".png")
        page.screenshot(path=path, clip=clip)
        annotate(path, marks, (clip["x"], clip["y"]))
        meta[name] = {"shows": what, "callouts": {str(m["n"]): m["label"] for m in marks}}
        print("doc_shots: wrote", path)

    at(3.6)
    full = {"x": 0, "y": 0, "width": VIEW[0], "height": VIEW[1]}
    shot("composer-01-overview", full, [
        {"n": 1, "box": box(page, "#show"), "label": "the show being edited (show picker); file: new, copy, rename, delete"},
        {"n": 2, "box": union(box(page, "#rewind"), box(page, "#play"), box(page, "#clock")), "label": "play / pause (space) and the playhead clock"},
        {"n": 3, "box": box(page, "#heads"), "label": "the lanes: NOTES, CLIPS, then one per part of the robot (ARM_L … DEVICE)"},
        {"n": 4, "box": union(box(page, "#ruler"), box(page, "#music")), "label": "the bar/beat ruler and the song's waveform"},
        {"n": 5, "box": box(page, "#previewPane"), "label": "At the playhead: what every part is doing now"},
        {"n": 6, "box": box(page, "#inspectorPane"), "label": "Cue: the selected cue's fields"},
        {"n": 7, "box": box(page, "#problemsPane"), "label": "the validator: problems, warnings and notes"},
        {"n": 8, "box": union(box(page, "#save"), box(page, "#status")), "label": "save (Ctrl/Cmd-S), and the validator's verdict on the whole show"},
    ], "the whole composer")

    bar = box(page, "#bar")
    music = [box(page, "#snap"), box(page, "#metro"), box(page, "#tap"), box(page, "#tapUse"),
             box(page, "#beatOne"), box(page, "#musicBtn")]
    shot("composer-02-music", clip_around(union(bar, box(page, "#ruler"), box(page, "#music")), 6), [
        {"n": 1, "box": box(page, "#musicBtn"), "label": "♫ music: choose a song from the library or add one (or drop an audio file on the page)"},
        {"n": 2, "box": union(box(page, "#tap"), box(page, "#tapBpm"), box(page, "#tapUse")), "label": "tap along: four or more taps estimate the tempo; use writes it into the show"},
        {"n": 3, "box": box(page, "#beatOne"), "label": "beat 1 ⇤: beat 1 falls at the playhead (music.offset_s)"},
        {"n": 4, "box": box(page, "#snap"), "label": "snap: dragged cues snap to bar, beat, ½ or ¼ beat"},
        {"n": 5, "box": box(page, "#metro"), "label": "click: a metronome on every beat"},
        {"n": 6, "box": box(page, "#ruler"), "label": "the grid the show is written on: bar.beat"},
    ], "music, tempo and the grid")
    del music

    # a cue placed: the HEAD lane's + adds one at the playhead, and the inspector shows it
    at(6.0)
    page.locator('button[data-add="HEAD"]').click()
    page.wait_for_timeout(300)
    page.locator('#lanes [data-lane="HEAD"] [data-t="6"]').first.click()      # select it: the inspector opens
    page.wait_for_timeout(300)
    lanes = box(page, "#tl")
    sel = page.locator('#lanes [data-lane="HEAD"] [data-t="6"]').first
    cue_box = sel.bounding_box() if sel.count() else None
    marks = [{"n": 1, "box": box(page, 'button[data-add="HEAD"]'), "label": "+ adds a cue to this lane at the playhead"}]
    if cue_box:
        marks.append({"n": 2, "box": (cue_box["x"], cue_box["y"], cue_box["width"], cue_box["height"]),
                      "label": "the new cue: drag to move it (it snaps to the grid)"})
    marks.append({"n": 3, "box": box(page, "#inspectorPane"), "label": "its fields, edited here; the validator re-checks every edit"})
    shot("composer-03-cues", clip_around(union(lanes, box(page, "#inspectorPane")), 4), marks, "placing and editing cues")

    page.keyboard.press("Control+z")
    page.wait_for_timeout(300)
    note = page.locator(".tnote").first
    nb = note.bounding_box()
    shot("composer-04-notes", clip_around(union(box(page, ".head.notes"), (nb["x"], nb["y"], nb["width"], nb["height"]),
                                                box(page, "#heads", 0)), 4) | {"height": 260}, [
        {"n": 1, "box": box(page, ".head.notes"), "label": "the NOTES lane: requests for whoever edits next, often an agent"},
        {"n": 2, "box": box(page, 'button[data-addnote="1"]'), "label": "+ adds a note at the playhead"},
        {"n": 3, "box": box(page, 'button[data-prompt="1"]'), "label": "⧉ copies the open notes as a prompt for an agent (save first: the agent reads the file)"},
        {"n": 4, "box": (nb["x"], nb["y"], nb["width"], nb["height"]), "label": "a note over a span of time (and some parts); the agent replies on it"},
    ], "notes for an agent")

    # the validator, with a deliberate problem: a head cue past the head's travel (in the demo's COPY only)
    if not want or "05" in want:
        at(6.0)
        page.locator('button[data-add="HEAD"]').click()
        page.wait_for_timeout(300)
        page.locator('#lanes [data-lane="HEAD"] [data-t="6"]').first.click()
        page.wait_for_timeout(300)
        page.fill("#iFields", '{"slot":"HEAD","pan":200,"tilt":90}')
        page.click("#iApply")
        page.wait_for_timeout(600)
        chip = union(box(page, "#save"), box(page, "#status"))
        pane = box(page, "#problemsPane")
        a = clip_around(chip, 8)
        b = {"x": pane[0], "y": pane[1], "width": pane[2], "height": min(pane[3], 230)}
        pa, pb = os.path.join(out, "_a.png"), os.path.join(out, "_b.png")
        page.screenshot(path=pa, clip=a)
        page.screenshot(path=pb, clip=b)
        annotate(pa, [{"n": 1, "box": box(page, "#status")}], (a["x"], a["y"]))
        first = page.locator("#problems li.err").first.bounding_box()
        annotate(pb, [{"n": 2, "box": (first["x"], first["y"], first["width"], first["height"])}], (b["x"], b["y"]))
        from PIL import Image
        ia, ib = Image.open(pa), Image.open(pb)
        im = Image.new("RGB", (max(ia.width, ib.width), ia.height + 10 + ib.height), (7, 10, 15))
        im.paste(ia, (im.width - ia.width, 0))
        im.paste(ib, (0, ia.height + 10))
        path = os.path.join(out, "composer-05-validate.png")
        im.save(path)
        os.remove(pa)
        os.remove(pb)
        meta["composer-05-validate"] = {"shows": "the validator, with a problem",
                                        "callouts": {"1": "the verdict on the whole show: valid, valid with warnings, or N problems",
                                                     "2": "each problem names its cue and what to change (tap it to select the cue); warnings and notes follow"}}
        print("doc_shots: wrote", path)


def shot_twin(out: str, show_path: str, meta: dict) -> None:
    """The twin at the playhead: the MuJoCo robot and the face, as the studio shows them (offscreen here)."""
    import numpy as np
    from PIL import Image
    import cue
    import face_bridge
    import render
    import sim_preview as P
    lim = os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml")
    tl = cue.load_show(show_path).export(cue.load_limits(lim), lim)
    t = 9.0
    robot = render.Robot(640, 800)
    pts = []
    for tt in np.arange(0, tl["duration_s"], 0.5):
        robot.pose(P.sample(tl, float(tt)))
        pts.append(robot.outline())
    robot.fit(np.concatenate(pts))
    face = render.PageFace((720, 405), seed=1)
    try:
        follow = face_bridge.Follower()
        px = None
        for k in range(int(t * 30) + 1):                 # step the face up to t, as a render does
            px, _ = face.frame(follow.update(1, tl, k / 30.0, True), k / 30.0)
    finally:
        face.close()                                     # never leave a face page open
    face_rgb = render.to_rgb(px, (720, 405))
    img = robot.frame(P.sample(tl, t), render.to_rgb(px, robot.face_wh))
    canvas = Image.new("RGB", (640 + 760, 800), (7, 10, 15))
    canvas.paste(Image.fromarray(img), (0, 0))
    canvas.paste(Image.fromarray(face_rgb), (660, 197))
    path = os.path.join(out, "composer-06-twin.png")
    canvas.save(path)
    marks = [{"n": 1, "box": (30, 40, 580, 720), "label": "the MuJoCo twin: the robot as the show moves it (arms, head, lift, base)"},
             {"n": 2, "box": (660, 197, 720, 405), "label": "the face twin: the head's face, driven by the same show"}]
    annotate(path, marks)
    meta["composer-06-twin"] = {"shows": "the twin at the playhead", "callouts": {str(m["n"]): m["label"] for m in marks}}
    print("doc_shots: wrote", path)


def shot_render(out: str, show_path: str, tmp: str, meta: dict) -> None:
    """`opendubs-studio render`: the command, and a frame of the video it writes."""
    from PIL import Image, ImageDraw
    import render
    mp4 = os.path.join(tmp, "demo.mp4")
    lines = []
    with contextlib.redirect_stdout(io.StringIO()):
        render.render(show_path, mp4, layout="c", size=(1280, 720), limits=os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml"),
                      t_from=8.0, t_to=9.0, log=lines.append, seed=1)
    frame = os.path.join(tmp, "frame.png")
    subprocess.run([render.ffmpeg(), "-v", "error", "-y", "-ss", "0.5", "-i", mp4, "-frames:v", "1", frame], check=True)
    cmd = "$ opendubs-studio render wave-and-nod.yaml --out wave-and-nod.mp4"
    said = [l for l in lines if l.startswith("render: wrote")][:1]
    W, top = 1280, 120
    im = Image.new("RGB", (W, top + 720), (7, 10, 15))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([16, 14, W - 16, top - 14], radius=8, fill=(14, 20, 29))
    f = font(20)
    d.text((32, 28), cmd, fill=(230, 237, 243), font=f)
    if said:
        d.text((32, 62), said[0].replace(mp4, "wave-and-nod.mp4").split(" — ")[0] + " — 1280x720, 30 fps, with the music",
               fill=(107, 124, 143), font=font(16))
    im.paste(Image.open(frame).convert("RGB"), (0, top))
    path = os.path.join(out, "composer-07-render.png")
    im.save(path)
    marks = [{"n": 1, "box": (22, 20, W - 44, top - 40), "label": "the render command (the studio's; render.py underneath)"},
             {"n": 2, "box": (6, top + 6, 564, 708), "label": "the robot twin, framed to the whole show"},
             {"n": 3, "box": (590, top + 160, 684, 400), "label": "the face, beside it (and on the robot's head)"}]
    annotate(path, marks)
    meta["composer-07-render"] = {"shows": "rendering a show to video", "callouts": {str(m["n"]): m["label"] for m in marks}}
    print("doc_shots: wrote", path)


@contextlib.contextmanager
def shows_page(shows: str, tmp: str):
    """The dashboard's Shows page, isolated: a runner in its FAKE-robot mode on its own port, and a stub that
    serves the dashboard's own page and forwards only the routine calls to that runner. No dashboard backend
    runs, so nothing here can reach a head, a StamPLC or a robot."""
    import functools
    import http.server
    import threading
    rport, sport = free_port(), free_port()
    empty = os.path.join(tmp, "robot-shows")
    os.makedirs(empty, exist_ok=True)
    log = open(os.path.join(tmp, "runner.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(CHOREO, "runner.py"), "palette", shows, "--robot", "fake",
                          "--port", str(rport), "--local-shows", empty, "--earcons", "off",
                          "--limits", os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml")],
                         stdout=log, stderr=subprocess.STDOUT)
    front = os.path.join(REPO, "software", "kiosk-dashboard", "frontend")

    class Stub(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] in ("/api/choreo/routines", "/api/choreo/palette"):
                try:
                    body = urllib.request.urlopen(f"http://127.0.0.1:{rport}/api/{self.path.split('/')[-1]}", timeout=5).read()
                    code = 200
                except OSError as e:
                    body, code = json.dumps({"error": str(e)}).encode(), 503
            elif self.path.startswith("/api/"):
                body, code = b'{"error": "not in this stub"}', 404
            else:
                return super().do_GET()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", sport), functools.partial(Stub, directory=front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{rport}/api/routines", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{sport}/"
    finally:
        srv.shutdown()
        p.terminate()
        p.wait(10)
        log.close()


def shot_shows(page, out: str, meta: dict) -> None:
    from PIL import Image, ImageDraw
    page.evaluate("() => setView('shows')")
    page.wait_for_timeout(1500)
    card = box(page, ".shows-card")
    clip = clip_around(card, 4)
    pc = os.path.join(out, "_shows.png")
    page.screenshot(path=pc, clip=clip)
    marks = [{"n": 2, "box": box(page, "#showsList"), "label": "every show on the robot that validates, as a button; icons show what it uses"},
             {"n": 3, "box": box(page, "#showsStart"), "label": "how a run starts: wait for the deadman, count down, or now"},
             {"n": 4, "box": union(box(page, "#showsStop"), box(page, "#showsPark")), "label": "STOP, and park both arms"}]
    annotate(pc, marks, (clip["x"], clip["y"]))
    cmd = "$ python3 software/choreo/show_sync.py push <robot> my-show.yaml"
    top = 70
    shot = Image.open(pc).convert("RGB")
    im = Image.new("RGB", (max(shot.width, 900), top + shot.height), (7, 10, 15))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([12, 10, im.width - 12, top - 10], radius=8, fill=(14, 20, 29))
    d.text((28, 22), cmd, fill=(230, 237, 243), font=font(20))
    im.paste(shot, (0, top))
    path = os.path.join(out, "composer-08-shows.png")
    im.save(path)
    os.remove(pc)
    annotate(path, [{"n": 1, "box": (18, 14, im.width - 36, top - 28)}])
    meta["composer-08-shows"] = {"shows": "putting a show on the robot, and running it from the dashboard",
                                 "callouts": {"1": "push the show to the robot (checked here first; --dry-run shows what would happen)",
                                              **{str(m["n"]): m["label"] for m in marks}}}
    print("doc_shots: wrote", path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(REPO, "docs", "assets", "composer"))
    ap.add_argument("--only", default="", help="shot numbers, e.g. 01,04")
    a = ap.parse_args(argv)
    want = {s.strip().zfill(2) for s in a.only.split(",") if s.strip()}
    os.makedirs(a.out, exist_ok=True)
    meta_path = os.path.join(a.out, "shots.json")
    meta = json.load(open(meta_path)) if os.path.isfile(meta_path) else {}
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="opendubs-docshots-") as tmp:
        shows = stage(tmp)
        if not want or want & {"01", "02", "03", "04", "05"}:
            with composer(shows, tmp) as url, sync_playwright() as pw:
                b = browser(pw)
                try:
                    page = b.new_page(viewport={"width": VIEW[0], "height": VIEW[1]})
                    page.goto(url)
                    page.wait_for_function("() => typeof S !== 'undefined' && tl()", timeout=20000)
                    page.wait_for_timeout(1200)
                    shots_composer(page, a.out, want, meta)
                finally:
                    b.close()
        show_path = os.path.join(shows, DEMO)
        if not want or "06" in want:
            shot_twin(a.out, show_path, meta)
        if not want or "07" in want:
            shot_render(a.out, show_path, tmp, meta)
        if not want or "08" in want:
            for f in os.listdir(os.path.join(CHOREO, "shows")):     # a few shipped routines beside the demo
                if f.endswith(".yaml") and f != DEMO:
                    shutil.copy(os.path.join(CHOREO, "shows", f), shows)
            with shows_page(shows, tmp) as url, sync_playwright() as pw:
                b = browser(pw)
                try:
                    page = b.new_page(viewport={"width": 1280, "height": 1000})
                    page.goto(url)
                    page.wait_for_timeout(1500)
                    shot_shows(page, a.out, meta)
                finally:
                    b.close()
    with open(meta_path, "w") as fh:
        json.dump(dict(sorted(meta.items())), fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
