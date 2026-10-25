#!/usr/bin/env python3
"""The listener's promises: a spotter hit alone posts nothing, "trick or treat" counts only while a
program is armed and Parakeet agrees, a bystander's sentence is not an approval, and the robot's
own voice is never heard.

    python3 software/architect/tests/test_listener.py      # stdlib only
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import kws, listener as ls  # noqa: E402

import audio  # noqa: E402  (software/voice, on the path through listener)
import vad  # noqa: E402

RATE = 16000


def frames(pcm: bytes):
    return list(audio.chunks(pcm, RATE, ls.FRAME_MS))


QUIET = frames(audio.noise(3000, RATE, amp=0.003))
SPEECH = frames(audio.mix(audio.noise(1200, RATE, amp=0.003), audio.tone(220, 1200, RATE, amp=0.3, am_hz=4)))


class Spotter:
    """Fires `keyword` on the frame `at` after it is armed with `fire`."""

    def __init__(self):
        self.pending, self.fed, self.resets = None, 0, 0

    def fire(self, keyword, at=0):
        self.pending = [keyword, at]

    def feed(self, pcm):
        self.fed += 1
        if self.pending is not None:
            if self.pending[1] <= 0:
                kw, self.pending = self.pending[0], None
                return [kw]
            self.pending[1] -= 1
        return []

    def reset(self):
        self.resets += 1


class Case(unittest.TestCase):
    def make(self, heard, state="IDLE"):
        self.posted = []
        self.spotter = Spotter()
        self.t = [0.0]

        def post(kind, text):
            self.posted.append((kind, text))
            return None

        li = ls.Listener(self.spotter, lambda pcm: heard, post, make_vad=vad.EnergyVAD,
                         clock=lambda: self.t[0], background=False)
        li.state = state
        return li

    def feed(self, li, fs):
        for f in fs:
            li.frame(f)
            self.t[0] += ls.FRAME_MS / 1000


class Wake(Case):
    def test_an_instruction_after_the_phrase(self):
        li = self.make("Hey OpenDubs, hand out candy.")
        self.feed(li, QUIET[:20])
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET)
        self.assertEqual(self.posted, [("instruction", "hand out candy.")])

    def test_the_jabra_spellings(self):
        """How Parakeet wrote a real "Hey OpenDubs" on the Jabra, and the near misses still refused."""
        for text, rest in (("Hey, opened up.", ""), ("Hey overdubs, hand out candy", "hand out candy"),
                           ("Hey OpenDubs, stop", "stop"), ("A open dubs, yes.", "yes."),
                           ("I opened dubs, wave hello.", "wave hello."), ("I opened up's wave hello.", "wave hello."), ("Hey Oaken Ducks. No.", "No."),
                           ("Hey, open dogs, give the candy", "give the candy")):
            self.assertEqual(ls.wake_rest(text), rest, text)
        for text in ("hey doves", "hey dogs", "open the door", "hey, open the window", "open doors", "it opened up"):
            self.assertIsNone(ls.wake_rest(text), text)

    def test_a_second_phrase_starts_a_second_turn(self):
        """The second take: "Hey OpenDubs, yes. Hey OpenDubs, no." was one turn, so "no" was lost."""
        li = self.make("Hey OpenDubs, yes.")
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET[:10])     # 0.2 s of quiet: not yet the end of the turn
        self.assertEqual(self.posted, [])
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:1])
        self.assertEqual(self.posted, [("instruction", "yes.")])   # the first turn, ended by the second phrase
        self.assertEqual(li.turn[0], "wake")                       # and the second has begun

    def test_a_near_miss_posts_nothing(self):
        li = self.make("Hey doves, hand out candy.")
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET)
        self.assertEqual(self.posted, [])


class Trick(Case):
    def test_only_while_armed_and_confirmed(self):
        for state, heard, want in (("ARMED", "Trick or treat!", [("trigger", "trick or treat")]),
                                   ("ARMED", "the trick is to eat", []),
                                   ("IDLE", "Trick or treat!", []),
                                   ("AWAITING_YES", "Trick or treat!", [])):
            li = self.make(heard, state)
            self.spotter.fire(kws.TRICK, at=40)
            self.feed(li, SPEECH + QUIET[:40])
            self.assertEqual([p for p in self.posted if p[0] == "trigger"], want, (state, heard))

    def test_how_parakeet_writes_it(self):
        for text in ("Trick or treat!", "trick-or-treat", "Trick r treat", "trick a treat", "Trik or treats",
                     "check or cheat.", "Trigger treat."):
            self.assertTrue(ls.is_trick_or_treat(text), text)
        for text in ("treat or trick", "the trick is", "treat yourself", "trick or tree house is", "Yeah.",
                     "pick or choose", "pull the trigger"):
            self.assertFalse(ls.is_trick_or_treat(text), text)


class OpenWindow(Case):
    def test_a_short_yes_approves_and_a_sentence_does_not(self):
        li = self.make("Yes.", "AWAITING_YES")
        self.feed(li, QUIET[:20] + SPEECH + QUIET)
        self.assertEqual(self.posted, [("speech", "Yes.")])
        li = self.make("yeah my kid wants the red one", "AWAITING_YES")
        self.feed(li, QUIET[:20] + SPEECH + QUIET)
        self.assertEqual(self.posted, [])

    def test_no_open_listening_while_armed(self):
        li = self.make("Yes.", "ARMED")
        self.feed(li, QUIET[:20] + SPEECH + QUIET)
        self.assertEqual(self.posted, [])


class OwnVoice(Case):
    def test_deaf_while_speaking_and_after(self):
        li = self.make("Trick or treat!", "ARMED")
        li.speaking = True
        self.feed(li, SPEECH)
        self.assertEqual(self.spotter.fed, 0)
        li.said()
        self.feed(li, QUIET[:int(ls.TAIL_S * 1000 / ls.FRAME_MS)])
        self.assertEqual(self.spotter.fed, 0)
        self.feed(li, QUIET[:1])
        self.assertEqual((self.spotter.fed, self.spotter.resets), (1, 1))   # starts clean


if __name__ == "__main__":
    unittest.main()
