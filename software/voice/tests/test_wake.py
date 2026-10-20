#!/usr/bin/env python3
"""The wake word, "hey dubs": the transcript check, the end-of-utterance detector on fake audio, and
whole wake turns, including the silent ones a false wake must give.

    python3 software/voice/tests/test_wake.py
"""
import asyncio
import json
import unittest

from fakes import FakeGate, connect, make_voice, settle, silent_clip, speech_clip

import audio
import vad
import wakeword
import wire
from brains import FakeLLM
from speech import FakeSTT


class Phrase(unittest.TestCase):
    def test_accepts_how_parakeet_writes_it(self):
        """Transcripts seen on the robot, and near spellings."""
        cases = {
            "Hey Dubs.": "",
            "Hey dubs, what is your name?": "what is your name?",
            "Hey dubs can you hear me?": "can you hear me?",
            "hey dub, are you a robot": "are you a robot",
            "Hay Dubs! Tell me a joke.": "Tell me a joke.",
            "Heydubs, hi": "hi",
            "Hey Dub's, hello": "hello",
            "Hey Dubbs, what is your name?": "what is your name?",
            "Hey, Doubs.": "",
            "Hey Debs, hello": "hello",
            "hey dubz": "",
            # the pre-roll holds the end of what came before, often an earlier try
            "Dubs. Hey dubs, what time is it?": "what time is it?",
            "Okay, so hey dubs, are you there?": "are you there?",
        }
        for t, rest in cases.items():
            self.assertEqual(wakeword.strip(t), rest, t)

    def test_refuses_near_misses(self):
        """Seen on the robot: the near-miss clips transcribed as these."""
        for t in ("Hey doves.", "Hey Dad.", "Hey, does it work?", "Hey dogs, what is your name?",
                  "Hey Tom. Can you hear me?", "dubs hey", "What is your name?", "", "Hey",
                  "I told you we could go there and then hey dubs"):     # too far in: not a wake
            self.assertIsNone(wakeword.strip(t), t)


def frames(pcm):
    step = wire.UP_FRAME_SAMPLES * 2
    return [pcm[i:i + step] for i in range(0, len(pcm), step)]


class Endpointing(unittest.TestCase):
    def run_ep(self, pcm, pre_roll_ms, **kw):
        ep = vad.Endpointer(vad.EnergyVAD(), pre_roll_ms, **kw)
        for i, f in enumerate(frames(pcm)):
            r = ep.feed(f)
            if r:
                return r, (i + 1) * 20
        return None, len(frames(pcm)) * 20

    def test_speech_after_the_wake_word_then_silence_ends_it(self):
        room = audio.noise(300, wire.UP_RATE, amp=0.003)
        keyword = audio.mix(audio.noise(600, wire.UP_RATE, amp=0.003), audio.tone(220, 600, wire.UP_RATE, amp=0.3, am_hz=4))
        question = audio.mix(audio.noise(1500, wire.UP_RATE, amp=0.003), audio.tone(200, 1500, wire.UP_RATE, amp=0.3, am_hz=4))
        quiet = audio.noise(2000, wire.UP_RATE, amp=0.003)
        r, at = self.run_ep(room + keyword + audio.noise(500, wire.UP_RATE, amp=0.003) + question + quiet, pre_roll_ms=900)
        self.assertEqual(r, "end")
        self.assertGreater(at, 900 + 500 + 1500)              # not before the question ended
        self.assertLess(at, 900 + 500 + 1500 + 1500)          # but soon after, on the silence

    def test_nothing_said_after_the_wake_word(self):
        keyword = audio.mix(audio.noise(900, wire.UP_RATE, amp=0.003), audio.tone(220, 900, wire.UP_RATE, amp=0.3, am_hz=4))
        r, at = self.run_ep(keyword + audio.noise(6000, wire.UP_RATE, amp=0.003), pre_roll_ms=900, start_timeout_s=2.0)
        self.assertEqual(r, "no_speech")
        self.assertAlmostEqual(at, 900 + 2000, delta=60)

    def test_the_keywords_tail_is_not_the_question(self):
        """Speech running across the wake point (the end of "dubs") must not count as the question
        starting, or a pause after the keyword would end the turn at once."""
        keyword = audio.mix(audio.noise(1100, wire.UP_RATE, amp=0.003), audio.tone(220, 1100, wire.UP_RATE, amp=0.3, am_hz=4))
        r, _ = self.run_ep(keyword + audio.noise(1500, wire.UP_RATE, amp=0.003), pre_roll_ms=900, start_timeout_s=10)
        self.assertIsNone(r)                                  # still waiting for the question

    def test_silero_backend_seam(self):
        class Det:
            def __init__(self):
                self.n = 0

            def accept_waveform(self, x):
                self.n += 1
                assert all(-1.0 <= v < 1.0 for v in x)

            def is_speech_detected(self):
                return self.n % 2 == 0
        v = vad.SileroVAD("unused", detector=Det())
        self.assertEqual([v.speech(f) for f in frames(speech_clip(80))], [False, True, False, True])

    def test_make_vad_falls_back_without_the_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertIsInstance(vad.make_vad(d), vad.EnergyVAD)


async def wake(v, page, pcm, pre_roll_ms=600):
    await v.on_text(page, json.dumps({"type": "wake", "keyword": "hey dubs", "pre_roll_ms": pre_roll_ms}))
    for i, f in enumerate(frames(pcm)):
        await v.on_bytes(page, wire.pack_up(i, i * 20, f))
        if v.state != "listen":
            break


def utterance():
    """A keyword, a pause, a question, then the room: what a page sends after a hit."""
    room = lambda ms: audio.noise(ms, wire.UP_RATE, amp=0.003)
    voice = lambda ms: audio.mix(room(ms), audio.tone(220, ms, wire.UP_RATE, amp=0.3, am_hz=4))
    return voice(600) + room(400) + voice(1400) + room(3000)


class WakeTurns(unittest.IsolatedAsyncioTestCase):
    def voice(self, transcript, **kw):
        v = make_voice(stt=FakeSTT(transcript), llm=FakeLLM(["I am OpenDubs."]), wake=True,
                       make_vad=vad.EnergyVAD, **kw)
        return v

    async def test_ready_advertises_wake_only_when_enabled(self):
        link, _ = await connect(self.voice("x"))
        self.assertEqual(link.of("ready")[0]["wake"], {"keyword": "hey dubs"})
        link2, page2 = await connect(make_voice())
        self.assertNotIn("wake", link2.of("ready")[0])
        await page2.link.voice.on_text(page2, json.dumps({"type": "wake", "pre_roll_ms": 0}))
        self.assertEqual(link2.of("error")[-1]["code"], "VOICE_NO_WAKE")

    async def test_the_spotter_line_is_tuned_per_robot(self):
        import os
        from unittest import mock
        line = "▁HE Y ▁DU B S :3.0 #0.05 @HEY_DUBS"
        with mock.patch.dict(os.environ, {"OPENDUBS_VOICE_WAKE_KEYWORDS": line}):
            link, _ = await connect(self.voice("x"))
        self.assertEqual(link.of("ready")[0]["wake"], {"keyword": "hey dubs", "keywords": line})
        with mock.patch.dict(os.environ, {"OPENDUBS_VOICE_WAKE_KEYWORDS": "HEY DUBS"}):   # no @name: the page's own
            link, _ = await connect(self.voice("x"))
        self.assertEqual(link.of("ready")[0]["wake"], {"keyword": "hey dubs"})

    async def test_a_wake_turn_ends_by_itself_and_answers_the_question(self):
        v = self.voice("Hey dubs, what is your name?")
        link, page = await connect(v)
        await wake(v, page, utterance())
        await settle(v)
        self.assertEqual(link.states(), ["listen", "think", "speak", "idle"])
        self.assertEqual(link.of("heard")[0]["transcript"], "what is your name?")
        turn = link.of("turn")[-1]
        self.assertEqual((turn["status"], turn["source"]), ("ok", "wake"))
        self.assertEqual(v.history[0], {"role": "user", "text": "what is your name?"})   # the phrase is stripped
        self.assertTrue(link.frames)
        self.assertLess(turn["ms"]["utterance"], 600 + 400 + 1400 + 1500)               # ended on the silence

    async def test_a_false_wake_is_silent(self):
        for transcript, why in (("Hey doves.", "no wake phrase"), ("Hey dubs.", "only the wake phrase")):
            v = self.voice(transcript)
            link, page = await connect(v)
            await wake(v, page, utterance())
            await settle(v)
            turn = link.of("turn")[-1]
            self.assertEqual(turn["status"], "not_addressed", transcript)
            self.assertIn(why, turn["why"])
            self.assertEqual(link.frames, [], "a false wake must never speak")
            self.assertEqual(v.tts.spoken, [])
            self.assertEqual(v.history, [])
            self.assertEqual(link.states()[-1], "idle")

    async def test_nothing_said_after_the_wake_word_is_silent(self):
        v = self.voice("never asked")
        link, page = await connect(v)
        keyword = audio.mix(audio.noise(600, wire.UP_RATE, amp=0.003), audio.tone(220, 600, wire.UP_RATE, amp=0.3, am_hz=4))
        await wake(v, page, keyword + silent_clip(5000))
        await settle(v)
        turn = link.of("turn")[-1]
        self.assertEqual(turn["status"], "not_addressed")
        self.assertIn("nothing was said", turn["why"])
        self.assertEqual((link.frames, v.tts.spoken), ([], []))

    async def test_a_wake_never_cuts_a_reply(self):
        v = make_voice(stt=FakeSTT("Hey dubs, hello"), llm=FakeLLM(["Hi."], delay_s=5), wake=True,
                       make_vad=vad.EnergyVAD)
        link, page = await connect(v)
        await wake(v, page, utterance())
        await asyncio.sleep(0.05)
        self.assertEqual(v.state, "think")
        await v.on_text(page, json.dumps({"type": "wake", "keyword": "hey dubs", "pre_roll_ms": 0}))
        self.assertEqual(v.state, "think")                   # ignored: the robot hearing itself
        self.assertEqual(link.of("stop"), [])

    async def test_wake_is_off_during_a_show(self):
        v = self.voice("Hey dubs, hello", gate=FakeGate(False, "a show is running (wave)"))
        link, page = await connect(v)
        await v.on_text(page, json.dumps({"type": "wake", "keyword": "hey dubs", "pre_roll_ms": 0}))
        self.assertEqual(link.of("turn")[-1]["status"], "off")
        self.assertEqual(v.state, "idle")

    async def test_bad_wake_messages(self):
        v = self.voice("x")
        link, page = await connect(v)
        for bad in ({"pre_roll_ms": -1}, {"pre_roll_ms": 99999}, {"pre_roll_ms": "500"}, {"pre_roll_ms": True}):
            await v.on_text(page, json.dumps({"type": "wake", **bad}))
            self.assertEqual(link.of("error")[-1]["code"], "VOICE_BAD_MESSAGE", bad)
        self.assertEqual(v.state, "idle")

    async def test_talk_turns_are_unchanged(self):
        """Talk still ends on release, with no endpointer, and keeps its spoken "say that again"."""
        from fakes import say
        v = self.voice("what is your name")
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        turn = link.of("turn")[-1]
        self.assertEqual((turn["status"], turn["source"]), ("ok", "talk"))
        self.assertEqual(v.history[0]["text"], "what is your name")


if __name__ == "__main__":
    unittest.main()
