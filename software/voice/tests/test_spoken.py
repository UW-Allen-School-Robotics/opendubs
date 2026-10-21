#!/usr/bin/env python3
"""What the robot may say out loud: no emoji or markdown, and no offer of motion, whatever the model
writes. A regression here is heard by a visitor ("Let me show you a little move" happened).

    python3 software/voice/tests/test_spoken.py
"""
import unittest

from fakes import connect, make_voice, say, settle, speech_clip

import spoken
from brains import FakeLLM


class Speech(unittest.TestCase):
    def test_symbols_are_not_spoken(self):
        cases = {
            "Sure thing! 😊": "Sure thing!",
            "I love tidy cables 🤖✨.": "I love tidy cables.",
            "**Hello** there, I'm _OpenDubs_.": "Hello there, I'm OpenDubs.",
            "- I have two arms.": "I have two arms.",
            "1. I have a lift.": "I have a lift.",
            "*waves happily* Hi there!": "Hi there!",
            "Thumbs up 👍🏽 from me.": "Thumbs up from me.",
            "I run on an NVIDIA Jetson.": "I run on an NVIDIA Jetson.",
        }
        for raw, said in cases.items():
            self.assertEqual(spoken.clean(raw), said, raw)

    def test_offers_of_motion_are_caught(self):
        for s in ("Let me show you a little move!", "Let me show you my moves.", "I'll do a little dance for you.",
                  "Watch me spin!", "I'm going to drive over to you.", "I can follow you to the door.",
                  "Want me to wave at you?", "Let me grab that for you.", "I'll come closer.",
                  "How about I give you a tour?", "I could pick it up for you.", "Let's do a high five!",
                  "I will raise my arm.", "Here's a quick dance."):
            self.assertTrue(spoken.offers_motion(s), s)

    def test_plain_talk_passes(self):
        for s in ("My operator controls how I move.", "I have two arms and a lift.",
                  "I perform shows that my operator starts.", "I can tell you about the lab.",
                  "I can change my expression.", "Let me think about that.", "I'll tell you a joke.",
                  "I like people who say thank you.", "I can't move on my own."):
            self.assertFalse(spoken.offers_motion(s), s)

    def test_the_first_offer_becomes_the_honest_line(self):
        g = spoken.Gate()
        said = [g.pass_(s) for s in ("Sure!", "Let me show you a little move!", "I'll spin too.", "🎉")]
        self.assertEqual(said, ["Sure!", spoken.NO_MOTION, None, None])
        self.assertEqual(g.dropped, 2)


class Reply(unittest.IsolatedAsyncioTestCase):
    async def test_what_happened_on_the_robot_is_not_said_again(self):
        v = make_voice(llm=FakeLLM(["Oh, fun! Let me show you a little move. 💃 I'm OpenDubs."]))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(v.tts.spoken, ["Oh, fun!", spoken.NO_MOTION, "I'm OpenDubs."])
        self.assertEqual(v.history[-1]["text"], " ".join(v.tts.spoken))   # the history holds what was said
        self.assertEqual(link.of("turn")[-1]["reply"], " ".join(v.tts.spoken))


if __name__ == "__main__":
    unittest.main()
