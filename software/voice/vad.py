"""Voice activity detection, and the end of a wake-initiated utterance.

A Talk turn ends when the button is released. A wake turn has no button, so the service decides:
the page sends the keyword's pre-roll and then live 16 kHz frames, and Endpointer watches them.

    after the wake point (plus GRACE_S for the keyword's tail), wait up to START_TIMEOUT_S for speech
    to begin                                                               -> else "no_speech"
    once speech has begun, END_SILENCE_S of silence ends the utterance    -> "end"
    the utterance cap (max_utterance_s) still applies, in conversation.py

Backends, behind one call, `speech(frame) -> bool` for each 20 ms frame:

  SileroVAD  Silero VAD (MIT) through sherpa-onnx, already in the image; the model is pinned in
             models.lock.json (vad/silero_vad.onnx) and fetched at provision time
  EnergyVAD  the energy gate of audio.py, per frame: tests, and a desk with no model

Silero is the step the workplan named 1.4. It replaces nothing in Talk turns, whose end is the
release and whose NO_SPEECH check stays audio.has_speech.
"""
from __future__ import annotations

import os

import audio
import wire

START_TIMEOUT_S = 4.0     # [E] long enough to take a breath after "hey dubs"
GRACE_S = 0.35            # after the wake point: Silero's speech flag outlasts a word by ~0.25 s, so
                          # the tail of "dubs" itself must not count as the question starting
END_SILENCE_S = 0.8       # [E] Pipecat's default is 0.2 s plus a turn model; we have no turn model yet


class EnergyVAD:
    """Speech if a frame is well above the quiet floor seen so far. A stand-in for Silero."""

    def __init__(self, threshold_db: float = 12.0, min_dbfs: float = -45.0):
        self.threshold_db, self.min_dbfs = threshold_db, min_dbfs
        self.floor = None

    def speech(self, pcm: bytes) -> bool:
        lv = audio.frame_levels(pcm, wire.UP_RATE, frame_ms=20)
        if not lv:
            return False
        level = max(lv)
        # the floor follows the quietest frames at once and rises only slowly, so speech does not drag it up
        self.floor = level if self.floor is None else min(level, self.floor + 0.05)
        return level >= self.min_dbfs and level - self.floor >= self.threshold_db


class SileroVAD:
    """Silero VAD through sherpa-onnx's VoiceActivityDetector. `detector` is a seam for the tests."""

    def __init__(self, model: str, detector=None, threshold: float = 0.5):
        if detector is None:
            import sherpa_onnx
            c = sherpa_onnx.VadModelConfig()
            c.silero_vad.model = model
            c.silero_vad.threshold = threshold
            c.silero_vad.min_silence_duration = 0.25
            c.silero_vad.min_speech_duration = 0.25
            c.silero_vad.window_size = 512
            c.sample_rate = wire.UP_RATE
            detector = sherpa_onnx.VoiceActivityDetector(c, buffer_size_in_seconds=30)
        self.detector = detector

    def speech(self, pcm: bytes) -> bool:
        self.detector.accept_waveform([x / 32768.0 for x in audio.samples(pcm)])
        return bool(self.detector.is_speech_detected())


def make_vad(root: str | None = None):
    """A fresh detector for one utterance: Silero if its pinned model is there, else the stand-in."""
    path = os.path.join(root or os.environ.get("OPENDUBS_VOICE_MODELS", "/var/lib/opendubs/models"),
                        "vad", "silero_vad.onnx")
    if os.path.exists(path):
        try:
            return SileroVAD(path)
        except ImportError:
            pass
    return EnergyVAD()


class Endpointer:
    """Feed it each uplink frame of a wake turn; it says when the utterance is over."""

    def __init__(self, vad, pre_roll_ms: float, start_timeout_s: float = START_TIMEOUT_S,
                 end_silence_s: float = END_SILENCE_S, grace_s: float = GRACE_S):
        self.vad = vad
        self.pre_roll_ms = max(0.0, float(pre_roll_ms))
        self.start_timeout_s, self.end_silence_s, self.grace_s = start_timeout_s, end_silence_s, grace_s
        self.heard_ms = 0.0          # audio received so far
        self.speech_started = False
        self.silence_ms = 0.0

    def feed(self, pcm: bytes) -> str | None:
        """None while the utterance goes on; "end" after speech then silence; "no_speech" if nothing
        was said after the wake word in time."""
        ms = audio.duration_ms(pcm, wire.UP_RATE)
        is_speech = self.vad.speech(pcm)            # always fed, so a model keeps its state
        self.heard_ms += ms
        if self.heard_ms <= self.pre_roll_ms + self.grace_s * 1000:   # the keyword, and its tail
            return None
        if is_speech:
            self.speech_started, self.silence_ms = True, 0.0
            return None
        if not self.speech_started:
            return "no_speech" if self.heard_ms - self.pre_roll_ms >= self.start_timeout_s * 1000 else None
        self.silence_ms += ms
        return "end" if self.silence_ms >= self.end_silence_s * 1000 else None
