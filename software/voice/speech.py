"""Speech-to-text and text-to-speech backends, behind two small interfaces.

    STT:  await stt.transcribe(pcm16k) -> Transcript        one bounded push-to-talk clip
    TTS:  async for pcm24k in tts.speak(sentence): ...     PCM chunks as they are ready

Chosen by VOICE_STT / VOICE_TTS:

  fake      deterministic, no model: for tests and for a desk demo of the whole turn
  parakeet  Parakeet-TDT-0.6B-v2 (CC-BY-4.0)   } NOT PACKAGED YET. Workplan step 1.3 builds them
  kokoro    Kokoro-82M (Apache-2.0)            } into the GPU image on the non-robot Thor; until
                                                 then they refuse to start, by name.

STT and TTS stay on the robot in every brain mode: audio never leaves it.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import audio
import wire


class BackendUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    ms: float = 0.0


class FakeSTT:
    """Hears `text` in any clip with speech in it. VOICE_FAKE_TRANSCRIPT sets it for a desk demo."""

    def __init__(self, text: str | None = None, delay_s: float = 0.0):
        self.text = text if text is not None else os.environ.get("VOICE_FAKE_TRANSCRIPT", "hello robot")
        self.delay_s = delay_s

    async def transcribe(self, pcm: bytes) -> Transcript:
        await asyncio.sleep(self.delay_s)
        return Transcript(self.text if audio.has_speech(pcm, wire.UP_RATE) else "")


class FakeTTS:
    """A soft syllable-rate hum, about 60 ms per character, so a page's speaking envelope (and the
    mouth, when it is on) has something to follow. Real-time pacing is optional."""

    MS_PER_CHAR = 60

    def __init__(self, delay_s: float = 0.0, realtime: bool = False):
        self.delay_s = delay_s
        self.realtime = realtime
        self.spoken: list[str] = []

    async def speak(self, text: str):
        self.spoken.append(text)
        await asyncio.sleep(self.delay_s)
        ms = min(8000, max(200, self.MS_PER_CHAR * len(text)))
        pcm = audio.tone(180, ms, wire.DOWN_RATE, amp=0.15, am_hz=4.0)
        for c in audio.chunks(pcm, wire.DOWN_RATE, wire.DOWN_CHUNK_MS):
            yield c
            await asyncio.sleep(wire.DOWN_CHUNK_MS / 1000 if self.realtime else 0)


class ParakeetSTT:
    def __init__(self):
        raise BackendUnavailable("VOICE_STT=parakeet: Parakeet is not packaged yet "
                                 "(notes/workplan-voice.md, step 1.3). Use VOICE_STT=fake on a desk.")


class KokoroTTS:
    def __init__(self):
        raise BackendUnavailable("VOICE_TTS=kokoro: Kokoro is not packaged yet "
                                 "(notes/workplan-voice.md, step 1.3). Use VOICE_TTS=fake on a desk.")


def _make(kind: str, name: str, choices: dict):
    if name not in choices:
        raise ValueError(f"VOICE_{kind}={name!r}: use {' or '.join(choices)}")
    return choices[name]()


def make_stt(name: str):
    return _make("STT", name, {"fake": FakeSTT, "parakeet": ParakeetSTT})


def make_tts(name: str):
    return _make("TTS", name, {"fake": FakeTTS, "kokoro": KokoroTTS})
