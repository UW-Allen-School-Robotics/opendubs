"""Show music on the Jabra: the listener holds the dashboard's show-audio role, so a show's music plays
from the robot's speakerphone instead of the head tablet (whose screen can go dark mid-show, #590).

    listener --show ROUTINE --show-audio          # dev.sh: SHOW_AUDIO=1 (the boot unit sets it)

THE ROLE (software/kiosk-dashboard/backend/show_audio.py, the Choreography lane's; this is one more
client of it, and changes nothing there). One device plays a show's music: the last to claim the role
on /ws/show-audio. This claims it on connecting; the tablet claims it on start-up only when nobody holds
it, so it does not take it back. A person pressing "Play show audio here" on a page takes it, and this
does not take it back either: it plays nothing until it holds the role again.

The runner's commands, relayed by the hub to the holder:
  load   {id, items: [{url, at, held?}]}  download and decode every file, then say {ready: id}
  start  {id, t0_mono}                     show time 0 is t0_mono on the Thor's CLOCK_MONOTONIC. This runs
                                           on the Thor, so it is this process's time.monotonic(): no clock
                                           sync is needed (the pings a browser sends are for its own clock)
  unhold {id}                              a held item (the projector's music) joins in step
  stop   {fade_s}                          fade out; cut: silence now
A lost connection stops the music and nothing else (the hub's DEC-7). Ambient music, files sent from the
/play/ page and AirPlay are not played here: they stay the tablet's, when it holds the role.

THE OUTPUT (Mixer). One stream to the Jabra, written continuously in 20 ms blocks at 24 kHz mono (the
listener's speech rate): the show's music, placed by show time, and the robot's speech over it, the
music ducked while the robot talks. Each block is placed at the time it will be heard (now plus the
device's buffer, alsa.LATENCY_US), so the music starts on the show's beat to within a few tens of ms.

DECODING: miniaudio (MIT; its MP3 decoder is dr_mp3, public domain / MIT-0). Not ffmpeg: the distro's
build carries GPL parts, and this image keeps out of GPL code (image/speech_no_gpl.py). MP3, WAV, FLAC
and Vorbis; a show whose music is in another format (AAC/M4A) is not made ready, and the runner plays
it silently with its own warning, as it does for any device that is not ready in time.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import urllib.parse
import urllib.request

import numpy as np

from opendubs_architect import alsa

RATE = alsa.PLAY_RATE                 # 24 kHz: the listener's speech rate, so speech needs no resampling
BLOCK = RATE // 50                    # 20 ms
AHEAD_S = alsa.LATENCY_US / 1e6       # a block written now is heard about this much later
MUSIC_GAIN = 0.7                      # the music under nothing
DUCK_GAIN = 0.3                       # the music while the robot speaks
DASHBOARD = "http://127.0.0.1:8080"


class Track:
    def __init__(self, pcm: np.ndarray, at: float, held: bool = False, url: str = ""):
        self.pcm, self.at, self.held, self.url = pcm, float(at), bool(held), url


class Mixer:
    """The Jabra's one output. `say(pcm)` queues speech (s16le mono 24 kHz bytes) and returns an Event
    set once it has been written; `play(tracks, t0)`, `fade(s)`, `cut()`, `unhold()` drive the music.
    `block(heard_t)` is the pure part: the 20 ms that will be heard at monotonic time `heard_t`."""

    def __init__(self, card: str | None = None, log=None, clock=time.monotonic):
        self.card, self._log_fn, self.clock = card, log, clock
        self.lock = threading.Lock()
        self.tracks: list[Track] = []
        self.t0 = None                       # show time 0, monotonic seconds
        self.fade_from = self.fade_until = None
        self.speech: queue.Queue = queue.Queue()
        self.now_speech = None               # [samples, position, Event]

    # ---- the music ----------------------------------------------------------------------------
    def play(self, tracks: list, t0: float) -> None:
        with self.lock:
            self.tracks, self.t0, self.fade_from, self.fade_until = list(tracks), float(t0), None, None

    def unhold(self) -> int:
        with self.lock:
            n = sum(1 for t in self.tracks if t.held)
            for t in self.tracks:
                t.held = False
            return n

    def fade(self, seconds: float) -> None:
        with self.lock:
            if self.t0 is None:
                return
            if seconds <= 0:
                self.tracks, self.t0 = [], None
            else:
                now = self.clock() + AHEAD_S
                self.fade_from, self.fade_until = now, now + seconds

    def cut(self) -> None:
        self.fade(0)

    @property
    def playing(self) -> bool:
        return self.t0 is not None

    # ---- speech -------------------------------------------------------------------------------
    def say(self, pcm: bytes) -> threading.Event:
        done = threading.Event()
        self.speech.put((np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16), done))
        return done

    # ---- one block ----------------------------------------------------------------------------
    def block(self, heard_t: float) -> np.ndarray:
        out = np.zeros(BLOCK, dtype=np.float32)
        if self.now_speech is None:
            try:
                s, done = self.speech.get_nowait()
                self.now_speech = [s, 0, done]
            except queue.Empty:
                pass
        talking = self.now_speech is not None
        with self.lock:
            if self.t0 is not None:
                gain = DUCK_GAIN if talking else MUSIC_GAIN
                if self.fade_until is not None:
                    if heard_t >= self.fade_until:
                        self.tracks, self.t0, self.fade_from, self.fade_until = [], None, None, None
                        gain = 0.0
                    else:
                        gain *= max(0.0, (self.fade_until - heard_t) / (self.fade_until - self.fade_from))
                show_t = heard_t - self.t0 if self.t0 is not None else None
                for t in self.tracks if show_t is not None else []:
                    if t.held:
                        continue
                    start = int(round((show_t - t.at) * RATE))
                    lo, hi = max(start, 0), min(start + BLOCK, len(t.pcm))
                    if hi > lo:
                        out[lo - start: hi - start] += t.pcm[lo:hi].astype(np.float32) * gain
        if talking:
            s, pos, done = self.now_speech
            n = min(BLOCK, len(s) - pos)
            out[:n] += s[pos: pos + n].astype(np.float32)
            self.now_speech[1] = pos + n
            if self.now_speech[1] >= len(s):
                done.set()
                self.now_speech = None
        return np.clip(out, -32768, 32767).astype(np.int16)

    # ---- the device ---------------------------------------------------------------------------
    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    def run(self) -> None:
        """Write blocks to the Jabra for ever; a lost device is opened again by name."""
        while True:
            try:
                out = alsa.Playback(alsa.find_card(self.card or "Jabra"), rate=RATE)
                self._log("music_out_open", device=out.device)
                while True:
                    out.write(self.block(self.clock() + AHEAD_S).tobytes())
            except alsa.DeviceError as e:
                self._log("music_out_lost", error=str(e))
                if self.now_speech is not None:           # never leave a speaker waiting on a lost line
                    self.now_speech[2].set()
                    self.now_speech = None
                time.sleep(1.0)

    def start(self) -> "Mixer":
        threading.Thread(target=self.run, daemon=True).start()
        return self


def decode(data: bytes) -> np.ndarray:
    import miniaudio
    d = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=RATE)
    return np.frombuffer(d.samples, dtype=np.int16).copy()


def media_url(url: str, dashboard: str = DASHBOARD) -> str:
    """The hub's item path as a URL urllib accepts: song files have spaces ("14 - Ghostbusters.mp3"),
    which a browser escapes by itself and urllib refuses (found on opendubs2)."""
    full = url if url.startswith("http") else dashboard.rstrip("/") + url
    return urllib.parse.quote(full, safe=":/?&=%#")


def fetch(url: str, dashboard: str = DASHBOARD) -> bytes:
    """A show's file, from the dashboard's own media route (the hub's items are same-origin paths)."""
    with urllib.request.urlopen(media_url(url, dashboard), timeout=30) as r:
        return r.read()


class ShowAudio:
    """The show-audio role's client. Transport-free at its core: `handle(msg)` acts on one message from
    the hub and returns what to send back (or None). `fetch(url) -> bytes` and `decode(bytes) -> pcm`
    are seams; `run()` is the WebSocket, reconnecting."""

    def __init__(self, mixer: Mixer, dashboard: str = DASHBOARD, log=None, fetch_fn=None, decode_fn=decode,
                 label: str = "Jabra"):
        self.mixer, self.dashboard, self._log_fn, self.label = mixer, dashboard.rstrip("/"), log, label
        self.fetch = fetch_fn or (lambda url: fetch(url, self.dashboard))
        self.decode = decode_fn
        self.holds, self.load_id, self.tracks, self.ready_id = False, None, [], None

    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    def hello(self) -> dict:
        return {"claim": True, "label": self.label}

    def handle(self, m: dict):
        if "pong" in m:
            return None
        if "holder" in m:
            held, self.holds = self.holds, bool(m.get("you_hold"))
            if held and not self.holds:              # someone else plays show audio now
                self.mixer.cut()
                self._log("show_audio_role", holds=False, holder=m.get("holder"))
            elif self.holds and not held:
                self._log("show_audio_role", holds=True)
            return None
        op = m.get("op")
        if op == "load":
            return self._load(m)
        if op == "start":
            if m.get("id") != self.load_id or self.ready_id != self.load_id:
                self._log("show_audio_skipped", why="start for a show that is not loaded here", id=m.get("id"))
                return None
            self.mixer.play(self.tracks, float(m["t0_mono"]))
            self._log("show_audio_start", id=m.get("id"), late_s=round(time.monotonic() - float(m["t0_mono"]), 3))
            return None
        if op == "unhold" and m.get("id") == self.load_id:
            self._log("show_audio_unhold", n=self.mixer.unhold())
            return None
        if op == "stop":
            self.mixer.fade(float(m.get("fade_s") or 0))
            self._log("show_audio_stop", fade_s=m.get("fade_s"))
            return None
        if op == "cut":
            self.mixer.cut()
            self._log("show_audio_cut")
            return None
        return None                                  # ambient, play_file, airplay_gate: the tablet's

    def _load(self, m: dict):
        self.mixer.cut()
        self.load_id, self.ready_id, self.tracks = m.get("id"), None, []
        try:
            tracks = []
            for it in m.get("items") or []:
                tracks.append(Track(self.decode(self.fetch(it["url"])), it.get("at", 0.0), it.get("held", False),
                                    it["url"]))
        except Exception as e:  # noqa: BLE001  not ready: the runner waits, then plays silently, and says so
            self._log("show_audio_load_failed", id=m.get("id"), error=str(e)[:200])
            return None
        if self.load_id != m.get("id"):
            return None                              # superseded while decoding
        self.tracks, self.ready_id = tracks, m.get("id")
        self._log("show_audio_ready", id=m.get("id"), items=[t.url.rsplit("/", 1)[-1] for t in tracks],
                  seconds=[round(len(t.pcm) / RATE, 1) for t in tracks])
        return {"ready": m.get("id")}

    async def _session(self):
        import websockets
        url = self.dashboard.replace("http", "ws", 1) + "/ws/show-audio"
        async with websockets.connect(url, max_size=None) as ws:
            self._log("show_audio_connected", url=url)
            await ws.send(json.dumps(self.hello()))
            loop = asyncio.get_running_loop()
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                # a load downloads and decodes: off the loop, so the hub's other messages are not held up
                reply = await loop.run_in_executor(None, self.handle, m) if m.get("op") == "load" else self.handle(m)
                if reply is not None:
                    await ws.send(json.dumps(reply))

    def run(self) -> None:
        retry = 0.5
        while True:
            try:
                asyncio.run(self._session())
                retry = 0.5
            except Exception as e:  # noqa: BLE001  the dashboard restarting, or not up yet
                self._log("show_audio_lost", error=str(e)[:200])
            self.holds = False
            self.mixer.cut()                         # DEC-7: a lost connection stops the music, nothing else
            time.sleep(retry)
            retry = min(retry * 2, 8.0)

    def start(self) -> "ShowAudio":
        threading.Thread(target=self.run, daemon=True).start()
        return self
