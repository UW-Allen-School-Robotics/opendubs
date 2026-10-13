"""Small PCM helpers: is there speech in a clip, a test tone, and cutting audio into chunks.

`has_speech` is a deliberately crude energy gate, a STAND-IN for Silero VAD (workplan step 1.4).
With push-to-talk it only has to reject an empty or silent clip, so it never reaches a model: the
head voice plan's NO_SPEECH rule. It compares the loudest 20 ms frame with the quietest ones (the
`floor`/`peak` confidence of that plan), so a steady fan or ESC whine is not mistaken for speech.

Standard library only. PCM is s16le mono throughout.
"""
from __future__ import annotations

import math
import sys
from array import array

# Thresholds for the stand-in. [E] judgement, to be replaced with Silero's verdict and checked on
# recordings from the robot's own room (workplan gate V-2 records them).
MIN_SPEECH_MS = 250          # shorter than this is a tap on the button, not an utterance
PEAK_MIN_DBFS = -45.0        # quieter than this at its loudest is silence
PEAK_OVER_FLOOR_DB = 12.0    # speech rises well above the room; a steady noise does not


def samples(pcm: bytes) -> array:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder != "little":
        a.byteswap()
    return a


def to_bytes(a: array) -> bytes:
    if sys.byteorder != "little":
        a = array("h", a)
        a.byteswap()
    return a.tobytes()


def dbfs(rms: float) -> float:
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def frame_levels(pcm: bytes, rate: int, frame_ms: int = 20) -> list[float]:
    """RMS level of each frame, in dBFS."""
    s = samples(pcm)
    n = max(1, rate * frame_ms // 1000)
    out = []
    for i in range(0, len(s) - n + 1, n):
        f = s[i:i + n]
        out.append(dbfs(math.sqrt(sum(x * x for x in f) / n)))
    return out


def level(pcm: bytes, rate: int) -> tuple[float, float]:
    """(peak, floor) in dBFS: the loudest frame, and the 10th-percentile frame."""
    lv = sorted(frame_levels(pcm, rate))
    if not lv:
        return -120.0, -120.0
    return lv[-1], lv[len(lv) // 10]


def duration_ms(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / rate * 1000


def has_speech(pcm: bytes, rate: int) -> bool:
    if duration_ms(pcm, rate) < MIN_SPEECH_MS:
        return False
    peak, floor = level(pcm, rate)
    return peak >= PEAK_MIN_DBFS and peak - floor >= PEAK_OVER_FLOOR_DB


def tone(freq: float, ms: float, rate: int, amp: float = 0.3, am_hz: float = 0.0) -> bytes:
    """A sine, optionally amplitude-modulated so it rises and falls like syllables."""
    n = int(rate * ms / 1000)
    a = array("h", bytes(2 * n))
    for i in range(n):
        t = i / rate
        env = 0.5 - 0.5 * math.cos(2 * math.pi * am_hz * t) if am_hz else 1.0
        a[i] = int(32767 * amp * env * math.sin(2 * math.pi * freq * t))
    return to_bytes(a)


def silence(ms: float, rate: int) -> bytes:
    return bytes(2 * int(rate * ms / 1000))


def noise(ms: float, rate: int, amp: float = 0.01, seed: int = 1) -> bytes:
    """Deterministic white noise (a tiny LCG), for tests: a room that is not perfectly silent."""
    n = int(rate * ms / 1000)
    a = array("h", bytes(2 * n))
    x = seed
    for i in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        a[i] = int((x / 0x3FFFFFFF - 1.0) * 32767 * amp)
    return to_bytes(a)


def mix(*clips: bytes) -> bytes:
    """Sum equal-rate clips sample by sample (the longest sets the length), clipped to s16."""
    arrs = [samples(c) for c in clips]
    n = max(len(a) for a in arrs)
    out = array("h", bytes(2 * n))
    for a in arrs:
        for i, v in enumerate(a):
            out[i] = max(-32768, min(32767, out[i] + v))
    return to_bytes(out)


def chunks(pcm: bytes, rate: int, ms: int):
    """Cut PCM into chunks of `ms`; the last may be shorter."""
    step = 2 * rate * ms // 1000
    for i in range(0, len(pcm), step):
        yield pcm[i:i + step]
