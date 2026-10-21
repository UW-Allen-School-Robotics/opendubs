#!/usr/bin/env python3
"""Projector video (video_out.py; notes/design/choreo-video.md): the file server's byte ranges (a
Cast receiver fetches and seeks by them), the schedule on the show's clock, the runner's preflight
for a video output, and the VIDEO cue's rules. No projector, no pychromecast, no mpv needed."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

LIB = tempfile.mkdtemp(prefix="opendubs-video-lib-")
os.environ["OPENDUBS_LIBRARY"] = LIB

import cue  # noqa: E402
import runner as R  # noqa: E402
import video_out as V  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
R.REQUIRE_SWEEP = False
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


print("video: the Thor serves the file by byte ranges")
f = os.path.join(LIB, "clip.mp4")
data = bytes(range(256)) * 40
open(f, "wb").write(data)
srv = V.MediaServer(port=0, bind="127.0.0.1")
key = srv.register(f)
base = f"http://127.0.0.1:{srv.port}"


def get(path, rng=None):
    req = urllib.request.Request(base + path, headers={"Range": rng} if rng else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(e.headers)


code, body, h = get(key, "bytes=100-199")
check("a range request gets 206 and exactly those bytes, with Content-Range",
      code == 206 and body == data[100:200] and h.get("Content-Range") == f"bytes 100-199/{len(data)}", (code, h))
code, body, _ = get(key, "bytes=-10")
check("... a suffix range gets the last bytes", code == 206 and body == data[-10:], code)
code, body, h = get(key)
check("... no range gets the whole file, saying ranges are accepted",
      code == 200 and body == data and h.get("Accept-Ranges") == "bytes", code)
check("only a registered file is served (no directory, no other path)", get("/v/../index.yaml")[0] == 404
      and get("/" + os.path.basename(f))[0] == 404)
code, body, h = get("/black.png")
check("the black still is served, a real PNG", code == 200 and body[:8] == b"\x89PNG\r\n\x1a\n")
srv.close()

print("\nvideo: the schedule, on the show's clock")


class FakeOut:
    kind, lead_s, tol_s, min_gap_s, nudge = "cast", 0.5, V.CAST_TOL_S, V.SEEK_MIN_GAP_S, 0.0

    def __init__(self, drift=0.0):
        self.calls, self.drift, self.playing_since = [], drift, None
        self.now = 0.0

    def load(self, path, from_s):
        self.calls.append(("load", os.path.basename(path), from_s))

    def play(self):
        self.calls.append(("play", round(self.now, 2)))
        self.playing_since = self.now

    def seek(self, pos):
        self.calls.append(("seek", round(self.now, 2)))
        self.drift = 0.0

    def black(self):
        self.calls.append(("black", round(self.now, 2)))

    def stop(self):
        self.calls.append(("stop", round(self.now, 2)))

    def pause(self):
        self.calls.append(("pause", round(self.now, 2)))

    def speed(self, r):
        pass

    def position(self):
        # the receiver's clock runs `drift` seconds per second fast
        el = self.now - self.playing_since
        return el + self.lead_s * 0 + el * self.drift, True


out = FakeOut(drift=0.05)
sv = V.ShowVideo(out, [{"t": 2.0, "end_t": 30.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}])
sv.prepare()
t = 0.0
while t < 31.0:
    out.now = t
    sv.tick(t)
    t = round(t + 0.05, 2)
plays = [c for c in out.calls if c[0] == "play"]
seeks = [c[1] for c in out.calls if c[0] == "seek"]
check("a video is loaded first and played EARLY by its output's start lead", out.calls[0][0] == "load"
      and plays == [("play", 1.5)], out.calls[:3])
check("drift past the tolerance is seeked back, but never more often than the minimum gap",
      seeks and all(b - a >= V.SEEK_MIN_GAP_S for a, b in zip(seeks, seeks[1:])), seeks)
check("at its end it goes black", ("black", 30.0) in out.calls, out.calls[-3:])
out = FakeOut()
sv = V.ShowVideo(out, [{"t": 0.0, "end_t": 30.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}])
sv.prepare()
out.now = 0.0
sv.tick(0.0)
sv.end("ESTOPPED")
check("an e-stop stops it plainly (no black still loaded, nothing more sent)", out.calls[-1][0] == "stop", out.calls)
out = FakeOut()
sv = V.ShowVideo(out, [{"t": 5.0, "end_t": 9.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}])
sv.end("REFUSED")
check("a run refused before its video loaded leaves the projector alone", out.calls == [], out.calls)

print("\nvideo: the runner's preflight")
open(os.path.join(LIB, "index.yaml"), "w").write(
    "- {id: clip, kind: video, file: clip.mp4, sha256: x, bytes: 10240, source: test, licence: test, "
    "duration_s: 20, width: 1280, height: 720}\n")


class Out(FakeOut):
    def __init__(self, ready):
        super().__init__()
        self.ready = ready

    def status(self):
        return {"ready": self.ready, "why": None if self.ready else "projector not found on the robot's Wi-Fi"}


def run_show(extra, out, override=None):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=LIB)
    tmp.write("id: v\nslots: [VIDEO, FACE]\nrequires: {tab5: true}\n" + extra + "cues:\n"
              "  - { t: 1.0, slot: VIDEO, do: play, video: clip, secs: 3 }\n"
              "  - { t: 4.5, slot: FACE, do: emote, name: SOFT }\n")
    tmp.close()
    robot = R.FakeRobotState()
    clk = {"t": 1000.0}

    def sleep(s):
        clk["t"] += s
    r = R.Runner(tmp.name, LIMITS, robot, log=io.StringIO(), clock=lambda: clk["t"], sleep=sleep, start_lead=0.5,
                 video={"cast": out}, video_override=override)
    return r, r.run()


r, end = run_show("", Out(ready=False))
check("an output that isn't ready: the show runs WITHOUT its video, saying why",
      end[0] == "COMPLETED" and not r.videos and "not found" in (r.video_note or ""), (end, r.video_note))
r, end = run_show("video: { required: true }\n", Out(ready=False))
check("... unless the show requires it: refused, SHOW_VIDEO_NOT_READY, with the reason in operator words",
      end[:2] == ("REFUSED", "SHOW_VIDEO_NOT_READY") and "Wi-Fi" in end[2], end)
r, end = run_show("", Out(ready=True), override="off")
check("the operator's play-time choice of OFF runs it without video", end[0] == "COMPLETED" and not r.videos, end)
r, end = run_show("", Out(ready=True))
check("a ready output gets the show's video", end[0] == "COMPLETED" and len(r.videos) == 1
      and r.videos[0].items[0]["id"] == "clip" and r.videos[0].items[0]["end_t"] == 4.0, r.videos and r.videos[0].items)

print("\naudio: music on the projector, dialogue on the tablet (choreo-audio.md)")
SETTINGS = os.path.join(LIB, "projector.json")
V.SETTINGS_PATH = SETTINGS
V.save_settings({"audio_lead_s": 0.6, "video_lead_s": 0.9})
open(os.path.join(LIB, "song.wav"), "wb").write(b"RIFF....WAVE")


def run_audio(music_role, out, extra_cues="", lose=False):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=LIB)
    role = f", role: {music_role}" if music_role else ""
    tmp.write("id: a\nslots: [FACE, VIDEO]\nrequires: {tab5: true}\n"
              f"music: {{ file: song.wav, bpm: 120{role} }}\ncues:\n"
              "  - { t: 4.0, slot: FACE, do: emote, name: SOFT }\n" + extra_cues)
    tmp.close()
    clk = {"t": 1000.0}

    def sleep(s):
        clk["t"] += s
    audio = R.NullAudio(holder="tablet")
    r = R.Runner(tmp.name, LIMITS, R.FakeRobotState(), log=io.StringIO(), clock=lambda: clk["t"], sleep=sleep,
                 start_lead=0.5, audio=audio, video={"cast": out})
    if lose:                                   # mid-show: just after the tablet starts the show's audio
        send = audio.send

        def send_then_lose(msg):
            send(msg)
            if msg.get("op") == "start" and r.music:
                r.music[0].on_lost("the projector stopped answering")
        audio.send = send_then_lose
    end = r.run()
    load = next((c for c in audio.calls if c.get("op") == "load"), {})
    return r, end, load.get("items", []), audio.calls


r, end, items, _ = run_audio(None, Out(ready=True))
check("unmarked audio is DIALOGUE: it stays on the tablet, never the projector, even with one ready",
      end[0] == "COMPLETED" and not r.music and items and not items[0].get("held"), (items, r.music))
r, end, items, _ = run_audio("music", Out(ready=True))
check("MUSIC goes to the projector, started early by ITS AUDIO offset (projector.json) — and stays on the "
      "tablet too, HELD, as the fallback",
      r.music and r.music[0].items[0]["lead_s"] == 0.6 and items[0].get("held") is True and r.music_on == "projector"
      and "path" not in items[0], (items, r.music and r.music[0].items))
r, end, items, _ = run_audio("music", Out(ready=False))
check("no projector: the music plays from the tablet, as today, saying why",
      not r.music and not items[0].get("held") and r.music_on == "tablet" and "Wi-Fi" in (r.music_why or ""), r.music_why)
r, end, items, _ = run_audio("music", Out(ready=True), "  - { t: 1.0, slot: VIDEO, do: play, video: clip, secs: 2 }\n")
check("a show that casts its own video keeps its music on the tablet (one media item at a time)",
      not r.music and not items[0].get("held") and "videos" in (r.music_why or ""), r.music_why)
r, end, items, calls = run_audio("music", Out(ready=True), lose=True)
check("the projector dropping out hands the music to the tablet (unhold, same load), and the show goes on",
      end[0] == "COMPLETED" and any(c.get("op") == "unhold" and c.get("id") == r._load_id for c in calls)
      and r.music_on == "tablet", (end, [c.get("op") for c in calls]))
# a video that CARRIES the show's song (its library entry's `song`): the projector plays the music
with open(os.path.join(LIB, "index.yaml"), "a") as fh:
    fh.write("- {id: tune, kind: song, file: song.wav, sha256: x, bytes: 12, source: test, licence: test, duration_s: 20}\n"
             "- {id: clipsong, kind: video, file: clip.mp4, sha256: x, bytes: 10240, source: test, licence: test, "
             "duration_s: 20, width: 1280, height: 720, song: tune}\n")


def run_carrier(lose=False):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=LIB)
    tmp.write("id: c\nslots: [FACE, VIDEO]\nrequires: {tab5: true}\nmusic: { song: tune, bpm: 120 }\ncues:\n"
              "  - { t: 0.0, slot: VIDEO, do: play, video: clipsong, secs: 4 }\n"
              "  - { t: 4.0, slot: FACE, do: emote, name: SOFT }\n")
    tmp.close()
    clk = {"t": 1000.0}

    def sleep(s):
        clk["t"] += s
    audio = R.NullAudio(holder="tablet")
    r = R.Runner(tmp.name, LIMITS, R.FakeRobotState(), log=io.StringIO(), clock=lambda: clk["t"], sleep=sleep,
                 start_lead=0.5, audio=audio, video={"cast": Out(ready=True)})
    if lose:
        send = audio.send

        def send_then_lose(msg):
            send(msg)
            if msg.get("op") == "start" and r.videos and r.videos[0].on_lost:
                r.videos[0].on_lost("the projector stopped answering")
        audio.send = send_then_lose
    end = r.run()
    load = next((c for c in audio.calls if c.get("op") == "load"), {})
    return r, end, load.get("items", []), audio.calls


r, end, items, _ = run_carrier()
check("a video CARRYING the show's song: the projector plays the music, the tablet holds its copy silent, "
      "and the video starts by the AUDIO offset (the beat must land)",
      end[0] == "COMPLETED" and items and items[0].get("held") is True and r.music_on == "projector"
      and r.videos and r.videos[0].items[0]["lead_s"] == 0.6 and not r.music, (items, r.music_on, r.videos and r.videos[0].items))
r, end, items, calls = run_carrier(lose=True)
check("... and if that video drops out, the tablet takes the music over from the same point",
      end[0] == "COMPLETED" and any(c.get("op") == "unhold" for c in calls) and r.music_on == "tablet",
      [c.get("op") for c in calls])
try:
    V.save_settings({"audio_lead_s": 7})
    bad = False
except ValueError:
    bad = True
check("the projector's settings refuse a nonsense offset, and keep the ones set by ear",
      bad and V.load_settings()["audio_lead_s"] == 0.6 and V.load_settings()["video_lead_s"] == 0.9)

print("\nvideo: the VIDEO cue's rules")


def problems(text):
    s = cue.show_from_text("id: x\nslots: [VIDEO]\ncues:\n" + text, os.path.join(LIB, "x.yaml"))
    return [q.msg for q in s.analyse(cue.load_limits(LIMITS))["problems"]]


check("a play names a library VIDEO (not a song or skin)",
      any("not a video in the library" in m for m in problems("  - { t: 1, slot: VIDEO, do: play, video: nope }\n")))
check("... from_s past its end is refused",
      any("past the end" in m for m in problems("  - { t: 1, slot: VIDEO, do: play, video: clip, from_s: 25 }\n")))
check("... an unknown output or end is refused", len(problems(
    "  - { t: 1, slot: VIDEO, do: play, video: clip, output: tv, end: fade }\n")) == 2)
check("... and two videos at once collide (one projector)", any("collision" in m for m in problems(
    "  - { t: 1, slot: VIDEO, do: play, video: clip }\n  - { t: 5, slot: VIDEO, do: play, video: clip }\n")))
check("a valid video cue validates", not problems("  - { t: 1, slot: VIDEO, do: play, video: clip, secs: 4 }\n"
                                                  "  - { t: 6, slot: VIDEO, do: stop }\n"))
check("a sound's role is music or dialogue, nothing else",
      any("role 'speech'" in m for m in problems("  - { t: 1, slot: SOUND, do: play, sound: attend, role: speech }\n")))

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
