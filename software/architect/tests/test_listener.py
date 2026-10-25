#!/usr/bin/env python3
"""The listener's promises: a spotter hit alone posts nothing, "trick or treat" counts only while a
program is armed and Parakeet agrees, a bystander's sentence is not an approval, and the robot's
own voice is never heard.

    python3 software/architect/tests/test_listener.py      # stdlib only
"""
import math
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

    def test_never_an_instruction_or_a_reply(self):
        """A child's "Hey OpenDubs, trick or treat" before anything is armed never reaches the model."""
        for state in ("IDLE", "AWAITING_YES", "CHECK_FAILED"):
            li = self.make("Hey OpenDubs, trick or treat!", state)
            self.spotter.fire(kws.HEY)
            self.feed(li, QUIET[:5] + SPEECH + QUIET)
            self.assertEqual(self.posted, [], state)
        li = self.make("Hey OpenDubs, trick or treat!", "ARMED")
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET)
        self.assertEqual(self.posted, [("trigger", "trick or treat")])

    def test_how_parakeet_writes_it(self):
        for text in ("Trick or treat!", "trick-or-treat", "Trick r treat", "trick a treat", "Trik or treats",
                     "check or cheat.", "Trigger treat.", "or treat."):
            self.assertTrue(ls.is_trick_or_treat(text), text)
        for text in ("treat or trick", "the trick is", "treat yourself", "trick or tree house is", "Yeah.",
                     "pick or choose", "pull the trigger", "a treat", "here's a treat", "more treats"):
            self.assertFalse(ls.is_trick_or_treat(text), text)


class FakeChat:
    def greeting(self):
        return "Happy haunting!"

    def reply(self, heard):
        return f"You asked: {heard}"


class Chat(Case):
    def armed(self, heard, post_state=None):
        li = self.make(heard, "ARMED")
        self.said = []
        li.chat, li.say = FakeChat(), self.said.append
        li.post = lambda kind, text: (self.posted.append((kind, text)), post_state)[1]
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET)
        return li

    def test_a_question_while_armed_is_answered_and_never_posted(self):
        self.armed("Hey OpenDubs, what is your name?")
        self.assertEqual(self.posted, [])
        self.assertEqual(self.said, ["You asked: what is your name?"])

    def test_stop_still_reaches_the_runner(self):
        self.armed("Hey OpenDubs, stop!")
        self.assertEqual((self.posted, self.said), ([("speech", "stop!")], []))

    def test_a_greeting_once_the_run_has_started(self):
        self.armed("Hey OpenDubs, trick or treat!", post_state="RUNNING")
        self.assertEqual((self.posted, self.said), ([("trigger", "trick or treat")], ["Happy haunting!"]))

    def test_hold_on_when_no_run_starts(self):
        """A "trick or treat" during a run, or refused for the deadman, gets a word instead of silence."""
        li = self.armed("Hey OpenDubs, trick or treat!", post_state="ARMED")     # refused
        self.assertEqual(len(self.said), 1)
        self.assertIn(self.said[0], ls.HOLD_ON)
        li.state, now = "RUNNING", self.t[0]
        li.clock = lambda: now                                               # a crowd, all at once
        for _ in range(3):
            self.spotter.fire(kws.HEY)
            self.feed(li, QUIET[:5] + SPEECH + QUIET)
        self.assertEqual(len(self.said), 1)
        li.clock = lambda: now + ls.HOLD_ON_EVERY_S
        self.spotter.fire(kws.HEY)
        self.feed(li, QUIET[:5] + SPEECH + QUIET)
        self.assertEqual(len(self.said), 2)
        self.assertNotEqual(self.said[0], self.said[1])                      # not the same line twice running

    def test_what_the_model_may_say(self):
        from opendubs_architect import chat
        c = chat.Chat(profile={"served_name": "x"}, ask=lambda *a: "Sure, let me dance for you! I love Halloween.")
        self.assertEqual(c.reply("can you dance"), "I can't move on my own; my operator controls that. I love Halloween.")
        c = chat.Chat(profile={"served_name": "x"}, ask=lambda *a: "Let me hand you a candy! **Boo!**")
        self.assertEqual(c.greeting(), "Let me hand you a candy!")          # the arm is handing one over
        c = chat.Chat(profile={"served_name": "x"}, ask=lambda *a: None)    # the model down
        self.assertIn(c.greeting(), chat.FALLBACK_GREETINGS)
        self.assertEqual(c.reply("hi"), chat.FALLBACK_REPLY)


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


class Shows(unittest.TestCase):
    """--show: "trick or treat" plays a routine through the dashboard, with the Shows page's gates."""

    def shows(self, replies):
        self.calls = []

        def call(method, path, body):
            self.calls.append((method, path, body))
            return replies.get(path, (404, None))
        return ls.Shows("http://dash", "monster_mash", log=lambda ev: None, call=call)

    def test_a_trigger_arms_the_show_for_the_deadman(self):
        sh = self.shows({"/api/choreo/run/monster_mash": (200, {"started": "monster_mash", "live": True})})
        self.assertEqual(sh.post("trigger", "trick or treat"), "RUNNING")
        self.assertEqual(self.calls[-1], ("POST", "/api/choreo/run/monster_mash", {"start": "deadman"}))

    def test_a_refusal_is_ready_again_and_nothing_else_is_posted(self):
        sh = self.shows({"/api/choreo/run/monster_mash": (409, {"error": "'reach' is still running"}),
                         "/api/choreo/stop": (200, {"stopped": True})})
        self.assertEqual(sh.post("trigger", "trick or treat"), "ARMED")     # the listener says HOLD_ON
        self.assertEqual(sh.post("speech", "stop!"), "ARMED")
        self.assertEqual(self.calls[-1][1], "/api/choreo/stop")
        n = len(self.calls)
        self.assertIsNone(sh.post("instruction", "hand out candy"))         # no program, no prompt
        self.assertIsNone(sh.post("speech", "yes"))
        self.assertEqual(len(self.calls), n)

    def test_state_follows_the_palette_and_unknown_is_none(self):
        sh = self.shows({"/api/choreo/palette": (200, {"running": "monster_mash"})})
        self.assertEqual(sh.state(), "RUNNING")
        sh = self.shows({"/api/choreo/palette": (200, {"running": None})})
        self.assertEqual(sh.state(), "ARMED")
        sh = self.shows({})
        self.assertIsNone(sh.state())

    def test_an_unknown_or_invalid_routine_refuses_to_start(self):
        with self.assertRaises(SystemExit):
            self.shows({"/api/choreo/routines": (200, [{"id": "reach", "valid": True}])}).check()
        with self.assertRaises(SystemExit):
            self.shows({"/api/choreo/routines": (200, [{"id": "monster_mash", "valid": False}])}).check()


class Loudness(unittest.TestCase):
    def test_a_quiet_line_is_brought_up(self):
        """Kokoro's lines, peaking near -19 dBFS, were not heard through the Jabra."""
        quiet = audio.tone(300, 500, 24000, amp=0.11)
        peak = max(abs(x) for x in audio.samples(ls.loud(quiet)))
        self.assertAlmostEqual(20 * math.log10(peak / 32767), ls.PEAK_DBFS, delta=0.2)
        self.assertEqual(ls.loud(audio.silence(100, 24000)), audio.silence(100, 24000))   # silence stays silent


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
