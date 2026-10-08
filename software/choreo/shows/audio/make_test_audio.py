#!/usr/bin/env python3
"""Generate the test audio the demo shows are written against.

    python3 make_test_audio.py            # writes beat-demo-120.wav and wave-and-nod-120.wav here

WHY GENERATED: no copyrighted audio goes in this repo, and a click track is a better test signal
than music anyway — every beat is a transient you can see in the waveform and hear against the
grid. The .wav files are git-ignored; this script is the source. It is deterministic, so two runs
produce byte-identical files.

The track: 120 BPM, 4/4, beat 1 at 0.5 s (matching shows/beat-demo.yaml's music block), an
accented click on each downbeat, a plain click on the other beats, and a quiet two-note bass line
so the waveform has a shape between the clicks. Standard library only.
"""
from __future__ import annotations

import math
import os
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))


def click_track(path, bpm=120.0, offset_s=0.5, beats_per_bar=4, length_s=36.0, rate=22050):
    n = int(length_s * rate)
    buf = [0.0] * n
    beat_s = 60.0 / bpm

    def tone(start_s, dur_s, hz, amp, decay):
        i0 = int(start_s * rate)
        for i in range(int(dur_s * rate)):
            k = i0 + i
            if k >= n:
                break
            env = math.exp(-i / (decay * rate))
            buf[k] += amp * env * math.sin(2 * math.pi * hz * i / rate)

    beat = 0
    while True:
        t = offset_s + beat * beat_s
        if t >= length_s:
            break
        down = beat % beats_per_bar == 0
        tone(t, 0.06, 1760.0 if down else 1100.0, 0.55 if down else 0.35, 0.012)
        # bass: root on beats 1-2 of each bar, a fifth on 3-4, alternating bars up an octave
        bar = beat // beats_per_bar
        root = 55.0 * (2 if bar % 2 else 1)
        hz = root if (beat % beats_per_bar) < 2 else root * 1.5
        tone(t, beat_s * 0.9, hz, 0.18, beat_s * 0.5)
        beat += 1

    peak = max(1e-9, max(abs(v) for v in buf))
    scale = 0.9 * 32767 / peak
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(v * scale)) for v in buf))
    return path


def tune(path, bpm=120.0, offset_s=0.5, length_s=19.0, rate=22050):
    """wave-and-nod-120.wav (shows/wave-and-nod.yaml): a cheerful tune on the same grid as the
    click track — a pentatonic melody on every beat, a soft click under it, and a bass note per
    bar — ending with a held chord on bar 9 so the show's return to rest has something to land on."""
    n = int(length_s * rate)
    buf = [0.0] * n
    beat_s = 60.0 / bpm

    def tone(start_s, dur_s, hz, amp, decay):
        i0 = int(start_s * rate)
        for i in range(int(dur_s * rate)):
            k = i0 + i
            if k >= n:
                break
            env = math.exp(-i / (decay * rate)) * min(1.0, i / (0.004 * rate))
            buf[k] += amp * env * (math.sin(2 * math.pi * hz * i / rate)
                                   + 0.3 * math.sin(4 * math.pi * hz * i / rate))

    c = 261.63                                         # C4; the C major pentatonic, in semitones
    step = lambda s: c * 2 ** (s / 12)
    melody = [0, 4, 7, 9, 7, 4, 2, 4,  0, 4, 7, 12, 9, 7, 9, 7,
              4, 7, 9, 12, 9, 7, 4, 2,  0, 2, 4, 7, 4, 2, 0, 0]
    bass = [0, -5, -3, -7, 0, -5, -8, -5]               # one per bar, under the melody
    for b, s in enumerate(melody):
        t = offset_s + b * beat_s
        tone(t, beat_s * 0.95, step(s), 0.30, beat_s * 0.45)
        tone(t, 0.04, 2200.0, 0.10, 0.008)
        if b % 4 == 0:
            tone(t, beat_s * 3.8, step(bass[b // 4] - 12), 0.22, beat_s * 1.6)
    end = offset_s + len(melody) * beat_s               # bar 9: a held C major chord
    for s in (-12, 0, 4, 7):
        tone(end, length_s - end, step(s), 0.18, 1.2)

    peak = max(1e-9, max(abs(v) for v in buf))
    scale = 0.9 * 32767 / peak
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(v * scale)) for v in buf))
    return path


if __name__ == "__main__":
    p = click_track(os.path.join(HERE, "beat-demo-120.wav"))
    print("wrote", p)
    p = tune(os.path.join(HERE, "wave-and-nod-120.wav"))
    print("wrote", p)
