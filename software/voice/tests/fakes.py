"""Test doubles: a page, a face, a gate, and fake audio. No robot, no model, no network."""
from __future__ import annotations

import asyncio
import time
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import audio  # noqa: E402
import wire  # noqa: E402
from conversation import Voice  # noqa: E402
from speech import FakeSTT, FakeTTS  # noqa: E402
from brains import FakeLLM  # noqa: E402


def speech_clip(ms: float = 1200) -> bytes:
    """A quiet room with a syllable-rate voice-band hum in the middle: 'speech' to the energy gate."""
    room = audio.noise(ms, wire.UP_RATE, amp=0.003)
    lead = ms * 0.2
    voice = audio.silence(lead, wire.UP_RATE) + audio.tone(220, ms * 0.6, wire.UP_RATE, amp=0.3, am_hz=4)
    return audio.mix(room, voice)


def silent_clip(ms: float = 1200) -> bytes:
    return audio.noise(ms, wire.UP_RATE, amp=0.003)


def fan_clip(ms: float = 1200) -> bytes:
    """A steady loud hum with no rise and fall: a fan or an ESC whine, not speech."""
    return audio.tone(4000, ms, wire.UP_RATE, amp=0.2)


class FakeLink:
    """A face page: records what the service sends, and says `played` after each reply's audio,
    as the real page does when its playback queue drains."""

    def __init__(self, auto_played: bool = True):
        self.texts: list[dict] = []
        self.frames: list[tuple[int, int, bytes]] = []
        self.voice: Voice | None = None
        self.page = None
        self.auto_played = auto_played

    async def send_text(self, s: str) -> None:
        m = json.loads(s)
        assert m["type"] in wire.SERVICE_TYPES, m
        self.texts.append(m)
        if self.auto_played and m["type"] == "tts_end" and self.voice:
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.ensure_future(self.voice.on_text(self.page, json.dumps(
                    {"type": "played", "turn": m["turn"]}))))

    async def send_bytes(self, b: bytes) -> None:
        self.frames.append(wire.parse_down(b))

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.texts if m["type"] == kind]

    def states(self) -> list[str]:
        return [m["convo"] for m in self.of("state")]


class FakeFace:
    def __init__(self, reply: str = "OK"):
        self.lines: list[str] = []
        self.convos: list[str] = []
        self.reply = reply

    async def send(self, line: str) -> str:
        from face import check
        check(line)
        self.lines.append(line)
        return self.reply

    async def convo(self, state: str) -> None:
        self.convos.append(state)


class FakeGate:
    url = "http://127.0.0.1:1/api/choreo/palette"

    def __init__(self, on: bool = True, why=None):
        self.on, self.why = on, why
        self.listeners: list = []

    async def set(self, on: bool, why=None) -> None:
        self.on, self.why = on, why
        for cb in self.listeners:
            await cb(on, why)


class BrokenBrain:
    name = "broken"

    async def stream(self, history, system):
        from brains import BrainOffline
        raise BrainOffline("no server")
        yield  # noqa: unreachable — makes this an async generator


def make_voice(*, llm=None, stt=None, tts=None, face=None, gate=None, **kw) -> Voice:
    return Voice(stt or FakeSTT("what are you"), tts or FakeTTS(), llm or FakeLLM(),
                 face or FakeFace(), gate or FakeGate(), "You are a test robot.",
                 robot_status=kw.pop("robot_status", lambda: {"ts": time.time(), "state": {"words": "Ready"},
                                                              "faults": [], "battery": {"percent": 80, "charging": False}}),
                 **kw)


async def connect(voice: Voice, **kw) -> tuple[FakeLink, object]:
    link = FakeLink(**kw)
    page = await voice.join(link)
    link.voice, link.page = voice, page
    await voice.on_text(page, json.dumps({"type": "hello", "protocol": wire.PROTOCOL,
                                          "up": {"rate": wire.UP_RATE, "format": "s16le", "channels": 1}}))
    return link, page


async def say(voice: Voice, page, clip: bytes, release: bool = True, seq_skip: int = 0) -> None:
    """Press, stream the clip in 20 ms frames, release."""
    await voice.on_text(page, '{"type":"press"}')
    step = wire.UP_FRAME_SAMPLES * 2
    for i, off in enumerate(range(0, len(clip), step)):
        seq = i + (seq_skip if i >= 3 else 0)
        await voice.on_bytes(page, wire.pack_up(seq, i * 20, clip[off:off + step]))
    if release:
        await voice.on_text(page, '{"type":"release"}')


async def settle(voice: Voice, timeout: float = 5.0) -> None:
    """Until the turn in flight has finished and the state is idle."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if voice._task is None and voice.state == "idle":
            await asyncio.sleep(0.01)
            if voice._task is None and voice.state == "idle":
                return
        await asyncio.sleep(0.01)
    raise AssertionError(f"voice did not settle: state={voice.state}")
