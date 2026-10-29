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


def run_show(extra, out, override=None, key="cast"):
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
                 video={key: out}, video_override=override)
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


def run_audio(music_role, out, extra_cues="", lose=False, key="cast"):
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
                 start_lead=0.5, audio=audio, video={key: out}, video_default=key)
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

print("\nvideo: the STAGE PAGE gets only the show's timing (choreo-stage.md)")
import threading  # noqa: E402
import time  # noqa: E402


class FakeStage(V.StageOutput):
    """The dashboard's /api/stage and /api/stage/show, in memory."""
    def __init__(self, pages=1):
        super().__init__("http://dash.invalid", library_root=LIB)
        self.n, self.sent, self.cv = pages, [], threading.Condition()

    def _get(self):
        return {"pages": self.n}

    def post(self, msg):
        with self.cv:
            self.sent.append(msg)
            self.cv.notify_all()
        return {"ok": True}

    def wait_for(self, op, secs=3.0):
        with self.cv:
            return self.cv.wait_for(lambda: any(m["op"] == op for m in self.sent), secs)


so = FakeStage(pages=0)
check("no stage page open: not ready, saying where to open one",
      so.status()["ready"] is False and "/stage/" in so.status()["why"], so.status())
so.n = 2
check("... a page open: ready, with how many", so.status() == {"present": True, "ready": True, "pages": 2, "why": None,
                                                                 "page": None},
      so.status())
check("a library video is served to the page by the dashboard, same origin",
      so.url(f) == "/api/choreo/media/library/clip.mp4", so.url(f))

so = FakeStage()
sh = so.make_show([{"t": 1.0, "end_t": 4.0, "path": f, "from_s": 2.0, "end": "hold", "id": "clip"}],
                  {"bpm": 120, "offset_s": 0.25})
sh.begin()
check("load sends the videos (show seconds, the dashboard's URL) and the beat grid — nothing device-specific",
      so.wait_for("load") and so.sent[0]["videos"] == [{"t": 1.0, "end_t": 4.0, "url": "/api/choreo/media/library/clip.mp4",
                                                        "from_s": 2.0, "end": "hold", "id": "clip",
                                                        "lead_s": 0.0, "lead_kind": "video", "audio": False,
                                                        "sha256": None, "lib": "clip"}]
      and so.sent[0]["beat"] == {"offset_s": 0.25, "bpm": 120.0}, so.sent)
check("... and, by default, no per-output audio leads and no skeleton layer over the video",
      so.sent[0]["audio_leads_s"] == {} and so.sent[0]["skeletons"] is False, so.sent[0])
check("... and nothing starts before the show's clock does", [m["op"] for m in so.sent] == ["load"], so.sent)
sh.go(lambda: -0.5)                          # half a second before the show's t0
before = time.monotonic()
check("start gives t0 on the Thor's monotonic clock (now + the lead)", so.wait_for("start")
      and abs(so.sent[1]["t0_mono"] - (before + 0.5)) < 0.2 and so.sent[1]["id"] == so.sent[0]["id"], so.sent)
sh.end("ESTOPPED")
sh._thread.join(3)
check("an e-stop stops the pages with a CUT (no fade, no hold)",
      so.sent[-1] == {"op": "stop", "id": so.sent[0]["id"], "cut": True}, so.sent[-1])

so = FakeStage()
sh = so.make_show([{"t": 0.0, "end_t": 4.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}], None)
sh.begin()
sh.go(lambda: 0.0)
so.wait_for("start")
sh.end("COMPLETED")
sh._thread.join(3)
check("a show with no beat sends none; a normal end is not a cut",
      so.sent[0]["beat"] is None and so.sent[-1]["cut"] is False, so.sent)

so = FakeStage()
sh = so.make_show([{"t": 0.0, "end_t": 4.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}], None)
sh.end("REFUSED")
check("a run refused before it began sends the pages nothing", so.sent == [], so.sent)

r, end = run_show("", FakeStage(pages=1), override="stage", key="stage")
check("the runner hands a show's video to the stage output as one StageShow",
      end[0] == "COMPLETED" and len(r.videos) == 1 and isinstance(r.videos[0], V.StageShow), (end, r.videos))
r, end = run_show("video: { required: true }\n", FakeStage(pages=0), override="stage", key="stage")
check("... and refuses a show that requires video when no stage page is open",
      end[:2] == ("REFUSED", "SHOW_VIDEO_NOT_READY") and "/stage/" in end[2], end)
check("'stage' is a video output a cue may name", "stage" in cue.VIDEO_OUTPUTS)

print("\nvideo: the stage Pi — its own audio and picture delays, and the music alone (choreo-stage.md)")
got = V.save_settings({"stage_audio_lead_s": 0.18, "stage_video_lead_s": 0.05})
check("the stage's own audio and video leads are settings beside the cast's, set by ear",
      got["stage_audio_lead_s"] == 0.18 and got["stage_video_lead_s"] == 0.05 and "audio_lead_s" in got, got)
try:
    V.save_settings({"stage_audio_lead_s": 9})
    ok = False
except ValueError:
    ok = True
check("... in the same 0-3 s range", ok)


class Captured(FakeStage):
    """FakeStage that keeps each StageShow it makes, to read its load."""
    def make_show(self, items, music, log=None, on_lost=None, buffer_s=None, **kw):
        self.made = super().make_show(items, music, log, on_lost, buffer_s, **kw)
        return self.made


so = Captured()
r, end = run_show("", so, override="stage", key="stage")
so.wait_for("load")
check("a show's video on the stage starts by the stage's VIDEO lead", end[0] == "COMPLETED"
      and so.sent[0]["videos"][0]["lead_s"] == 0.05 and so.sent[0]["videos"][0]["audio"] is False, so.sent[:1])
so = Captured()
r, end, items, calls = run_audio("music", so, key="stage")
so.wait_for("load")
v = so.sent[0]["videos"][0] if so.sent else {}
check("MUSIC with no video goes to the stage page (a stage Pi's speaker): the song alone, by the stage's AUDIO "
      "lead, at the tablet's own address — and the tablet holds its copy, silent",
      r.music_on == "projector" and v.get("audio") is True and v.get("lead_s") == 0.18
      and v.get("url") == items[0]["url"] and items[0].get("held") is True, (v, items))
so = Captured()
r, end, items, calls = run_audio("music", so, key="stage", lose=True)
check("... every stage page gone: the tablet takes the music over at the same point (unhold), the show goes on",
      end[0] == "COMPLETED" and any(c.get("op") == "unhold" for c in calls) and r.music_on == "tablet",
      (end, [c.get("op") for c in calls]))


class SoundStage(Captured):
    """A stage page that reports its box's sound output (the Tablet lane's device.json → page report)."""
    def __init__(self, audio_output="bt", why=None):
        super().__init__()
        self.page = {"name": "stage-pi", "audio_output": audio_output, "audio_why": why, "audio_name": "Boom 2"}

    def _get(self):
        return {"pages": self.n, "pages_info": [self.page]}


def run_chosen(out, choice):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=LIB)
    tmp.write("id: a\nslots: [FACE]\nrequires: {tab5: true}\nmusic: { file: song.wav, bpm: 120 }\ncues:\n"
              "  - { t: 4.0, slot: FACE, do: emote, name: SOFT }\n")
    tmp.close()
    clk = {"t": 1000.0}
    audio = R.NullAudio(outputs={"tablet": "tablet", "speakerphone": "Jabra"})
    r = R.Runner(tmp.name, LIMITS, R.FakeRobotState(), log=io.StringIO(), clock=lambda: clk["t"],
                 sleep=lambda s: clk.__setitem__("t", clk["t"] + s), start_lead=0.5, audio=audio,
                 video={"stage": out}, video_default="cast", audio_output=choice)
    end = r.run()
    load = next((c for c in audio.calls if c.get("op") == "load"), {})
    return r, end, load.get("items", [])


print("\nvideo: the music played on the device the operator CHOSE (choreo-audio-output.md)")
so = SoundStage()
r, end, items = run_chosen(so, "stage")
so.wait_for("load")
v = so.sent[0]["videos"][0] if so.sent else {}
check("music chosen for the stage box goes to the stage page — even with the video elsewhere — and no hub device "
      "holds a copy (no quiet second speaker)",
      end[0] == "COMPLETED" and r.audio_device == "stage" and v.get("audio") is True and items == []
      and so.made.mute_on_stall is False, (end, v, items))
so = SoundStage(audio_output="none", why="the Bluetooth speaker Boom 2 is not connected")
r, end, items = run_chosen(so, "stage")
check("... a stage box that can't make sound refuses the run with the box's own reason",
      end[:2] == ("REFUSED", "SHOW_AUDIO_OUTPUT_MISSING") and "Boom 2 is not connected" in end[2] and not so.sent, end)
print("\nvideo: the stage's audio lead PER SOUND OUTPUT, and the skeleton layer only where a show asks (choreo-stage.md)")
got = V.save_settings({"stage_audio_leads_s": {"bt": 0.2, "usb": 0.02}})
check("each sound output the box can select keeps its own audio lead, set by ear",
      got["stage_audio_leads_s"] == {"bt": 0.2, "usb": 0.02}, got)
got = V.save_settings({"stage_audio_leads_s": {"bt": 0.225}})
check("... a change to one output MERGES: the others keep theirs",
      got["stage_audio_leads_s"] == {"bt": 0.225, "usb": 0.02}, got)
got = V.save_settings({"stage_audio_leads_s": {"usb": None}})
check("... null removes an output's lead (it goes back to the box's estimate)", got["stage_audio_leads_s"] == {"bt": 0.225}, got)
for bad in ({"stage_audio_leads_s": {"radio": 0.1}}, {"stage_audio_leads_s": {"bt": 5}}, {"stage_audio_leads_s": 0.2}):
    try:
        V.save_settings(bad)
        ok = False
    except ValueError:
        ok = True
    check(f"... refused: {bad}", ok)
check("... and the store keeps them across a reload", V.load_settings()["stage_audio_leads_s"] == {"bt": 0.225})
so = Captured()
r, end, items, calls = run_audio("music", so, key="stage")
so.wait_for("load")
check("the song alone on the stage is HEARD (lead_kind audio), and the load carries the per-output leads",
      so.sent[0]["videos"][0]["lead_kind"] == "audio" and so.sent[0]["audio_leads_s"] == {"bt": 0.225}, so.sent[:1])
so = Captured()
r, end = run_show("", so, override="stage", key="stage")
so.wait_for("load")
check("a show's video on the stage plays CLEAN by default: no skeleton layer over it",
      so.sent[0]["skeletons"] is False and so.sent[0]["videos"][0]["lead_kind"] == "video", so.sent[:1])
so = Captured()
r, end = run_show("video: { skeletons: true }\n", so, override="stage", key="stage")
so.wait_for("load")
check("... and `video: {skeletons: true}` (Them Bones) draws the stage page's skeletons over it",
      end[0] == "COMPLETED" and so.sent[0]["skeletons"] is True, (end, so.sent[:1]))
check("the validator knows video: {skeletons} and wants true or false",
      "skeletons" in cue.VIDEO_KEYS and any("skeletons must be true or false" in q.msg for q in cue.show_from_text(
          "id: x\nslots: [VIDEO]\nvideo: { skeletons: maybe }\ncues:\n  - { t: 1, slot: VIDEO, do: play, video: clip }\n",
          os.path.join(LIB, "x.yaml")).analyse(cue.load_limits(LIMITS))["problems"]))
V.save_settings({"stage_audio_leads_s": {"bt": None}})
check("the stage's status carries what its page says of itself (screen, sound, power)",
      FakeStage().status().get("page") is None)

print("\nvideo: the stage video — the box's own copy, else streamed with a head start (choreo-stage.md)")


class PreStage(Captured):
    """The stage, with each page's head-start report (GET /api/stage's `preload`) and stalls."""
    def __init__(self, pages=1, buffered=0.0, ready=False, local=False):
        super().__init__(pages)
        self.pre = {"buffered_s": buffered, "need_s": 25.0, "ready": ready, "local": local}
        self.stalls = []

    def _get(self):
        sid = self.made.id if getattr(self, "made", None) else None
        return {"pages": self.n, "preload": [dict(self.pre, id=sid)] * self.n,
                "stalls": [dict(x, id=sid) for x in self.stalls]}


so = PreStage(buffered=12.0)
lost = []
sh = so.make_show([{"t": 0.0, "end_t": 40.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip", "sha256": "ab"}],
                  None, on_lost=lost.append, buffer_s=25)
sh.begin()
so.wait_for("load")
time.sleep(0.7)
check("load carries the head start and each video's library id and hash (the box's copy is found by them)",
      so.sent[0]["buffer_s"] == 25.0 and so.sent[0]["videos"][0]["lib"] == "clip"
      and so.sent[0]["videos"][0]["sha256"] == "ab", so.sent[0])
check("a page still buffering: not ready, saying how far it has got",
      not sh.ready() and sh.waiting_for() == "the stage video (buffering, 12 of 25 s)", sh.waiting_for())
so.pre = {"buffered_s": 0.0, "need_s": 25.0, "ready": True, "local": True}
time.sleep(0.7)
check("... a page playing the box's OWN copy is ready at once", sh.ready())
sh.go(lambda: 0.0)
so.stalls = [{"at_s": 31.5}]
time.sleep(2.4)
check("a page that runs dry mid-show: logged, the pages muted, and the tablet takes the music — never an abort",
      sh.stalled and lost and "ran out of video at 31.5 s" in lost[-1] and any(m["op"] == "mute" for m in so.sent),
      (sh.stalled, lost, [m["op"] for m in so.sent]))
sh.end("COMPLETED")
so = PreStage(local=True)
so.pre["ready"] = True
sh = so.make_show([{"t": 0.0, "end_t": 40.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}], None,
                  on_lost=lost.append)
sh.mute_on_stall = False                     # the runner's word: the stage box is the only device with the music
sh.begin()
so.wait_for("load")
sh.go(lambda: 0.0)
so.stalls = [{"at_s": -0.2}]
time.sleep(2.4)
check("... but with no other device holding the music, a stall never mutes the pages (2026-10-28: a stall 0.2 s "
      "before the start silenced the Boom for the whole show)",
      sh.stalled and not any(m["op"] == "mute" for m in so.sent), [m["op"] for m in so.sent])
sh.end("COMPLETED")
so = PreStage(buffered=10.0)
sh = so.make_show([{"t": 0.0, "end_t": 4.0, "path": f, "from_s": 0.0, "end": "black", "id": "clip"}], None,
                  on_lost=lost.append)
sh.begin()
so.wait_for("load")
sh.drop("buffering, 10 of 25 s after 90 s")
check("dropped: the pages are stopped, and the tablet is told to take the music",
      so.sent[-1]["op"] == "stop" and sh.ready() and "not loaded in time" in lost[-1], (so.sent[-1], lost))
got = V.save_settings({"stage_buffer_s": 30})
check("the head start is a setting, 5-120 s (projector.json, beside the leads)", got["stage_buffer_s"] == 30.0, got)
V.save_settings({"stage_buffer_s": 25})


def armed(so, required=False, press_at=4.0, plan=None):
    """An armed run (start: deadman) of a show with a stage video: the deadman pressed, let go, pressed."""
    tmp = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=LIB)
    tmp.write("id: v\nslots: [VIDEO, FACE]\nrequires: {tab5: true}\n"
              + ("video: { required: true }\n" if required else "")
              + "cues:\n  - { t: 1.0, slot: VIDEO, do: play, video: clip, secs: 3 }\n"
              "  - { t: 4.5, slot: FACE, do: emote, name: SOFT }\n")
    tmp.close()
    robot = R.FakeRobotState(deadman=False)
    clk = {"t": 1000.0}
    plan = plan or [(2.0, True), (3.0, False), (press_at, True)]

    def sleep(sec):
        clk["t"] += sec
        time.sleep(0.0005)                         # the stage's own thread polls in real time
        while plan and clk["t"] >= 1000.0 + plan[0][0]:
            robot.set(deadman=plan.pop(0)[1])
    log = io.StringIO()
    r = R.Runner(tmp.name, LIMITS, robot, log=log, clock=lambda: clk["t"], sleep=sleep, start_lead=0.5,
                 video={"stage": so}, video_override="stage", start="deadman")
    end = r.run()
    return r, end, [json.loads(x) for x in log.getvalue().splitlines()]


import json  # noqa: E402
r, end, lines = armed(PreStage(buffered=25.0, ready=True), plan=[(2.0, True)])
go = next((x for x in lines if x["kind"] == "go"), None)
check("armed, every page with its head start: the GO is taken, and the stage plays the show",
      end[0] == "COMPLETED" and go and not r.videos[0].dropped, (end, go))
r, end, lines = armed(PreStage(buffered=12.0), press_at=95.0)
notes = [x.get("note", "") for x in lines if x["kind"] == "status"]
check("armed, still buffering: the Shows page says 'buffering, 12 of 25 s', and a press is not a GO",
      any("buffering, 12 of 25 s" in n for n in notes)
      and any(x["kind"] == "deadman" and "not a GO" in x.get("note", "") for x in lines), notes[-3:])
check("... past the preload wait the stage is dropped, and the show runs without it, saying why",
      end[0] == "COMPLETED" and r.videos[0].dropped and "buffering" in (r.video_note or ""), (end, r.video_note))
r, end, _ = armed(PreStage(buffered=12.0), required=True, press_at=95.0)
check("... unless the show requires its video: refused, SHOW_VIDEO_NOT_READY, with how far it got",
      end[:2] == ("REFUSED", "SHOW_VIDEO_NOT_READY") and "12 of 25 s" in end[2], end)

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
