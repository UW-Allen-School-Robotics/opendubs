"""The wake phrase, "Hey OpenDubs": the Thor's check that a wake turn really contained it.

The tablet spots the phrase on-device (sherpa-onnx keyword spotting in the face page; the Tablet
lane's side) and only then sends audio: about 1.5 s from before the wake point, then live audio. A
keyword spotter on its own sometimes fires on a near miss ("hey doves" did, on the desk). So every
wake turn is checked here, at no extra cost: Parakeet transcribes the turn anyway, and the turn goes
on only if the phrase is near its start. Anything else ends silently (turn status `not_addressed`):
a false wake never makes the robot speak.

The phrase was "hey dubs" until 2026-10-20. At about six phonemes it was too short to spot at a
normal distance, so the project team lengthened it to "Hey OpenDubs" (spoken "hey open dubs").

The check is loose on purpose, because the spotter has already said yes:
- The phrase may come after a few other words. The 1.5 s before the wake point often holds the end
  of whatever was said before, such as an earlier try, so "dubs. Hey OpenDubs, hi" is a wake.
- Parakeet writes it many ways: "Hey OpenDubs", "Hey, open dubs", "Hey Open Dubbs", or "open dubs"
  with the "hey" lost. Each word is matched by spelling distance (one edit), not one spelling.
- "Hey dubs" is still accepted HERE, because Parakeet sometimes drops the middle word of a real
  "hey open dubs". The page does not spot "hey dubs", so this costs no false wakes.
- Near misses are refused: "hey doves", "hey dogs", "hey, does it", "hey Dad", "open the door",
  "hey open the window".

`strip()` returns what was said after the phrase, which is what the language model gets.
"""
from __future__ import annotations

import re

PHRASE = "hey opendubs"
FIRST = {"hey", "hay", "hei", "heh", "he", "eh", "hi"}
DUBS = ("dubs", "dub")
OPEN = ("open",)
WINDOW = 6                    # the phrase must start within this many words of the transcript's start
WORD = re.compile(r"[A-Za-z']+")


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near(w: str, targets) -> bool:
    w = w.lower().replace("'", "")
    return any(_distance(w, t) <= 1 for t in targets)


def _is_opendubs(w: str) -> bool:
    """One word: "OpenDubs", "Opendubbs"."""
    w = w.lower().replace("'", "")
    return w.startswith(("open", "opun", "upen")) and len(w) > 5 and _near(w[4:], DUBS)


def _match(words: list, i: int) -> int | None:
    """The index of the phrase's last word if it starts at word i, else None."""
    def g(k):
        return words[k].group().lower() if k < len(words) else ""
    w = g(i)
    if w in FIRST:
        if _near(g(i + 1), OPEN) and _near(g(i + 2), DUBS):
            return i + 2                                           # "hey open dubs"
        if _is_opendubs(g(i + 1)) or _near(g(i + 1), DUBS):
            return i + 1                                           # "hey opendubs", "hey dubs"
        return None
    if _near(w, OPEN) and _near(g(i + 1), DUBS):
        return i + 1                                               # "open dubs", the "hey" lost
    if _is_opendubs(w):
        return i                                                   # "OpenDubs" alone
    for f in ("hey", "hay"):                                       # run together: "Heyopendubs", "Heydubs"
        rest = w[len(f):]
        if w.startswith(f) and len(rest) > 1 and (_is_opendubs(rest) or _near(rest, DUBS)):
            return i
    return None


def strip(transcript: str) -> str | None:
    """None if the wake phrase is not near the transcript's start; otherwise what follows it, which
    may be empty ("Hey OpenDubs." on its own)."""
    text = transcript or ""
    words = list(WORD.finditer(text))
    for i in range(min(len(words), WINDOW)):
        last = _match(words, i)
        if last is not None:
            return text[words[last].end():].lstrip(" ,.!?;:-").strip()
    return None
