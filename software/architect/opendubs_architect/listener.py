"""The Halloween listener: the Jabra speakerphone on the Thor is the demo runner's ears and voice.

    python3 -m opendubs_architect.listener                       # the Jabra, Parakeet, Kokoro, runner on :8098
    python3 -m opendubs_architect.listener --stt fake --tts fake # a desk with the Jabra and no models

It hears through the Jabra, spots keywords on the Thor (kws.py), and posts what it heard to the demo
runner's `POST /api/heard`. With `--show ROUTINE` it drives the show runner instead (Shows, below):
"trick or treat" plays one routine from the robot's show palette, through the dashboard, with the
Shows page's own gates; no ARCHITECT program, no approval, the supervisor's deadman press the go. It speaks for the runner: `demo_runner --say-url
http://127.0.0.1:8099/api/say` sends each line here, and Kokoro plays it on the Jabra, whose own echo
canceller has the playback to subtract.

WHAT IT POSTS, and when. Every clip is transcribed by Parakeet before anything is posted, and a
spotter hit alone posts nothing.

    "OpenDubs, <words>"       any state  the turn runs to the end of speech (vad.Endpointer), and
                                         wake_rest must find the phrase: "instruction" while the
                                         runner is IDLE, "speech" otherwise ("OpenDubs, stop").
                                         The wake phrase is "OpenDubs" (kws.WAKE); "Hey OpenDubs"
                                         is still heard
    "trick or treat", in any  not ARMED  never an instruction or a reply, so a child saying it early
     form                                never becomes a prompt; the robot says a HOLD_ON line
    "trick or treat"          ARMED      no wake phrase; the spotter's hit plus Parakeet's agreement
                                         on the same 2 s -> "trigger" (the runner still needs the deadman)
    a short reply             AWAITING_YES  no wake phrase; YES_MAX_WORDS words or fewer -> "speech",
                                         so the runner's yes/no reads it; a longer sentence is
                                         someone else talking and is dropped
    a sentence with "stop"    RUNNING    no wake phrase -> "speech" "stop"

WHAT IT SAYS ITSELF (chat.py, the robot's local language model; never posted to the runner):

    a run starts              RUNNING    one varied greeting while the arm hands out the treat
    "trick or treat" that     any but    "Hold on, I'm thinking!" and its variations: a run is under
     starts no run            AWAITING_YES  way, or the runner refused it (the deadman not held)
    "OpenDubs, <words>"       ARMED      a short reply, unless the words hold "stop" (posted as above)
    "OpenDubs, dance" and     ARMED,     with --day (demo_day.py, the demo day's coordinator): asked
     other show requests      --day      of the coordinator, which picks and arms the show

Outside those states, and while the runner cannot be read, only the wake phrase is listened for.

WITH --day, "trick or treat" asks the coordinator first (POST /api/request {"want": "candy"}): it is
granted only once the base has stopped, and refused while another show runs or the demo is paused.
A refusal is a HOLD_ON line, as a refused run is. Without the coordinator answering, nothing starts.

WHILE THE ROBOT SPEAKS nothing is listened to, and for TAIL_S after: its own "trick or treat" in a
summary must not trigger it. The spotter starts clean afterwards.

NOTHING IS STORED. Audio lives in a 1.5 s ring buffer and the clip in hand; the log has events and
what was posted, not what was dropped.
"""
from __future__ import annotations

import argparse
import array
import asyncio
import collections
import concurrent.futures
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VOICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "voice")
sys.path.insert(0, os.path.abspath(VOICE))     # the voice service's modules, used and not changed

import vad  # noqa: E402
import wakeword  # noqa: E402

from opendubs_architect import kws  # noqa: E402

FRAME_MS = 20
PRE_ROLL_MS = 2500          # kept before a hit: the phrase itself, for Parakeet's check. 1.5 s lost the
                            # "trick" of a slow "trick ... or treat" on the robot: Parakeet heard "or treat."
TRICK_TAIL_MS = 400         # after a "trick or treat" hit, so the last word is whole in the clip
PHRASE_MS = 800             # "OpenDubs" ends about this long before the spotter fires
WAKE_MAX_MS = 5000          # a wake turn ends this long after the phrase, silence or not: with people
                            # talking nearby the 0.8 s of silence never came, and one turn ran 19.6 s
_STAGES = ("light", "posted", "greeted", "replied", "held", "not_confirmed", "not_addressed", "dropped",
           "turn_capped")   # events the page shows with their time after the spotter's hit
OPEN_LEAD_MS = 800          # kept before Silero says speech has begun: it says so late, and a 300 ms
                            # lead cut "yes" and "no" to nothing on the second take
OPEN_END_SILENCE_MS = 800
OPEN_MAX_MS = 6000
TAIL_S = 0.6                # deaf after the robot speaks: the room's echo of it
YES_MAX_WORDS = 3
HOLD_ON = ("Hold on, I'm thinking!", "One moment, let me think.", "Hmm, give me a second.",
           "Just a moment, I'm thinking it over.", "Hang on, my circuits are whirring!",
           "Wait a second, I'm thinking.", "Hold your horses, I'm thinking!", "Give me a moment to think.")
HOLD_ON_EVERY_S = 4.0
LIGHT_RGB = (150, 0, 255)   # purple: Halloween, and no state's colour (white, green, cyan, amber, red)
LIGHT_S = 2.0               # the dashboard's longest lease: it lapses by itself, nothing to release       # a crowd shouting "trick or treat" hears one, not a queue of them
PEAK_DBFS = -3.0            # each spoken line is brought up to this: Kokoro's own peaks near -19 dBFS
MAX_GAIN = 8.0              # were not heard through the Jabra at its volume 6 of 11 (2026-10-24)
OPEN_STATES = ("AWAITING_YES", "RUNNING")
_WORD = re.compile(r"[A-Za-z']+")
_STOP = re.compile(r"\bstop\b", re.I)
_OR = {"or", "r", "a", "er", "ah", "and", "n"}
# How Parakeet wrote a real "Hey OpenDubs" on the Jabra at 1 m (the takes of 2026-10-24), which
# wakeword.strip refuses: "Hey, opened up", "Hey overdubs", "A open dubs", "They opened up", "I opened
# dubs", "Hey open dumps", "Hey Oaken Ducks", "Hey, open dogs". The wake phrase is "OpenDubs" alone
# since 2026-10-26, so the leading word is optional; "opened up" alone counts only at the very start of
# what was heard, never in a sentence ("it opened up"). Accepted only after a spotter hit or in an open
# window, never on their own: "hey dogs" without the "open" stays refused.
_LEAD = r"(?:\b(?:hey|hay|hi|a|they|he|i)\b[\s,.!?]+)"
_JABRA_WAKE = re.compile(rf"{_LEAD}?(?:(?:open(?:ed)?|oaken)[\s,.]*(?:dubs?|dubbs|ducks?|dumps?|dogs?)|over\s*dubs?)\b[\s,.!?]*"
                         rf"|{_LEAD}opened\s+up\b[\s,.!?]*|^\W*opened\s+up\b[\s,.!?]*", re.I)
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


def loud(pcm: bytes) -> bytes:
    """A spoken line with its peak at PEAK_DBFS, raised by at most MAX_GAIN, never lowered."""
    import array
    a = array.array("h")
    a.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    peak = max((abs(x) for x in a), default=0)
    if not peak:
        return pcm
    g = min(MAX_GAIN, 32767 * 10 ** (PEAK_DBFS / 20) / peak)
    if g <= 1.0:
        return pcm
    return array.array("h", (max(-32768, min(32767, int(x * g))) for x in a)).tobytes()


def is_trick_or_treat(text: str) -> bool:
    """Parakeet's agreement: "trick or treat", in any of the ways it writes it. Asked only after the
    spotter has matched the whole phrase, so the phrase's own end is enough: "or treat" (a clip that
    began on its second word), but never "a treat", which is everyday speech."""
    w = [x.lower() for x in _WORD.findall(text or "")]
    if len(w) >= 2 and w[0] == "or" and wakeword._near(w[1], ("treat", "treats")):
        return True
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


_SHOW_ASK = re.compile(r"\b(dance|dancing|show|song|music|perform|monster|ghostbusters|ghost busters|bones)\b", re.I)


def wants_show(text: str) -> bool:
    """"OpenDubs, dance", "... play the monster mash": a request for one of the demo day's shows."""
    return bool(_SHOW_ASK.search(text or "")) and not _STOP.search(text or "")


def _not_armed(state) -> str:
    return "trick or treat while a run is under way" if state == "RUNNING" else \
        f"trick or treat while nothing is armed ({state})"


class Feed:
    """The last N events, in memory only, for the listener's page (GET /, GET /api/feed). It holds
    what Parakeet wrote for each clip it was given, which the log does not; nothing is written to disk."""

    def __init__(self, n: int = 200):
        self.lock, self.items, self.seq = threading.Lock(), collections.deque(maxlen=n), 0

    def add(self, ev: dict) -> None:
        with self.lock:
            self.seq += 1
            self.items.append(dict(ev, seq=self.seq))

    def since(self, seq: int) -> list:
        with self.lock:
            return [e for e in self.items if e["seq"] > seq]


class Listener:
    """Transport-free: `frame` for each 20 ms of capture, `state` set from the runner, `speaking`
    while the robot talks. `transcribe(pcm) -> str` and `post(kind, text) -> str | None` (the
    runner's new state) reach the models and the runner."""

    def __init__(self, spotter, transcribe, post, make_vad=vad.make_vad, clock=time.monotonic,
                 log=None, background=True, chat=None, say=None, log_heard=False, light=None, feed=None,
                 day=None):
        """`chat` (chat.Chat) and `say(text)`, when given, let the robot greet each visitor and answer
        questions while armed; without them it only listens for the runner."""
        self.spotter, self.transcribe, self.post = spotter, transcribe, post
        self.chat, self.say = chat, say
        self.light = light                      # Light: the base strip shows that it heard
        self.feed = feed                        # Feed: the page's events, transcripts included
        self.day = day                          # Day: the demo day's coordinator, for spoken show requests
        self.level_db = -120.0                  # the microphone's recent peak, for the page
        self._hit_at = None                     # when the spotter last fired (self.clock), for the timings
        self.log_heard = log_heard              # tuning only: what Parakeet wrote for a refused hit
        self._held_at, self._held_line = -1e9, None
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

    def _after(self) -> dict:
        return {"after_s": round(self.clock() - self._hit_at, 2)} if self._hit_at is not None else {}

    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw,
                          **(self._after() if event in _STAGES else {})})

    def said(self) -> None:
        """The robot has finished a line."""
        self.speaking = False
        self.quiet_until = self.clock() + TAIL_S

    # ---- capture ----------------------------------------------------------------------------------
    def frame(self, pcm: bytes) -> None:
        if self.feed is not None:               # the page's level meter: a peak that falls 1.5 dB a frame
            a = array.array("h")
            a.frombytes(pcm[:len(pcm) - len(pcm) % 2])
            peak = max((abs(v) for v in a), default=0)
            db = 20 * math.log10(peak / 32768) if peak else -120.0
            self.level_db = max(db, self.level_db - 1.5)
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
        if hits:
            self._hit_at = self.clock()
        if kws.TRICK in hits and self.turn is not None and self.turn[0] == "wake":
            # "OpenDubs, trick or treat": the spotter has the phrase, so the turn need not wait for silence
            self._log("spotted", keyword=kws.TRICK, state=self.state, within="wake turn")
            self.turn = ["wake_tail", self.turn[1] + [pcm], TRICK_TAIL_MS, self.turn[3]]
            return
        if kws.WAKE in hits:
            self._log("spotted", keyword=kws.WAKE)
            if self.turn is not None:           # a new "OpenDubs" ends the turn in hand, before it
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
            if self.state is not None:          # confirmed in any state: a run, or a HOLD_ON line
                self.turn, self.open = ["trick", list(self.ring), TRICK_TAIL_MS, self.state], None
                return
        self._open_window(pcm)

    def _continue_turn(self, pcm: bytes) -> None:
        kind, frames, rest, state = self.turn
        frames.append(pcm)
        if kind == "wake":
            end = rest.feed(pcm)
            if not end and rest.heard_ms - rest.pre_roll_ms >= WAKE_MAX_MS:
                end = "end"
                self._log("turn_capped", seconds=WAKE_MAX_MS / 1000)
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
                self._submit("wake" if kind == "wake_tail" else kind, b"".join(frames), state)

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
        if self.feed is not None:               # the page shows every transcript; the log only what was posted
            self.feed.add({"t": round(time.time(), 3), "event": "heard", "kind": kind, "text": text,
                           **self._after()})
        if kind == "wake":
            rest = wake_rest(text)
            if rest is not None:                    # the phrase confirmed, with or without words after it
                self._flash()
            if not rest:
                self._log("not_addressed", why="no wake phrase in the transcript" if rest is None
                          else "only the wake phrase was said")
                return
            if is_trick_or_treat(rest):             # never an instruction or a reply: a trigger or nothing
                if self.state == "ARMED":
                    self._send("trigger", "trick or treat")
                else:
                    self._log("dropped", state=self.state, why=_not_armed(self.state))
                    self._hold_on()
            elif self.day is not None and self.state == "ARMED" and wants_show(rest):
                ok, why = self.day.request("choreo", rest)
                self._log("show_asked", text=rest, ok=ok, why=why)
                if not ok:
                    self._hold_on()
            elif self.state == "ARMED" and self.chat and self.say and not _STOP.search(rest):
                answer = self.chat.reply(rest)      # a question while armed: the runner ignores it anyway
                self.say(answer)
                self._log("replied", text=answer)
            else:
                self._send("instruction" if self.state == "IDLE" else "speech", rest)
        elif kind == "trick":
            if is_trick_or_treat(text):
                self._flash()                       # heard, whether or not a run can start now
            if is_trick_or_treat(text) and self.state == "ARMED":
                self._send("trigger", "trick or treat")
            elif is_trick_or_treat(text):
                self._log("dropped", state=self.state, why=_not_armed(self.state))
                self._hold_on()
            else:
                self._log("not_confirmed", keyword=kws.TRICK, **({"heard": text} if self.log_heard else {}))
        else:
            # the phrase does not count as words of the reply: "Hey OpenDubs, yes" over music, which the
            # spotter missed, is still a yes
            words = [w.lower() for w in _WORD.findall(wake_rest(text) or text)]
            if is_trick_or_treat(text):             # an open window is never ARMED
                self._log("dropped", state=state, why=_not_armed(state))
                self._hold_on()
            elif state == "AWAITING_YES" and 0 < len(words) <= YES_MAX_WORDS:
                self._send("speech", text)
            elif state == "RUNNING" and "stop" in words:
                self._send("speech", "stop")
            else:
                self._log("dropped", state=state, words=len(words))

    def _send(self, kind: str, text: str) -> None:
        # The greeting is written while the run is being started (the dashboard's start took 1 to 1.5 s
        # on opendubs2, the model 0.3 s), and spoken only once it has: a refused start never hears it.
        greeting = None
        if kind == "trigger" and self.chat and self.say:
            greeting = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            line_f = greeting.submit(self.chat.greeting)
            greeting.shutdown(wait=False)
        got = self.post(kind, text)
        if got:
            self.state = got
        self._log("posted", kind=kind, text=text, state=got)
        if greeting and got == "RUNNING":
            line = line_f.result()                  # the run has started: it never waited on this
            self.say(line)
            self._log("greeted", text=line)
        elif kind == "trigger" and got != "RUNNING":
            self._hold_on()                         # refused: the deadman not held, or not yet armed

    def _flash(self) -> None:
        if self.light is not None:
            self._log("light", ok=self.light.flash())

    def status(self) -> dict:
        return {"state": self.state, "deaf": bool(self.speaking or self.clock() < self.quiet_until),
                "speaking": self.speaking, "level_db": round(self.level_db, 1)}

    def _hold_on(self) -> None:
        """A "trick or treat" that started no run gets a word, not silence, at most every HOLD_ON_EVERY_S.
        Not while the runner waits for a yes: it would talk over the approval."""
        if not self.say or self.state == "AWAITING_YES" or self.clock() - self._held_at < HOLD_ON_EVERY_S:
            return
        line = random.choice([x for x in HOLD_ON if x != self._held_line])
        self._held_at, self._held_line = self.clock(), line
        self.say(line)
        self._log("held", text=line)


# ---- the robot side: the Jabra, the models, the runner ------------------------------------------------
class Runner:
    """The ARCHITECT demo runner (demo_runner.py): "trick or treat" runs its armed program. With `day`
    (the coordinator), a trigger is asked of it first, as Shows does, and nothing is posted on a no."""

    def __init__(self, url: str, day=None, log=None):
        self.url = url.rstrip("/")
        self.day, self._log_fn = day, log

    def state(self) -> str | None:
        try:
            with urllib.request.urlopen(self.url + "/api/state", timeout=1.0) as r:
                return json.load(r).get("state")
        except (OSError, ValueError):
            return None

    def post(self, kind: str, text: str) -> str | None:
        if kind == "trigger" and self.day is not None:
            ok, why = self.day.request("candy")
            if not ok:
                if self._log_fn:
                    self._log_fn({"t": round(time.time(), 3), "event": "run_refused", "why": f"demo day: {why}"})
                return "ARMED"
        req = urllib.request.Request(self.url + "/api/heard", data=json.dumps({"kind": kind, "text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3.0) as r:
                return json.load(r).get("state")
        except (OSError, ValueError):
            return None


class Light:
    """The base's LED strip says "I heard you": LIGHT_RGB for LIGHT_S, through the dashboard's show-light
    layer (software/kiosk-dashboard/backend/show_light.py: loopback only, a lease that lapses by itself,
    the reserved states always shown over it). The colour must not read as a reserved state colour (the
    show validator's own rule, cue.light_reserved_clash: E-STOP red and DEGRADED amber, so not orange).
    A show that drives the light takes it over by renewing its own lease. `call(body) -> bool` is a seam."""

    def __init__(self, dashboard: str = "http://127.0.0.1:8080", rgb=None, br: int = 100, call=None):
        self.url, self.rgb, self.br = dashboard.rstrip("/"), list(rgb or LIGHT_RGB), int(br)
        choreo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "choreo")
        if os.path.abspath(choreo) not in sys.path:
            sys.path.insert(0, os.path.abspath(choreo))
        import cue
        clash = cue.light_reserved_clash(self.rgb)
        if clash:
            raise ValueError(f"the listener's light {self.rgb} reads as {clash}, a reserved state colour")
        self.call = call or self._post

    def _post(self, body: dict) -> bool:
        req = urllib.request.Request(self.url + "/api/choreo/light", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1.0) as r:
                return r.status == 200
        except (OSError, ValueError):
            return False

    def flash(self) -> bool:
        return self.call({"rgb": self.rgb, "br": self.br, "lease_ms": int(LIGHT_S * 1000)})


class Day:
    """The demo day's coordinator (demo_day.py) on 127.0.0.1:8096. request(want, text) -> (ok, why);
    a coordinator that does not answer refuses: nothing starts without it."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def request(self, want: str, text: str = ""):
        req = urllib.request.Request(self.url + "/api/request", data=json.dumps({"want": want, "text": text}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=6.0) as r:
                got = json.load(r)
            return bool(got.get("ok")), got.get("why")
        except (OSError, ValueError) as e:
            return False, f"the demo day's coordinator does not answer ({e})"


CONFIRM_S = 4.0                                   # how long a started show is watched for a refusal
CHECKING = ("VALIDATING", "PREFLIGHT")             # the runner's checks, before a run is on (runner.py)
ENDED_BADLY = ("REFUSED", "ABORTED", "ESTOPPED")


class Shows:
    """The show runner instead of ARCHITECT's: "trick or treat" plays ONE routine from the dashboard's
    show palette, through the same call the Shows page's button makes (POST /api/choreo/run/<id>, in
    software/kiosk-dashboard/backend/choreo_proxy.py). The runner keeps every gate it has for that
    button: the show validated and clearance-checked, CH5 in AUTO, the arms active and the deadman.

    START. `deadman`, the robot's default (agreed with the Orchestrator, 2026-10-13): "trick or treat"
    ARMS the run (nothing moves, the music loads) and the supervisor's FRESH press of the deadman is
    the go, so a child alone never starts motion and a deadman held down never starts a show.
    `countdown` starts at once while the deadman is held: hands-free, without the fresh-press rule.

    The listener sees the same two calls it makes of the demo runner: state() is RUNNING while a show
    runs (a "trick or treat" then gets a HOLD_ON line) and ARMED otherwise; a refused run comes back
    ARMED, with the runner's own words in the log. "Stop" stops the show; nothing else is posted.
    `call(method, path, body) -> (status, json)` is a seam for the tests."""

    def __init__(self, dashboard: str, routine: str, start: str = "deadman", countdown_s=None, log=None, call=None,
                 day=None):
        self.url, self.routine, self.start, self.countdown_s = dashboard.rstrip("/"), routine, start, countdown_s
        self.day = day                                  # Day: asked before the run, when given
        self._log_fn, self.call = log, call or self._call

    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=5.0) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:              # the runner's refusal, in its own words
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
        except (OSError, ValueError):
            return 503, None

    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    def check(self) -> None:
        """Refuse to start on a routine the robot does not have, or one that does not validate."""
        code, routines = self.call("GET", "/api/choreo/routines", None)
        if code != 200 or not isinstance(routines, list):
            raise SystemExit(f"listener: the dashboard's show palette cannot be read at {self.url} ({code})")
        r = next((x for x in routines if x.get("id") == self.routine), None)
        if r is None:
            raise SystemExit(f"listener: no routine {self.routine!r}; the robot has "
                             + ", ".join(x.get("id", "?") for x in routines))
        if not r.get("valid"):
            raise SystemExit(f"listener: {self.routine!r} does not validate: {'; '.join(r.get('problems') or [])}")

    def _palette_last(self):
        code, st = self.call("GET", "/api/choreo/palette", None)
        return st.get("last") if code == 200 and isinstance(st, dict) else None

    def _confirm(self, before) -> str | None:
        """The runner accepts a start, then checks it (VALIDATING, PREFLIGHT) and may refuse it a moment
        later: the arms not active, CH5 in MANUAL. Wait, at most CONFIRM_S, until the run is past those
        (ARMED_WAITING or further: it is on) or has ended; the refusal's reason, or None. A palette that
        cannot be read is no refusal: the run goes ahead as it did before this check (found 2026-10-27:
        a greeting promised candy for a run refused 0.2 s after it was accepted)."""
        end = time.monotonic() + CONFIRM_S
        while True:
            code, st = self.call("GET", "/api/choreo/palette", None)
            if code != 200 or not isinstance(st, dict):
                return None
            last = st.get("last")
            if st.get("running") == self.routine:
                if st.get("status") not in CHECKING:
                    return None
            elif isinstance(last, dict) and last != before and last.get("id") == self.routine:
                if last.get("status") in ENDED_BADLY:
                    return f"{last.get('code') or last.get('status')}: {(last.get('cause') or '')[:200]}"
                return None
            if time.monotonic() >= end:
                return None
            time.sleep(0.1)

    def state(self) -> str | None:
        code, st = self.call("GET", "/api/choreo/palette", None)
        if code != 200 or not isinstance(st, dict):
            return None
        return "RUNNING" if st.get("running") else "ARMED"

    def post(self, kind: str, text: str) -> str | None:
        if kind == "trigger" and self.day is not None:
            ok, why = self.day.request("candy")
            if not ok:
                self._log("show_refused", routine=self.routine, status=None, why=f"demo day: {why}")
                return "ARMED"
        if kind == "trigger":
            before = self._palette_last()
            body = {"start": self.start, **({"countdown_s": self.countdown_s} if self.countdown_s is not None else {})}
            code, got = self.call("POST", f"/api/choreo/run/{self.routine}", body)
            if code == 200:
                refused = self._confirm(before)
                if refused:                             # accepted, then refused at preflight: no greeting
                    self._log("show_refused", routine=self.routine, status=code, why=refused)
                    return "ARMED"
                self._log("show_started", routine=self.routine, start=self.start, reply=got)
                return "RUNNING"
            self._log("show_refused", routine=self.routine, status=code,
                      why=(got or {}).get("error") if isinstance(got, dict) else None)
            return "ARMED"
        if kind == "speech" and _STOP.search(text or ""):
            code, _ = self.call("POST", "/api/choreo/stop", None)
            self._log("show_stopped", status=code)
            return "ARMED"
        return None                                     # nothing else reaches a show


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runner", default="http://127.0.0.1:8098", help="the demo runner")
    ap.add_argument("--show", metavar="ROUTINE", help="trick or treat plays this show-palette routine "
                    "through the dashboard (Shows), instead of the demo runner's program")
    ap.add_argument("--dashboard", default="http://127.0.0.1:8080")
    ap.add_argument("--start", choices=("deadman", "countdown"), default="deadman",
                    help="deadman (the default): the supervisor's fresh deadman press is the go")
    ap.add_argument("--countdown-s", type=float, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099, help="POST /api/say {text}: the robot speaks it")
    ap.add_argument("--card", default="Jabra", help="the sound card, by a word of its name")
    ap.add_argument("--models", default=os.environ.get("OPENDUBS_VOICE_MODELS", "/var/lib/opendubs/models"))
    ap.add_argument("--kws-models", help="where kws.py fetched the spotter's model (default: --models)")
    ap.add_argument("--stt", default=os.environ.get("VOICE_STT", "parakeet"))
    ap.add_argument("--tts", default=os.environ.get("VOICE_TTS", "kokoro"),
                    help="kokoro (the voice service's American voice) or butler (bm_george, British; "
                    "butler.py, fetched into --kws-models)")
    ap.add_argument("--no-chat", action="store_true", help="no greetings or replies from the local model")
    ap.add_argument("--program", metavar="FILE", help="show mode: the ARCHITECT program the show was recorded from, "
                    "for the dashboard's ARCHITECT tab (display only; the show is what runs)")
    ap.add_argument("--no-light", action="store_true", help="do not flash the base strip purple when the phrase is heard")
    ap.add_argument("--log-heard", action="store_true", help="tuning: log what Parakeet wrote when it does not "
                    "confirm a spotter hit (about 2 s of speech, only after a hit; off by default)")
    ap.add_argument("--show-audio", action="store_true", help="show mode: hold the dashboard's show-audio role, "
                    "so shows' music plays on the Jabra (show_music.py), mixed with the robot's speech")
    ap.add_argument("--day", metavar="URL", help="show mode: the demo day's coordinator (demo_day.py, e.g. "
                    "http://127.0.0.1:8096), asked before the candy show and given spoken show requests")
    a = ap.parse_args(argv)
    if a.day and not a.show:
        ap.error("--day needs --show (the candy show)")
    if a.show_audio and not a.show:
        ap.error("--show-audio needs --show")
    os.environ["OPENDUBS_VOICE_MODELS"] = a.models      # speech.py and vad.py read it

    from opendubs_architect import alsa, chat
    import speech

    feed = Feed()
    program = None
    if a.program:                               # read once: the show is what runs, this is what it came from
        with open(a.program, encoding="utf-8") as f:
            code = f.read()
        import hashlib
        m = re.search(r"^\s*#\s*Summary:\s*(.+?)\s*$", code, re.M)
        program = {"code": code, "summary": m.group(1) if m else None, "path": a.program,
                   "sha": hashlib.sha256(code.encode()).hexdigest()[:12]}

    def log(ev):
        print(json.dumps(ev), flush=True)
        feed.add(ev)

    stt = speech.make_stt(a.stt)
    if a.tts == "butler":
        from opendubs_architect import butler
        tts = butler.ButlerTTS(a.kws_models or a.models)
    else:
        tts = speech.make_tts(a.tts)
    if a.show:
        runner = Shows(a.dashboard, a.show, a.start, a.countdown_s, log=log, day=Day(a.day) if a.day else None)
        runner.check()
    else:
        runner = Runner(a.runner, day=Day(a.day) if a.day else None, log=log)
    lines: queue.Queue = queue.Queue()
    listener = Listener(kws.Spotter(a.kws_models or a.models), lambda pcm: asyncio.run(stt.transcribe(pcm)).text,
                        runner.post, make_vad=lambda: vad.make_vad(a.models), log=log,
                        chat=None if a.no_chat else chat.Chat(), say=lambda t: lines.put(t[:500]),
                        log_heard=a.log_heard, light=None if a.no_light else Light(a.dashboard), feed=feed,
                        day=Day(a.day) if a.day else None)

    async def synth(text):                  # whole, then louder: Kokoro synthesises a sentence at once anyway
        return loud(b"".join([chunk async for chunk in tts.speak(text)]))

    mixer = None
    if a.show_audio:                        # one output for the Jabra: the shows' music and the speech over it
        from opendubs_architect import show_music
        mixer = show_music.Mixer(a.card, log=log).start()
        show_music.ShowAudio(mixer, a.dashboard, log=log).start()

    def speaker():
        out = None
        while True:
            text = lines.get()
            listener.speaking = True
            try:
                if mixer is not None:
                    pcm = asyncio.run(synth(text))
                    mixer.say(pcm).wait(timeout=len(pcm) / 2 / alsa.PLAY_RATE + 5.0)
                else:
                    out = out or alsa.Playback(alsa.find_card(a.card))
                    out.write(asyncio.run(synth(text)))
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

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if path == "/api/feed":
                after = int((dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("after") or 0))
                return self._send(200, {"status": dict(listener.status(), **(
                    {"show": a.show, "start": a.start} if a.show else {"runner": a.runner})),
                    "program": program, "seq": feed.seq, "events": feed.since(after)})
            self._send(404, {"error": "not found"})

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
    log({"t": round(time.time(), 3), "event": "listening", "say": f"http://{a.host}:{a.port}/api/say",
         **({"show": a.show, "start": a.start, "dashboard": a.dashboard} if a.show else {"runner": a.runner})})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenDubs listener</title>
<style>
  :root { --bg: #fafaf8; --ink: #1d1d1b; --quiet: #6b6b66; --line: #d9d8d3; --accent: #5b3fd1; --ok: #2e7d4f; --warn: #a15c00; }
  @media (prefers-color-scheme: dark) { :root { --bg: #171716; --ink: #ecebe6; --quiet: #a3a29b; --line: #393936; --accent: #a996ff; --ok: #6fcf97; --warn: #f2b45c; } }
  body { margin: 0; padding: 16px; background: var(--bg); color: var(--ink); font: 15px/1.45 system-ui, sans-serif; }
  main { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .quiet { color: var(--quiet); }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin: 10px 0 14px; }
  .chip { border: 1px solid var(--line); border-radius: 999px; padding: 3px 12px; font-size: 13px; }
  .chip.on { border-color: var(--ok); color: var(--ok); } .chip.off { border-color: var(--warn); color: var(--warn); }
  .meter { flex: 1; min-width: 160px; height: 10px; border-radius: 5px; background: var(--line); overflow: hidden; }
  .meter > div { height: 100%; width: 0; background: var(--accent); transition: width 0.15s; }
  ol { list-style: none; margin: 0; padding: 0; }
  li { border-top: 1px solid var(--line); padding: 8px 0; display: grid; grid-template-columns: 72px 1fr; gap: 10px; }
  li .t { color: var(--quiet); font-size: 13px; font-variant-numeric: tabular-nums; }
  li.heard .x { font-size: 19px; }
  li.said .x { color: var(--accent); }
  li.act .x { font-weight: 600; }
  li.minor .x { color: var(--quiet); font-size: 13px; }
</style></head>
<body><main>
<h1>What the listener hears</h1>
<p class="quiet" id="what">...</p>
<div class="row"><span class="chip" id="state">runner ...</span><span class="chip" id="ears">...</span>
  <span class="quiet">mic</span><div class="meter"><div id="level"></div></div><span class="quiet" id="db"></span></div>
<ol id="feed"></ol>
<p class="quiet">Kept in memory on the robot only, the last 200 events: what Parakeet wrote for each clip it was given (after the
spotter heard a phrase, or while the robot waits for a yes or runs a program), and what the listener did with it.</p>
</main>
<script>
const $ = (id) => document.getElementById(id);
let after = 0;
const fmt = (e) => {
  const kinds = {wake: "after OpenDubs", trick: "trick or treat check", open: "open window"};
  switch (e.event) {
    case "heard": return ["heard", e.text ? "\\u201c" + e.text + "\\u201d" : "(nothing intelligible)", kinds[e.kind] || e.kind];
    case "spotted": return ["minor", "spotter caught " + e.keyword.replace(/_/g, " ").toLowerCase(), ""];
    case "posted": return ["act", "sent " + e.kind + (e.kind === "trigger" ? "" : ": " + e.text), e.state ? "runner now " + e.state : ""];
    case "show_started": return ["act", "show armed: " + e.routine, "press the deadman fresh to start it"];
    case "show_refused": return ["act", "show refused", e.why || ("status " + e.status)];
    case "show_stopped": return ["act", "show stopped", ""];
    case "greeted": case "replied": case "held": return ["said", "robot: " + e.text, e.event === "held" ? "no run started" : ""];
    case "said": return ["minor", "spoke on the Jabra", ""];
    case "light": return ["minor", e.ok ? "base strip: purple" : "base strip: flash not accepted", ""];
    case "not_confirmed": return ["minor", "not confirmed as trick or treat", ""];
    case "turn_capped": return ["minor", "stopped listening after " + e.seconds + " s", ""];
    case "not_addressed": return ["minor", "no wake phrase: " + (e.why || ""), ""];
    case "dropped": return ["minor", "ignored" + (e.why ? ": " + e.why : ""), ""];
    default: return ["minor", e.event + (e.error ? ": " + e.error : ""), ""];
  }
};
async function tick() {
  try {
    const r = await (await fetch("/api/feed?after=" + after)).json();
    const s = r.status;
    $("what").textContent = s.show ? "Trick or treat plays the show " + s.show + " (start: " + s.start + ")" : "Posts to the ARCHITECT runner at " + s.runner;
    $("state").textContent = "runner " + (s.state || "unreachable");
    $("ears").textContent = s.deaf ? (s.speaking ? "deaf: robot talking" : "deaf: just spoke") : "listening";
    $("ears").className = "chip " + (s.deaf ? "off" : "on");
    const pct = Math.max(0, Math.min(100, (s.level_db + 60) / 60 * 100));
    $("level").style.width = pct + "%"; $("db").textContent = Math.round(s.level_db) + " dBFS";
    for (const e of r.events) {
      const [cls, text, note] = fmt(e);
      const li = document.createElement("li"); li.className = cls;
      const t = document.createElement("span"); t.className = "t";
      t.textContent = e.event === "spotted" ? new Date(e.t * 1000).toLocaleTimeString() : (e.after_s != null ? "+" + e.after_s.toFixed(2) + " s" : new Date(e.t * 1000).toLocaleTimeString());
      const x = document.createElement("span"); x.className = "x"; x.textContent = text;
      if (note) { const n = document.createElement("div"); n.className = "quiet"; n.style.fontSize = "13px"; n.textContent = note; x.appendChild(n); }
      li.append(t, x); $("feed").prepend(li);
      while ($("feed").children.length > 200) $("feed").lastChild.remove();
    }
    after = r.seq;
  } catch (err) { $("state").textContent = "listener unreachable"; }
}
tick(); setInterval(tick, 500);
</script></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
