"""The Halloween listener: the Jabra speakerphone on the Thor is the demo runner's ears and voice.

    python3 -m opendubs_architect.listener                       # the Jabra, Parakeet, Kokoro, runner on :8098
    python3 -m opendubs_architect.listener --stt fake --tts fake # a desk with the Jabra and no models

It hears through the Jabra, spots keywords on the Thor (kws.py), and posts what it heard to the demo
runner's `POST /api/heard`. It speaks for the runner: `demo_runner --say-url
http://127.0.0.1:8099/api/say` sends each line here, and Kokoro plays it on the Jabra, whose own echo
canceller has the playback to subtract.

WHAT IT POSTS, and when. Every clip is transcribed by Parakeet before anything is posted, and a
spotter hit alone posts nothing.

    "Hey OpenDubs, <words>"   any state  the turn runs to the end of speech (vad.Endpointer), and
                                         wake_rest must find the phrase: "instruction" while the
                                         runner is IDLE, "speech" otherwise ("Hey OpenDubs, stop")
    "trick or treat", in any  not ARMED  dropped: it is never an instruction or a reply, so a child
     form                                saying it early never becomes a prompt
    "trick or treat"          ARMED      no wake phrase; the spotter's hit plus Parakeet's agreement
                                         on the same 2 s -> "trigger" (the runner still needs the deadman)
    a short reply             AWAITING_YES  no wake phrase; YES_MAX_WORDS words or fewer -> "speech",
                                         so the runner's yes/no reads it; a longer sentence is
                                         someone else talking and is dropped
    a sentence with "stop"    RUNNING    no wake phrase -> "speech" "stop"

Outside those states, and while the runner cannot be read, only the wake phrase is listened for.

WHILE THE ROBOT SPEAKS nothing is listened to, and for TAIL_S after: its own "trick or treat" in a
summary must not trigger it. The spotter starts clean afterwards.

NOTHING IS STORED. Audio lives in a 1.5 s ring buffer and the clip in hand; the log has events and
what was posted, not what was dropped.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "voice")
sys.path.insert(0, os.path.abspath(VOICE))     # the voice service's modules, used and not changed

import vad  # noqa: E402
import wakeword  # noqa: E402

from opendubs_architect import kws  # noqa: E402

FRAME_MS = 20
PRE_ROLL_MS = 1500          # kept before a hit: the phrase itself, for Parakeet's check
TRICK_TAIL_MS = 400         # after a "trick or treat" hit, so the last word is whole in the clip
PHRASE_MS = 1000            # "Hey OpenDubs" ends about this long before the spotter fires
OPEN_LEAD_MS = 800          # kept before Silero says speech has begun: it says so late, and a 300 ms
                            # lead cut "yes" and "no" to nothing on the second take
OPEN_END_SILENCE_MS = 800
OPEN_MAX_MS = 6000
TAIL_S = 0.6                # deaf after the robot speaks: the room's echo of it
YES_MAX_WORDS = 3
OPEN_STATES = ("AWAITING_YES", "RUNNING")
_WORD = re.compile(r"[A-Za-z']+")
_OR = {"or", "r", "a", "er", "ah", "and", "n"}
# How Parakeet wrote a real "Hey OpenDubs" on the Jabra at 1 m (the takes of 2026-10-24), which
# wakeword.strip refuses: "Hey, opened up", "Hey overdubs", "A open dubs", "They opened up", "I opened
# dubs", "Hey open dumps", "Hey Oaken Ducks", "Hey, open dogs". Accepted here only after a spotter hit
# or in an open window, never on their own: "hey dogs" without the "open" stays refused.
_JABRA_WAKE = re.compile(r"\b(?:hey|hay|hi|a|they|he|i)\b[\s,.!?]+(?:(?:open(?:ed)?|oaken)[\s,.]*(?:dubs?|dubbs|ducks?|dumps?|dogs?|up)"
                         r"|over\s*dubs?)\b[\s,.!?]*", re.I)
_TRICK_LIKE = re.compile(r"^[a-z]*(?:ick|eck|ik)$")    # "check or cheat", as Parakeet wrote a real one
_TREAT_LIKE = re.compile(r"^[a-z]*(?:eat|eet|eats)$")


def wake_rest(text: str) -> str | None:
    """wakeword.strip, plus the Jabra's own spellings of the phrase near the start."""
    rest = wakeword.strip(text)
    if rest is not None:
        return rest
    m = _JABRA_WAKE.search(text or "")
    if m and len(_WORD.findall(text[:m.start()])) < wakeword.WINDOW:
        return re.sub(r"^'s\b[\s,.]*", "", text[m.end():]).strip()    # "I opened up's wave hello."
    return None


def is_trick_or_treat(text: str) -> bool:
    """Parakeet's agreement: "trick or treat", in any of the ways it writes it."""
    w = [x.lower() for x in _WORD.findall(text or "")]
    for i in range(len(w) - 2):
        if w[i + 1] in _OR and _TRICK_LIKE.match(w[i]) and _TREAT_LIKE.match(w[i + 2]):
            return True                                   # the same rhyme: "check or cheat"
    for i in range(len(w) - 1):
        if wakeword._near(w[i], ("trigger",)) and wakeword._near(w[i + 1], ("treat", "treats")):
            return True                                   # "Trigger treat.", as Parakeet wrote a real one
        if not wakeword._near(w[i], ("trick",)):
            continue
        if wakeword._near(w[i + 1], ("treat", "treats")):
            return True                                   # the "or" swallowed
        if w[i + 1] in _OR and i + 2 < len(w) and wakeword._near(w[i + 2], ("treat", "treats")):
            return True
    return False


class Listener:
    """Transport-free: `frame` for each 20 ms of capture, `state` set from the runner, `speaking`
    while the robot talks. `transcribe(pcm) -> str` and `post(kind, text) -> str | None` (the
    runner's new state) reach the models and the runner."""

    def __init__(self, spotter, transcribe, post, make_vad=vad.make_vad, clock=time.monotonic,
                 log=None, background=True):
        self.spotter, self.transcribe, self.post = spotter, transcribe, post
        self.make_vad, self.clock, self._log_fn = make_vad, clock, log
        self.state: str | None = None         # the runner's, None while it cannot be read
        self.speaking = False
        self.quiet_until = 0.0
        self.ring = collections.deque(maxlen=PRE_ROLL_MS // FRAME_MS)
        self.turn = None                      # [kind, frames, endpointer or ms left, runner state]
        self.open = None                      # [frames, silence_ms] once speech starts in an open window
        self.open_vad = None
        self._deaf = False
        self.jobs: queue.Queue = queue.Queue()
        self.background = background
        if background:
            threading.Thread(target=self._work, daemon=True).start()

    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    def said(self) -> None:
        """The robot has finished a line."""
        self.speaking = False
        self.quiet_until = self.clock() + TAIL_S

    # ---- capture ----------------------------------------------------------------------------------
    def frame(self, pcm: bytes) -> None:
        if self.speaking or self.clock() < self.quiet_until:
            if not self._deaf:
                self._deaf = True
                self.turn = self.open = self.open_vad = None
                self.ring.clear()
            return
        if self._deaf:
            self._deaf = False
            self.spotter.reset()
        self.ring.append(pcm)
        hits = self.spotter.feed(pcm)           # every frame: its streaming decoder does not survive a gap
        if kws.HEY in hits:
            self._log("spotted", keyword=kws.HEY)
            if self.turn is not None:           # a new "Hey OpenDubs" ends the turn in hand, before it
                kind, frames, _, state = self.turn
                keep = frames[:max(0, len(frames) - PHRASE_MS // FRAME_MS)] if kind == "wake" else frames
                if keep:
                    self._submit(kind, b"".join(keep), state)
            frames = list(self.ring)
            ep = vad.Endpointer(self.make_vad(), pre_roll_ms=len(frames) * FRAME_MS)
            for f in frames:
                ep.feed(f)
            self.turn, self.open = ["wake", frames, ep, self.state], None
            return
        if self.turn is not None:
            self._continue_turn(pcm)
            return
        if kws.TRICK in hits:
            self._log("spotted", keyword=kws.TRICK, state=self.state)
            if self.state == "ARMED":
                self.turn, self.open = ["trick", list(self.ring), TRICK_TAIL_MS, self.state], None
                return
        self._open_window(pcm)

    def _continue_turn(self, pcm: bytes) -> None:
        kind, frames, rest, state = self.turn
        frames.append(pcm)
        if kind == "wake":
            end = rest.feed(pcm)
            if end == "no_speech":
                self._log("not_addressed", why="nothing said after the wake word")
            if end:
                self.turn = None
                if end == "end":
                    self._submit(kind, b"".join(frames), state)
        else:
            self.turn[2] = rest - FRAME_MS
            if self.turn[2] <= 0:
                self.turn = None
                self._submit(kind, b"".join(frames), state)

    def _open_window(self, pcm: bytes) -> None:
        if self.state not in OPEN_STATES:
            self.open = self.open_vad = None
            return
        if self.open_vad is None:
            self.open_vad = self.make_vad()
        speech = self.open_vad.speech(pcm)      # fed every frame, so a model keeps its state
        if self.open is None:
            if speech:
                self.open = [list(self.ring)[-(OPEN_LEAD_MS // FRAME_MS):], 0]
            return
        frames = self.open[0]
        frames.append(pcm)
        self.open[1] = 0 if speech else self.open[1] + FRAME_MS
        if self.open[1] >= OPEN_END_SILENCE_MS or len(frames) * FRAME_MS >= OPEN_MAX_MS:
            self.open = None
            self._submit("open", b"".join(frames), self.state)

    # ---- deciding ---------------------------------------------------------------------------------
    def _submit(self, kind: str, clip: bytes, state) -> None:
        if self.background:
            self.jobs.put((kind, clip, state))
        else:
            self._decide(kind, clip, state)

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self._decide(*job)
            except Exception as e:  # noqa: BLE001  one bad clip never stops the listener
                self._log("job_failed", kind=job[0], error=str(e))

    def _decide(self, kind: str, clip: bytes, state) -> None:
        text = self.transcribe(clip)
        if kind == "wake":
            rest = wake_rest(text)
            if not rest:
                self._log("not_addressed", why="no wake phrase in the transcript" if rest is None
                          else "only the wake phrase was said")
                return
            if is_trick_or_treat(rest):             # never an instruction or a reply: a trigger or nothing
                if self.state == "ARMED":
                    self._send("trigger", "trick or treat")
                else:
                    self._log("dropped", state=self.state, why="trick or treat while nothing is armed")
            else:
                self._send("instruction" if self.state == "IDLE" else "speech", rest)
        elif kind == "trick":
            if is_trick_or_treat(text):
                self._send("trigger", "trick or treat")
            else:
                self._log("not_confirmed", keyword=kws.TRICK)
        else:
            # the phrase does not count as words of the reply: "Hey OpenDubs, yes" over music, which the
            # spotter missed, is still a yes
            words = [w.lower() for w in _WORD.findall(wake_rest(text) or text)]
            if is_trick_or_treat(text):             # an open window is never ARMED
                self._log("dropped", state=state, why="trick or treat while nothing is armed")
            elif state == "AWAITING_YES" and 0 < len(words) <= YES_MAX_WORDS:
                self._send("speech", text)
            elif state == "RUNNING" and "stop" in words:
                self._send("speech", "stop")
            else:
                self._log("dropped", state=state, words=len(words))

    def _send(self, kind: str, text: str) -> None:
        got = self.post(kind, text)
        if got:
            self.state = got
        self._log("posted", kind=kind, text=text, state=got)


# ---- the robot side: the Jabra, the models, the runner ------------------------------------------------
class Runner:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def state(self) -> str | None:
        try:
            with urllib.request.urlopen(self.url + "/api/state", timeout=1.0) as r:
                return json.load(r).get("state")
        except (OSError, ValueError):
            return None

    def post(self, kind: str, text: str) -> str | None:
        req = urllib.request.Request(self.url + "/api/heard", data=json.dumps({"kind": kind, "text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as r:
                return json.load(r).get("state")
        except (OSError, ValueError):
            return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runner", default="http://127.0.0.1:8098", help="the demo runner")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099, help="POST /api/say {text}: the robot speaks it")
    ap.add_argument("--card", default="Jabra", help="the sound card, by a word of its name")
    ap.add_argument("--models", default=os.environ.get("OPENDUBS_VOICE_MODELS", "/var/lib/opendubs/models"))
    ap.add_argument("--stt", default=os.environ.get("VOICE_STT", "parakeet"))
    ap.add_argument("--tts", default=os.environ.get("VOICE_TTS", "kokoro"))
    a = ap.parse_args(argv)
    os.environ["OPENDUBS_VOICE_MODELS"] = a.models      # speech.py and vad.py read it

    from opendubs_architect import alsa
    import speech

    def log(ev):
        print(json.dumps(ev), flush=True)

    stt, tts = speech.make_stt(a.stt), speech.make_tts(a.tts)
    runner = Runner(a.runner)
    listener = Listener(kws.Spotter(a.models), lambda pcm: asyncio.run(stt.transcribe(pcm)).text,
                        runner.post, make_vad=lambda: vad.make_vad(a.models), log=log)

    lines: queue.Queue = queue.Queue()

    async def play(out, text):
        async for chunk in tts.speak(text):
            out.write(chunk)

    def speaker():
        out = None
        while True:
            text = lines.get()
            listener.speaking = True
            try:
                out = out or alsa.Playback(alsa.find_card(a.card))
                asyncio.run(play(out, text))
                out.drain()
                log({"t": round(time.time(), 3), "event": "said", "text": text})
            except Exception as e:  # noqa: BLE001  a lost device is opened again on the next line
                log({"t": round(time.time(), 3), "event": "say_failed", "error": str(e)})
                if out:
                    out.close()
                out = None
            finally:
                listener.said()

    def poll():
        while True:
            listener.state = runner.state()
            time.sleep(0.3)

    def capture():
        while True:
            try:
                cap = alsa.Capture(alsa.find_card(a.card))
                log({"t": round(time.time(), 3), "event": "capture_open", "device": cap.device})
                while True:
                    listener.frame(cap.read())
            except alsa.DeviceError as e:
                log({"t": round(time.time(), 3), "event": "capture_lost", "error": str(e)})
                time.sleep(1.0)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/api/say":
                self.send_response(404)
                self.end_headers()
                return
            try:
                text = str(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))["text"])
            except (ValueError, KeyError, TypeError):
                self.send_response(422)
                self.end_headers()
                return
            lines.put(text[:500])
            self.send_response(202)
            self.end_headers()

    try:
        srv = ThreadingHTTPServer((a.host, a.port), H)
    except OSError as e:
        sys.exit(f"listener: port {a.port} is taken ({e}); another listener may be running. Leave it, or pick --port")
    for fn in (speaker, poll, capture):
        threading.Thread(target=fn, daemon=True).start()
    log({"t": round(time.time(), 3), "event": "listening", "say": f"http://{a.host}:{a.port}/api/say", "runner": a.runner})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
