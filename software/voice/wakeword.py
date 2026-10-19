"""The wake word, "hey dubs": the Thor's check that a wake turn really began with it.

The tablet spots the keyword on-device (sherpa-onnx keyword spotting in the face page; the Tablet
lane's side) and only then sends audio. A keyword spotter on its own sometimes fires on a near miss
("hey doves" did, on the desk). So every wake turn is checked here, at no extra cost: Parakeet
transcribes the turn anyway, and the turn proceeds only if the transcript starts with the phrase.
Anything else ends silently (turn status `not_addressed`): a false wake never makes the robot speak.

`strip()` returns what was said after the phrase, which is what the language model gets.
"""
from __future__ import annotations

import re

PHRASE = "hey dubs"
# How Parakeet writes it (seen on the robot: "Hey Dubs.", "Hey dubs, what is your name?"), and the
# near spellings an accent gives. The second word must still be "dub"/"dubs": "Hey doves.", "Hey
# Dad.", "Hey, does it work?" and "Hey dogs" are all refused.
FIRST = {"hey", "hay", "hei", "heh"}
SECOND = re.compile(r"^dub(s|z|'s)?$")
WORD = re.compile(r"[A-Za-z']+")


def strip(transcript: str) -> str | None:
    """None if the transcript does not start with the wake phrase; otherwise the rest of it, which
    may be empty ("Hey dubs." on its own)."""
    words = list(WORD.finditer(transcript or ""))
    if len(words) >= 2 and words[0].group().lower() in FIRST and SECOND.match(words[1].group().lower()):
        rest = transcript[words[1].end():]
    elif words and words[0].group().lower().startswith("heydub") and SECOND.match(words[0].group().lower()[3:]):
        rest = transcript[words[0].end():]                     # "Heydubs, ..." run together
    else:
        return None
    return rest.lstrip(" ,.!?;:-").strip()
