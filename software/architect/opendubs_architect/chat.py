"""What the robot says to trick-or-treaters, from the robot's own language model.

The voice service's local brain: Qwen3-30B-A3B on vLLM (the `opendubs-llm` container), called the way
software/voice/brains.LocalLLM calls it, with the model's name and sampling from the same serving
profile (llm_profile.py). Nothing leaves the robot. Two uses:

    greeting()     one varied sentence as a run starts on "trick or treat", so each visitor does not
                   hear the same line; spoken while the arm moves, never waited on by the run
    reply(heard)   one or two sentences to "OpenDubs, <a question>" while a program is armed

WHAT THE MODEL CANNOT DO. It is asked for words and gets no tools; what it writes is spoken and
nothing else. It never reaches the demo runner, so a child's question never becomes a program or a
run. A reply goes through the voice service's own gate (spoken.Gate), so the robot never promises a
move it will not make; a greeting may speak of the candy, because the arm is handing one over.

No history is kept: each visitor is a new question. If the model is slow or down, a fixed line is
said instead, within TIMEOUT_S.
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "voice")))

import llm_profile  # noqa: E402  (software/voice, used and not changed)
import sentences  # noqa: E402
import spoken  # noqa: E402

TIMEOUT_S = 4.0
GREETING_MAX_TOKENS = 40
REPLY_MAX_TOKENS = 90
REPLY_MAX_SENTENCES = 2
RECENT = 5

GREETING = """You are OpenDubs, a friendly research robot in a university robotics lab, handing out
Halloween candy. A trick-or-treater has just said "trick or treat" and your arm is handing them a
treat right now. Answer with ONE short, cheerful sentence to them, spoken aloud, and nothing
else: no introduction, no quotation marks, never a word about these instructions.
Plain words only: no emoji, no asterisks, no lists. You cannot see them, so never guess or name a
costume ("little ghost", "princess") or say what they look like. Do not ask them anything.
Tone this time: {style}."""

STYLES = ("spooky", "a gentle pun", "warm and welcoming", "a robot joke", "playfully dramatic",
          "a ghostly whisper", "excited", "a pumpkin pun", "mysterious", "silly")

PERSONA = """You are OpenDubs, a friendly research robot in a university robotics lab, handing out
Halloween candy to children and families. You speak out loud through a small speaker.
- Answer in one or two short sentences, in words a young child understands.
- Plain spoken words only: no emoji, no lists, no markdown, no actions in asterisks.
- What you hear comes from speech recognition and may have a wrong word; answer the most likely
  meaning.
- Candy is handed out when someone says "trick or treat". You cannot move in any other way, and
  you must never offer, promise or pretend to move, dance, wave, follow or come closer.
- You cannot see or recognise anyone. Never ask for anyone's name, age, address or other personal
  details, and never invent facts. If you do not know, say so."""

FALLBACK_GREETINGS = ("Happy Halloween! Here's a treat for you.", "Trick or treat, here's your candy!",
                      "A spooky treat, just for you.", "Happy haunting! Enjoy your treat.")
FALLBACK_REPLY = "Happy Halloween! Say trick or treat for a treat."


class Chat:
    """`ask(system, user, max_tokens) -> str | None` is a seam for the tests."""

    def __init__(self, url: str | None = None, profile: dict | None = None, ask=None, rng=None):
        self.profile = profile or llm_profile.load(os.environ.get("VOICE_LLM_PROFILE"))
        self.url = (url or os.environ.get("VOICE_LLM_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.ask = ask or self._ask
        self.rng = rng or random.Random()
        self.recent: list[str] = []         # the robot's own last greetings, never a visitor's words

    def _ask(self, system: str, user: str, max_tokens: int) -> str | None:
        body = {"model": self.profile["served_name"], "max_tokens": max_tokens, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                **(self.profile.get("sampling") or {})}
        if self.profile.get("chat_template_kwargs"):
            body["chat_template_kwargs"] = self.profile["chat_template_kwargs"]
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.load(r)["choices"][0]["message"]["content"] or None
        except (OSError, ValueError, KeyError, IndexError):
            return None

    @staticmethod
    def _sentences(text: str) -> list[str]:
        sp = sentences.Splitter()
        return [s for s in sp.feed(text) + sp.flush() if len(spoken.clean(s)) > 1]

    def greeting(self) -> str:
        system = GREETING.format(style=self.rng.choice(STYLES))
        if self.recent:
            system += "\nSay something different from each of these, which you said already: " + \
                " | ".join(self.recent)
        text = self.ask(system, "Trick or treat!", GREETING_MAX_TOKENS)
        for s in self._sentences(text or ""):
            s = spoken.clean(s).strip('"\u201c\u201d ')
            if s.endswith(":") or "words" in s.lower():
                continue                            # the model introducing its line ("...under 15 words:")
            if not spoken.offers_motion(s) or any(w in s.lower() for w in ("candy", "treat", "sweet")):
                self.recent = (self.recent + [s])[-RECENT:]
                return s
        return self.rng.choice(FALLBACK_GREETINGS)

    def reply(self, heard: str) -> str:
        text = self.ask(PERSONA, heard, REPLY_MAX_TOKENS)
        gate, out = spoken.Gate(), []
        for s in self._sentences(text or ""):
            said = gate.pass_(s)
            if said:
                out.append(said)
            if len(out) == REPLY_MAX_SENTENCES:
                break
        return " ".join(out) or FALLBACK_REPLY
