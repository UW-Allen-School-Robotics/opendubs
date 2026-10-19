"""The wake word, "hey dubs": the Thor's check that a wake turn really contained it.

The tablet spots the keyword on-device (sherpa-onnx keyword spotting in the face page; the Tablet
lane's side) and only then sends audio: about 1.5 s from before the wake point, then live audio. A
keyword spotter on its own sometimes fires on a near miss ("hey doves" did, on the desk). So every
wake turn is checked here, at no extra cost: Parakeet transcribes the turn anyway, and the turn goes
on only if the phrase is near its start. Anything else ends silently (turn status `not_addressed`):
a false wake never makes the robot speak.

The check is loose on purpose, because the spotter has already said yes:
- The phrase may come after a few other words. The 1.5 s before the wake point often holds the end
  of whatever was said before, such as an earlier "hey dubs", so "dubs. Hey dubs, hi" is a wake.
- "Dubs" is matched by spelling distance, not one spelling: Parakeet writes "Dubs", "Dubbs",
  "Dub's", "Doubs", "Debs", or runs it into "Heydubs". Words two edits away ("doves", "dogs",
  "does", "Dad") are refused.

`strip()` returns what was said after the phrase, which is what the language model gets.
"""
from __future__ import annotations

import re

PHRASE = "hey dubs"
FIRST = {"hey", "hay", "hei", "heh", "he", "eh", "hi"}
TARGETS = ("dubs", "dub")
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


def _is_dubs(w: str) -> bool:
    w = w.lower().replace("'", "")
    return any(_distance(w, t) <= 1 for t in TARGETS)


def strip(transcript: str) -> str | None:
    """None if the wake phrase is not near the transcript's start; otherwise what follows it, which
    may be empty ("Hey dubs." on its own)."""
    text = transcript or ""
    words = list(WORD.finditer(text))
    for i in range(min(len(words), WINDOW)):
        w = words[i].group().lower()
        if w in FIRST and i + 1 < len(words) and _is_dubs(words[i + 1].group()):
            end = words[i + 1].end()
        elif any(w.startswith(f) and len(w) > len(f) + 1 and _is_dubs(w[len(f):]) for f in ("hey", "hay")):
            end = words[i].end()                                   # "Heydubs", run together
        else:
            continue
        return text[end:].lstrip(" ,.!?;:-").strip()
    return None
