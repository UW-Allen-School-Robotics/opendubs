#!/usr/bin/env python3
"""Halloween music for the three short shows, synthesized here (original, nothing sampled).

    python3 make_halloween_audio.py      # writes halloween-wave-and-nod.wav, halloween-robotic-smile.wav
                                         # and halloween-celebrate.wav here (needs numpy)

WHY GENERATED, like make_test_audio.py: no audio file in this folder is committed (.gitignore); this
script is the source, and it is deterministic, so every run writes byte-identical files. The shows
(wave-and-nod, robotic-smile, celebrate) name these files in their `music: file:`.

Each track is written on its show's own grid (tempo, first beat, length, from software/choreo/shows/),
and its hits land on the face's steps (ghost.js DANCES[show].steps): a whoosh where the costume
changes, a theremin swoop on PEEK and EEK!, bells on HAPPY, a gong on the last bow. The sound world is
the usual Halloween one: a minor key, harpsichord and pizzicato, a church organ, a music box, a
theremin, and a little reverb; celebrate, a party, is in a major key with a dance beat and brass.
Audio is optional on the robot: a show runs the same without it.
"""
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent
rng = np.random.default_rng(31)


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def N(name):
    """'D4' / 'Bb3' / 'G#4' -> midi"""
    pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[name[0]]
    rest = name[1:]
    if rest.startswith("#"):
        pc += 1; rest = rest[1:]
    elif rest.startswith("b"):
        pc -= 1; rest = rest[1:]
    return 12 * (int(rest) + 1) + pc


class Track:
    def __init__(self, length):
        self.buf = np.zeros(int(length * SR))
        self.wet = np.zeros_like(self.buf)          # what goes through the reverb

    def add(self, t, sig, amp=1.0, wet=0.3):
        i = int(t * SR)
        if i >= len(self.buf):
            return
        sig = sig[: len(self.buf) - i] * amp
        self.buf[i:i + len(sig)] += sig
        self.wet[i:i + len(sig)] += sig * wet

    def render(self, fade=0.4):
        out = self.buf + reverb(self.wet)
        n = int(fade * SR)
        out[-n:] *= np.linspace(1, 0, n) ** 2
        # loud enough to hear over a crowd, its loudest moments squeezed rather than everything turned down
        out = np.tanh(out * 0.2 / (np.sqrt(np.mean(out ** 2)) + 1e-9))
        return out / (np.max(np.abs(out)) + 1e-9) * 0.89


def env(n, a=0.005, r=0.05):
    e = np.ones(n)
    na, nr = min(n, int(a * SR)), min(n, int(r * SR))
    if na: e[:na] = np.linspace(0, 1, na)
    if nr: e[-nr:] *= np.linspace(1, 0, nr)
    return e


def T(d):
    return np.arange(int(d * SR)) / SR


# ---- instruments ----------------------------------------------------------------------------------
def pluck(m, d=0.6):                      # harpsichord / pizzicato
    t, f = T(d), hz(m)
    s = sum(np.sin(2 * np.pi * k * f * t) * np.exp(-t * (4 + 2.2 * k)) / k ** 0.8 for k in range(1, 10))
    return s * env(len(t), 0.002, 0.03)


def organ(m, d, vib=0.004):
    t, f = T(d), hz(m)
    ph = 2 * np.pi * f * (t + vib / (2 * np.pi * 5.5) * np.sin(2 * np.pi * 5.5 * t))
    s = sum(w * np.sin(k * ph) for k, w in [(1, 1), (2, .6), (3, .35), (4, .25), (6, .12), (8, .08)])
    return s * env(len(t), 0.04, 0.15) * 0.5


def bell(m, d=2.0):                       # music box / tubular bell
    t, f = T(d), hz(m)
    s = sum(w * np.sin(2 * np.pi * p * f * t) * np.exp(-t * 2.2 * p ** 0.6) for p, w in [(1, 1), (2.76, .5), (5.4, .25), (8.93, .12)])
    return s * env(len(t), 0.001, 0.05)


def theremin(m0, m1, d, vib=0.015):
    t = T(d)
    f = hz(m0) * (hz(m1) / hz(m0)) ** (np.clip(t / d, 0, 1) ** 1.5)
    f = f * (1 + vib * np.sin(2 * np.pi * 6 * t) * np.clip(t / 0.3, 0, 1))
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(ph) + 0.18 * np.sin(2 * ph)) * env(len(t), 0.12, 0.25) * 0.6


def bass(m, d=0.45):
    t, f = T(d), hz(m)
    s = sum(((-1) ** (k // 2)) * np.sin(2 * np.pi * k * f * t) / k ** 2 for k in range(1, 12, 2))
    return s * np.exp(-t * 5) * env(len(t), 0.004, 0.04)


def kick():
    t = T(0.35)
    return np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-t * 28)) / SR) * np.exp(-t * 9)


def hat():
    t = T(0.08)
    n = rng.standard_normal(len(t))
    return np.diff(n, prepend=0) * np.exp(-t * 60) * 0.25


def tick():                               # a clock's tick: a woodblock
    t = T(0.06)
    return np.sin(2 * np.pi * 1800 * t) * np.exp(-t * 90) * 0.5


def whoosh(d=0.6):                        # a ghostly gust: noise swelling through a rising band
    t = T(d)
    n = rng.standard_normal(len(t))
    out = np.zeros_like(n)
    for w, g in [(40, 1.0), (14, 0.6), (6, 0.35)]:
        out += np.convolve(n, np.ones(w) / w, mode="same") * g
    shape = np.sin(np.pi * t / d) ** 2
    return out * shape * (0.4 + 0.6 * t / d) * 0.9


def howl(m0=N("D4"), m1=N("A4"), d=1.5, fall=0.7, r=0.35):   # a wolf: a yelp up, a long held "oooo", then it falls away
    t = T(d)
    u = t / d
    glide = np.where(u < 0.25, (u / 0.25) ** 0.6, np.where(u < fall, 1.0, 1 - (np.clip(u - fall, 0, None) / (1 - fall)) ** 1.4 * 1.3))
    f = hz(m0) * (hz(m1) / hz(m0)) ** glide
    f = f * (1 + 0.012 * np.sin(2 * np.pi * 5.2 * t) * np.clip((t - 0.3) / 0.4, 0, 1))
    ph = 2 * np.pi * np.cumsum(f) / SR
    s = sum(w * np.sin(k * ph) for k, w in [(1, 1), (2, .45), (3, .22), (4, .08)])   # a round "oo"
    n = rng.standard_normal(len(t))
    breath = np.convolve(n, np.ones(12) / 12, mode="same") * 0.25
    return (s + breath) * env(len(t), 0.08, r) * 0.6


def gong(m=N("D2"), d=3.0):
    t, f = T(d), hz(m)
    s = sum(w * np.sin(2 * np.pi * p * f * t + w) * np.exp(-t * (0.9 + p * 0.15)) for p, w in [(1, 1), (1.48, .6), (2.03, .5), (2.73, .35), (3.6, .2)])
    return s * env(len(t), 0.003, 0.3)


def reverb(x, seconds=1.6):
    t = T(seconds)
    ir = rng.standard_normal(len(t)) * np.exp(-t * 4.2)
    ir[0] = 0
    n = 1 << int(np.ceil(np.log2(len(x) + len(ir))))
    y = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(ir, n), n)[: len(x)]
    return y * 0.12


def chord(tr, t, names, d, amp=0.35, inst=organ, wet=0.5):
    for nm in names:
        tr.add(t, inst(N(nm), d), amp / len(names) ** 0.5, wet)


# ---- the shows --------------------------------------------------------------------------------------
def wave_and_nod():
    """120 bpm, beat 1 at 0.5 s, 19 s: D minor. A harpsichord nod (bars 1-2), the music box answering
    each waving hand (3-4 right, in the cat; 5-6 left), the organ for the reach (7-8), the held chord."""
    B, t0 = 0.5, 0.5
    bar = lambda n: t0 + (n - 1) * 4 * B
    tr = Track(19)
    tr.add(0.0, bell(N("A5"), 1.2), 0.25, 0.6)                       # SMILE: a music-box hello
    prog = [["D3", "F3", "A3"], ["D3", "F3", "A3"], ["Bb2", "D3", "F3"], ["A2", "C#3", "E3"],
            ["G2", "Bb2", "D3"], ["A2", "C#3", "E3"], ["D3", "F3", "A3"], ["A2", "C#3", "G3"]]
    roots = ["D2", "D2", "Bb1", "A1", "G1", "A1", "D2", "A1"]
    for b in range(8):
        s = bar(b + 1)
        for k in range(4):                                         # pizzicato bass on the beat
            tr.add(s + k * B, bass(N(roots[b]) + (12 if k % 2 else 0)), 0.5, 0.15)
        for k in range(8):                                         # harpsichord eighths
            tr.add(s + k * B / 2, pluck(N(prog[b][k % 3]) + 12, 0.5), 0.22, 0.25)
    motif = ["A4", "Bb4", "A4", "G#4", "A4", "F4", "E4", "D4"]         # the waving hand's tune (chromatic)
    for b, octave in [(3, 12), (4, 12), (5, 0), (6, 0)]:
        for k, nm in enumerate(motif):
            tr.add(bar(b) + k * B / 2, bell(N(nm) + octave, 0.9), 0.2, 0.5)
    tr.add(bar(3) - 0.3, whoosh(0.7), 0.35, 0.6)                    # into the cat
    tr.add(bar(3), theremin(N("E5"), N("A5"), 0.9), 0.18, 0.6)       # ...a meow-ish glide
    tr.add(bar(5) - 0.3, whoosh(0.7), 0.35, 0.6)                    # back to the pumpkin
    for b, c in [(7, ["D4", "F4", "A4", "D5"]), (8, ["A3", "C#4", "E4", "G4"])]:
        chord(tr, bar(b), c, 2.0, 0.35)
    for k, nm in enumerate(["D6", "A5", "F5", "D5"]):              # HAPPY: bells tumbling down
        tr.add(bar(7) + k * B, bell(N(nm), 1.6), 0.18, 0.6)
    chord(tr, 16.5, ["D3", "A3", "D4", "F4", "A4", "E5"], 2.6, 0.45)  # the held chord, to the end
    tr.add(16.5, gong(N("D2"), 2.5), 0.35, 0.5)
    return tr.render(0.6)


def robotic_smile():
    """123 bpm, beat 1 at 1.625 s, 33 s: A minor. A clock ticks while the ghost dozes and looks round,
    the beat drops at bar 5 with the waving, organ stabs for the pumps, a riser and a scream into the
    roof, then a bright chime for the chirp. Each costume change gets a gust."""
    B, t0 = 60 / 123, 1.625
    bar = lambda n: t0 + (n - 1) * 4 * B
    tr = Track(33)
    tr.add(0.0, organ(N("A1"), 9.5, 0.0), 0.1, 0.4)                # the drone under the intro
    tr.add(0.0, organ(N("E2"), 9.5, 0.0), 0.06, 0.4)
    wake = 1.893                                                  # where the face snaps awake: robotic-smile's first
                                                                  # step after DOZE in ghost.js DANCES (move both together)
    tr.add(0.1, howl(N("C#4"), N("G#4"), 1.0), 0.14, 0.95)         # DOZE: far off, a wolf howls...
    tr.add(wake - 1.3, howl(fall=0.93, r=0.04, d=1.33), 0.45, 0.6)  # ...then one close by, held right up to the wake
    chord(tr, wake, ["A4", "Bb4", "E5"], 0.5, 0.45, pluck, 0.3)      # ...the ghost jolts awake
    tr.add(wake - 0.1, whoosh(0.35), 0.35, 0.3)
    for k in range(16):                                            # bars 1-4: tick, tock
        tr.add(bar(1) + k * B, tick(), 0.25 if k % 2 == 0 else 0.15, 0.2)
    box = ["A5", "C6", "E6", "D#6", "E6", "C6", "B5", "G#5"]
    for b in (1, 2):
        for k, nm in enumerate(box):
            tr.add(bar(b) + k * B / 2, bell(N(nm), 1.0), 0.13, 0.6)
    tr.add(bar(3), theremin(N("E4"), N("E5"), 1.6), 0.28, 0.7)       # PEEK: a theremin swoop up
    tr.add(bar(4) + 2 * B, whoosh(1.0), 0.3, 0.5)                   # the riser into the drop
    prog = {5: "A", 6: "F", 7: "D", 8: "E", 9: "A", 10: "F", 11: "D", 12: "E", 13: "A", 14: "E"}
    roots = {"A": "A1", "F": "F1", "D": "D2", "E": "E1"}
    stabs = {"A": ["A3", "C4", "E4"], "F": ["F3", "A3", "C4"], "D": ["D3", "F3", "A3"], "E": ["E3", "G#3", "B3"]}
    for b, c in prog.items():
        s = bar(b)
        for k in range(4):
            tr.add(s + k * B, kick(), 1.1, 0.05)
            tr.add(s + k * B + B / 2, hat(), 0.5, 0.05)
            tr.add(s + k * B, bass(N(roots[c])), 0.7, 0.1)
            tr.add(s + k * B + B / 2, bass(N(roots[c]) + 12, 0.25), 0.3, 0.1)
        if b >= 9:                                                 # the pumps: organ stabs on 2 and 4
            for k in (1, 3):
                chord(tr, s + k * B, stabs[c], B * 0.8, 0.3)
    lead = ["E5", "F5", "E5", "D#5", "E5", "C5", "A4", "B4"]
    for b, shift in [(5, 0), (7, -12)]:                             # the right hand's tune, the left's
        for k, nm in enumerate(lead):
            tr.add(bar(b) + k * B, pluck(N(nm) + shift, 0.45), 0.25, 0.35)
    for t in (bar(7), bar(9), bar(11), bar(13), bar(15)):          # each costume change
        tr.add(t - 0.35, whoosh(0.7), 0.3, 0.6)
    tr.add(bar(13), theremin(N("A5"), N("E6"), 0.8, 0.04), 0.3, 0.6)  # EEK!: a scream
    for k in range(8):                                             # bars 13-14: rising organ
        tr.add(bar(13) + k * B, organ(N("A3") + k, B * 0.9), 0.14, 0.4)
    chord(tr, bar(15), ["A3", "C#4", "E4", "A4"], 3.6, 0.4)         # HAPPY: into the major
    for k, nm in enumerate(["A5", "C#6", "E6", "A6", "E6", "C#6"]):
        tr.add(bar(15) + k * B, bell(N(nm), 1.5), 0.16, 0.6)
    tr.add(bar(17), gong(N("A1"), 2.0), 0.3, 0.5)
    return tr.render(0.5)


def brass(m, d, amp_bend=0.0):             # a bright brass stab: a sawtooth opening up as it sounds
    t, f = T(d), hz(m)
    bright = 1 - np.exp(-t * 18)
    s = sum(np.sin(2 * np.pi * k * f * t * (1 + amp_bend * np.exp(-t * 20))) / k * (bright if k > 2 else 1) for k in range(1, 9))
    return s * env(len(t), 0.02, 0.08) * 0.45


def clap():                                # a handclap: three quick bursts of noise
    t = T(0.18)
    n = rng.standard_normal(len(t))
    n = np.diff(n, prepend=0)
    e = sum(np.exp(-np.clip(t - o, 0, None) * 45) * (t >= o) for o in (0, 0.012, 0.024))
    return n * e * 0.35


def crash(d=1.6):                          # a cymbal crash: bright noise, a long tail
    t = T(d)
    n = rng.standard_normal(len(t))
    n = n - np.convolve(n, np.ones(6) / 6, mode="same")
    return n * np.exp(-t * 2.4) * env(len(t), 0.002, 0.2) * 0.5


def celebrate():
    """10 s, no music of its own (120 bpm here): G major, a party. EEK! is a surprise (a brass stab and
    a crash, a theremin "whee!"); the beat starts on SMILE (kick, clap, a bouncing bass); HAPPY is the
    brass fanfare with bells; the cat's BEAT POP doubles the claps; the BOW lands on a big chord and a
    crash."""
    B = 0.5
    tr = Track(10)
    # EEK!: surprise!
    chord(tr, 0.0, ["G3", "B3", "D4", "G4"], 0.5, 0.55, brass, 0.4)
    tr.add(0.0, crash(1.4), 0.35, 0.4)
    tr.add(0.15, theremin(N("G4"), N("G6"), 0.9, 0.02), 0.22, 0.5)
    for k, nm in enumerate(["G5", "B5", "D6", "G6"]):                # a music-box sparkle
        tr.add(0.6 + k * B / 4, bell(N(nm), 0.9), 0.14, 0.6)
    # the party beat, from SMILE to the bow: kick on 1 and 3, claps on 2 and 4, hats between
    prog = ["G", "G", "E", "E", "C", "C", "D", "D"]                  # a bar each half: I vi IV V
    root = {"G": "G2", "E": "E2", "C": "C2", "D": "D2"}
    beat = 1.5
    while beat < 9.0 - 1e-6:
        k = round((beat - 1.5) / B)
        if k % 2 == 0: tr.add(beat, kick(), 0.8, 0.05)
        else: tr.add(beat, clap(), 0.65, 0.25)
        tr.add(beat + B / 2, hat(), 0.5, 0.05)
        r = N(root[prog[(k // 2) % len(prog)]])
        tr.add(beat, bass(r, 0.22), 0.65, 0.05)                     # bouncing octaves
        tr.add(beat + B / 2, bass(r + 12, 0.18), 0.45, 0.05)
        beat += B
    # SMILE: a cheeky organ riff over it
    for k, nm in enumerate(["D5", "B4", "G4", "B4", "D5", "E5", "D5", "B4"]):
        tr.add(1.5 + k * B / 2, organ(N(nm), 0.24), 0.32, 0.3)
    for k, nm in enumerate(["G4", "A4", "B4", "D5", "E5", "D5"]):
        tr.add(2.5 + k * B / 2, pluck(N(nm), 0.35), 0.32, 0.3)
    tr.add(3.65, whoosh(0.7), 0.3, 0.6)                             # into the pumpkin
    # HAPPY: the fanfare, brass with bells
    fan = [(4.0, "G4", 0.25), (4.25, "G4", 0.25), (4.5, "G4", 0.25), (4.75, "B4", 0.5), (5.25, "D5", 0.25),
           (5.5, "G5", 0.75), (6.25, "E5", 0.25), (6.5, "F#5", 0.5)]
    for t, nm, d in fan:
        tr.add(t, brass(N(nm), d), 0.3, 0.35)
        tr.add(t, brass(N(nm) - {"G": 3, "B": 4, "D": 3, "E": 4, "F": 4}[nm[0]], d), 0.16, 0.35)   # a third below, in G major
    chord(tr, 4.0, ["G3", "B3", "D4"], 1.0, 0.25)
    chord(tr, 5.0, ["C4", "E4", "G4"], 1.0, 0.25)
    chord(tr, 6.0, ["D4", "F#4", "A4"], 1.0, 0.25)
    for k, nm in enumerate(["G6", "D6", "B5", "G5", "B5", "D6", "G6", "B6"]):
        tr.add(4.0 + k * B / 2, bell(N(nm), 1.0), 0.12, 0.6)
    tr.add(4.0, crash(1.2), 0.25, 0.4)
    tr.add(6.65, whoosh(0.7), 0.3, 0.6)                             # into the cat
    # BEAT POP: claps on every beat, a meow-y theremin hop up on the off-beats
    for k in range(4):
        tr.add(7.0 + k * B, clap(), 0.6, 0.25)
        tr.add(7.0 + k * B + B / 2, theremin(N("D5"), N("G5"), 0.2, 0.0), 0.13, 0.4)
    for k, nm in enumerate(["D5", "E5", "F#5", "G5", "A5", "B5", "C6", "D6"]):   # a run up to the bow
        tr.add(8.0 + k * B / 4, pluck(N(nm), 0.25), 0.3, 0.3)
    # BOW: the big finish
    chord(tr, 9.0, ["G2", "D3", "G3", "B3", "D4", "G4"], 1.0, 0.55, brass, 0.45)
    chord(tr, 9.0, ["G3", "B3", "D4"], 1.0, 0.3)
    tr.add(9.0, crash(1.0), 0.45, 0.5)
    tr.add(9.0, kick(), 0.6, 0.05)
    return tr.render(0.35)


def write(path, x):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in [("wave-and-nod", wave_and_nod), ("robotic-smile", robotic_smile), ("celebrate", celebrate)]:
        out = OUT / f"halloween-{name}.wav"
        write(out, make())
        print(name, "→", out)
