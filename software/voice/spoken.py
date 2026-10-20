"""What the robot may say out loud, enforced on every sentence the model writes, not only asked of it.

The persona asks for both, and a model sometimes ignores it. On the robot it said "Let me show you a
little move", with an emoji. So two rules are held here, after the model and before speech:

- **Nothing that is not speech.** Emoji, markdown marks, list bullets and stage directions in
  asterisks ("*spins*") are removed: replies are spoken, and TTS reads a symbol aloud or garbles it.
- **No offer or claim of motion.** The robot cannot move itself by voice; a person with the radio
  transmitter drives it, and shows are started by the operator (V-D3). A sentence that offers or
  claims a move, a dance, driving, following or using the arms is dropped. The first one in a reply
  is replaced by NO_MOTION, so the visitor hears why instead of silence.

The second rule is deliberately broad on offers ("let me show you a move", "I'll drive over") and
narrow on description ("I perform shows my operator starts" passes): a dropped sentence costs a
little charm, a spoken false promise costs trust.
"""
from __future__ import annotations

import re
import unicodedata

NO_MOTION = "I can't move on my own; my operator controls that."

BOLD = re.compile(r"\*\*(.+?)\*\*")                          # "**Hello**" is emphasis, kept as words
STAGE = re.compile(r"(?<!\w)\*[^*\n]{1,80}\*(?!\w)")          # "*does a little spin*" is an action, dropped
GAP = re.compile(r"\s+([.,!?;:])")
MARKS = re.compile(r"[*_#`>~|\\]+")
BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+")
SPACE = re.compile(r"\s+")

# An offer or promise of motion: a lead ("let me", "I'll", "watch me" ...) then a motion within a
# few words. And a claim of ability for the moves a visitor would expect it to do on request.
_LEAD = r"(?:let me|let's|i'?ll|i will|i'?m going to|i am going to|i'?m gonna|watch me|here'?s|how about i|shall i|want me to|i could|i can)"
_MOVE = (r"(?:show you (?:a |my |some |the )?(?:little |quick |few |new )?(?:moves?|dance|dances|spin|trick|tricks|wave|steps?)"
         r"|bust (?:a|out)|do (?:a |my |some |the )?(?:little |quick )?(?:moves?|dance|spin|twirl|wave|trick|jig|robot)"
         r"|dance|spin|twirl|wave (?:at|to|my)|bow|nod my head|turn (?:around|my head|to)|look (?:over )?(?:at|toward) you"
         r"|drive|roll|move (?:over|closer|forward|back|away|toward|towards|to)|come (?:over|closer|to)|go (?:over|to)|head over"
         r"|follow you|lead you|take you|bring you|give you a tour|show you around"
         r"|grab|reach|pick (?:it|that|this|them|something|you) up|lift|raise (?:my|an) arm|wave my arm|hand (?:you|it|that)"
         r"|high.?five|fist.?bump|shake (?:your )?hands?|hug|point (?:at|to))")
OFFER = re.compile(rf"\b{_LEAD}\b(?:\W+\w+){{0,4}}?\W+{_MOVE}\b", re.IGNORECASE)


def _no_symbols(s: str) -> str:
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk", "Cs", "Co") or ch in "\u200d\ufe0f\ufe0e\u20e3":
            continue                                          # emoji, their joiners and selectors
        out.append(ch)
    return "".join(out)


def clean(sentence: str) -> str:
    """The sentence as speech: no emoji, markdown, bullets or stage directions."""
    s = STAGE.sub(" ", BOLD.sub(r"\1", sentence))
    s = _no_symbols(s)
    s = BULLET.sub("", s)
    s = MARKS.sub("", s)
    return GAP.sub(r"\1", SPACE.sub(" ", s)).strip()


def offers_motion(sentence: str) -> bool:
    return bool(OFFER.search(sentence))


class Gate:
    """One reply's sentences through both rules. `pass_(s)` gives what to say, or None."""

    def __init__(self) -> None:
        self.dropped = 0

    def pass_(self, sentence: str) -> str | None:
        s = clean(sentence)
        if len(s) < 2 or not any(ch.isalnum() for ch in s):
            return None
        if offers_motion(s):
            self.dropped += 1
            return NO_MOTION if self.dropped == 1 else None
        return s
