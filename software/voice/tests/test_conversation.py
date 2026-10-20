#!/usr/bin/env python3
"""Whole push-to-talk turns, with fake audio and fake backends: states, audio framing, the
NO_SPEECH rule, barge-in, the show interlock, tools, refusals, timeouts and the conversation window.

    python3 software/voice/tests/test_conversation.py
"""
import asyncio
import json
import unittest

from fakes import (BrokenBrain, FakeFace, FakeGate, connect, fan_clip, make_voice, say, settle,
                   silent_clip, speech_clip)

import conversation
import wire
from brains import FakeLLM, ToolCall
from speech import FakeSTT, FakeTTS


class Turns(unittest.IsolatedAsyncioTestCase):
    async def test_a_whole_turn(self):
        llm = FakeLLM(["I am OpenDubs. I live in a lab."])
        v = make_voice(llm=llm)
        link, page = await connect(v)
        self.assertEqual(link.of("ready")[0]["voice"], {"on": True, "why": None})
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(link.states(), ["listen", "think", "speak", "idle"])
        self.assertEqual(link.of("heard")[0]["transcript"], "what are you")
        turn = link.of("turn")[-1]
        self.assertEqual(turn["status"], "ok")
        self.assertEqual(turn["reply"], "I am OpenDubs. I live in a lab.")
        for k in ("stt", "first_text", "first_audio", "turn", "utterance"):
            self.assertIn(k, turn["ms"])
        # audio: every chunk carries this turn and a running sequence number
        self.assertTrue(link.frames)
        self.assertEqual({f[0] for f in link.frames}, {turn["turn"]})
        self.assertEqual([f[1] for f in link.frames], list(range(len(link.frames))))
        self.assertTrue(all(len(f[2]) <= wire.DOWN_CHUNK_SAMPLES * 2 for f in link.frames))
        self.assertEqual(link.of("tts_end")[0]["turn"], turn["turn"])
        # spoken sentence by sentence
        self.assertEqual(v.tts.spoken, ["I am OpenDubs.", "I live in a lab."])
        self.assertEqual([h["role"] for h in v.history], ["user", "assistant"])
        self.assertEqual(v.face.convos, ["listen", "think", "speak", "idle"])

    async def test_silence_never_reaches_a_model(self):
        for clip in (silent_clip(), fan_clip(), speech_clip()[:2000]):
            llm = FakeLLM()
            v = make_voice(llm=llm)
            link, page = await connect(v)
            await say(v, page, clip)
            await settle(v)
            self.assertEqual(link.of("turn")[-1]["status"], "unintelligible")
            self.assertEqual(llm.calls, [])
            self.assertEqual(v.tts.spoken, [conversation.SAY_AGAIN])
            self.assertEqual(v.history, [])

    async def test_empty_transcript_is_unintelligible(self):
        llm = FakeLLM()
        v = make_voice(llm=llm, stt=FakeSTT(""))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(link.of("turn")[-1]["status"], "unintelligible")
        self.assertEqual(llm.calls, [])

    async def test_half_duplex_drops_frames_outside_listen(self):
        v = make_voice()
        link, page = await connect(v)
        await v.on_bytes(page, wire.pack_up(0, 0, speech_clip()[:640]))
        self.assertEqual(page.dropped, 1)
        self.assertEqual(v.state, "idle")

    async def test_lost_frames_are_counted(self):
        v = make_voice()
        link, page = await connect(v)
        await say(v, page, speech_clip(), seq_skip=2)
        await settle(v)
        self.assertEqual(v._lost, 2)

    async def test_utterance_cap_releases(self):
        v = make_voice(max_utterance_s=0.5)
        link, page = await connect(v)
        await say(v, page, speech_clip(1200), release=False)
        self.assertIn(v.state, ("think", "speak", "idle"))
        await settle(v)
        self.assertLessEqual(link.of("turn")[-1]["ms"]["utterance"], 520)


class Interruptions(unittest.IsolatedAsyncioTestCase):
    async def test_press_during_a_reply_cuts_it(self):
        v = make_voice(llm=FakeLLM(["A long answer."], delay_s=5))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await asyncio.sleep(0.05)
        self.assertEqual(v.state, "think")
        await v.on_text(page, '{"type":"press"}')
        self.assertEqual(v.state, "listen")
        stop = link.of("stop")[0]
        self.assertEqual((stop["turn"], stop["why"]), (1, "barge_in"))
        self.assertEqual(link.of("turn")[-1]["status"], "cut")
        self.assertEqual(v.history, [])                 # the unanswered question is not kept
        self.assertEqual(v.turn, 2)

    async def test_barge_in_without_a_new_utterance(self):
        v = make_voice(llm=FakeLLM(["Hello."], delay_s=5))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await asyncio.sleep(0.05)
        await v.on_text(page, '{"type":"barge_in"}')
        self.assertEqual(v.state, "idle")
        self.assertEqual(link.of("stop")[0]["why"], "barge_in")

    async def test_a_show_cuts_the_turn_and_closes_voice(self):
        gate = FakeGate()
        llm = FakeLLM(["Hi."], delay_s=5)
        v = make_voice(llm=llm, gate=gate)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await asyncio.sleep(0.05)
        await gate.set(False, "a show is running (wave)")
        self.assertEqual(v.state, "idle")
        self.assertEqual(link.of("voice")[-1], {"type": "voice", "on": False, "why": "a show is running (wave)"})
        self.assertEqual(link.of("stop")[0]["why"], "a show is running (wave)")
        # and a press while it is off is answered `off`, and captures nothing
        await v.on_text(page, '{"type":"press"}')
        self.assertEqual(link.of("turn")[-1]["status"], "off")
        self.assertEqual(v.state, "idle")

    async def test_voice_off_at_connect_is_reported(self):
        v = make_voice(gate=FakeGate(False, "voice is switched off on this robot"))
        link, page = await connect(v)
        self.assertEqual(link.of("ready")[0]["voice"]["on"], False)

    async def test_a_second_page_is_busy_while_the_first_talks(self):
        v = make_voice(llm=FakeLLM(["Hi."], delay_s=5))
        a, pa = await connect(v)
        b, pb = await connect(v)
        await say(v, pa, speech_clip())
        await asyncio.sleep(0.05)
        await v.on_text(pb, '{"type":"press"}')
        self.assertEqual(b.of("turn")[-1]["status"], "busy")
        self.assertIs(v.owner, pa)
        await v.leave(pa)
        self.assertEqual(v.state, "idle")
        self.assertIsNone(v.owner)

    async def test_the_page_leaving_mid_turn(self):
        v = make_voice(llm=FakeLLM(["Hi."], delay_s=5))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await asyncio.sleep(0.05)
        await v.leave(page)
        await settle(v)
        self.assertEqual(v.history, [])


class Brains(unittest.IsolatedAsyncioTestCase):
    async def test_face_tool_then_more_answer(self):
        llm = FakeLLM([[ToolCall("t1", "set_expression", {"expression": "soft"}), "Aw, thank you."],
                       "Have a nice day."])
        v = make_voice(llm=llm)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(v.face.lines, ["EMOTE SOFT"])
        self.assertEqual(link.of("turn")[-1]["reply"], "Aw, thank you. Have a nice day.")
        self.assertEqual([h["role"] for h in v.history], ["user", "assistant", "tool", "assistant"])
        self.assertFalse(v.history[2]["results"][0]["is_error"])

    async def test_a_motion_tool_is_refused_and_nothing_moves(self):
        llm = FakeLLM([[ToolCall("t1", "drive", {"forward": 1.0}),
                        ToolCall("t2", "set_expression", {"expression": "SOFT\nGAZE 0 0"})], "Sorry."])
        v = make_voice(llm=llm)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(v.face.lines, [])
        results = v.history[2]["results"]
        self.assertTrue(all(r["is_error"] for r in results))

    async def test_robot_status_reads_and_reports_truthfully(self):
        llm = FakeLLM([[ToolCall("t1", "robot_status", {})], "You are not stopped."])
        import time
        v = make_voice(llm=llm, robot_status=lambda: {"ts": time.time(), "state": {"words": "Emergency stop"},
                                                      "faults": [], "battery": {"percent": None}})
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        content = v.history[2]["results"][0]["content"]
        self.assertIn("state: Emergency stop", content)
        self.assertIn("battery: unknown", content)
        self.assertIn("I can't tell right now", content)

    async def test_end_conversation(self):
        llm = FakeLLM([[ToolCall("t1", "end_conversation", {}), "Goodbye!"]] + ["ok"])
        v = make_voice(llm=llm)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        await asyncio.sleep(0.05)
        self.assertEqual(link.of("conversation_end")[-1]["why"], "goodbye")
        self.assertEqual(v.history, [])
        self.assertIsNone(v.owner)

    async def test_refusal_speaks_a_decline_and_forgets_the_question(self):
        v = make_voice(llm=FakeLLM(["no"], stop="refusal"))
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(link.of("turn")[-1]["status"], "declined")
        self.assertIn(conversation.DECLINE, v.tts.spoken)
        self.assertEqual(v.history, [])

    async def test_offline_brain(self):
        v = make_voice(llm=BrokenBrain())
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(link.of("turn")[-1]["status"], "offline")
        self.assertEqual(v.tts.spoken, [conversation.OFFLINE])
        self.assertEqual(v.history, [])

    async def test_a_slow_brain_gets_a_holding_line_then_an_apology(self):
        v = make_voice(llm=FakeLLM(["Late."], delay_s=2), slow_s=0.05, give_up_s=0.15)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(v.tts.spoken, [conversation.MOMENT, conversation.LOST])
        self.assertEqual(link.of("turn")[-1]["status"], "timeout")
        self.assertEqual(v.history, [])
        # the face showed THINK at release, before any of that: it never waits on the pipeline
        self.assertEqual(link.states()[:2], ["listen", "think"])


class Window(unittest.IsolatedAsyncioTestCase):
    async def test_history_carries_between_turns_then_ends_when_quiet(self):
        llm = FakeLLM(["One.", "Two."])
        v = make_voice(llm=llm, convo_idle_s=0.2)
        link, page = await connect(v)
        await say(v, page, speech_clip())
        await settle(v)
        await say(v, page, speech_clip())
        await settle(v)
        self.assertEqual(len(llm.calls[1]), 3)          # user, assistant, user
        await asyncio.sleep(0.35)
        self.assertEqual(link.of("conversation_end")[-1]["why"], "quiet")
        self.assertEqual(v.history, [])

    async def test_played_ends_speak_promptly_and_silence_without_it(self):
        v = make_voice(llm=FakeLLM(["Hi."]), played_grace_s=0.1)
        link, page = await connect(v, auto_played=False)
        await say(v, page, speech_clip())
        await asyncio.sleep(0.05)
        self.assertEqual(v.state, "speak")               # waits for the page, up to the audio's length
        await v.on_text(page, json.dumps({"type": "played", "turn": v.turn}))
        await settle(v)


class Protocol(unittest.IsolatedAsyncioTestCase):
    async def test_hello_first_and_matching(self):
        v = make_voice()
        from fakes import FakeLink
        link = FakeLink()
        page = await v.join(link)
        await v.on_text(page, '{"type":"press"}')
        self.assertEqual(link.of("error")[-1]["code"], "VOICE_NO_HELLO")
        await v.on_text(page, json.dumps({"type": "hello", "protocol": 1,
                                          "up": {"rate": 48000, "format": "s16le", "channels": 1}}))
        self.assertEqual(link.of("error")[-1]["code"], "VOICE_BAD_HELLO")
        await v.on_text(page, "not json")
        self.assertEqual(link.of("error")[-1]["code"], "VOICE_BAD_MESSAGE")
        await v.on_text(page, '{"type":"rm -rf"}')
        self.assertEqual(link.of("error")[-1]["code"], "VOICE_BAD_MESSAGE")

    async def test_bad_audio_frame(self):
        v = make_voice()
        link, page = await connect(v)
        await v.on_text(page, '{"type":"press"}')
        await v.on_bytes(page, b"\x01\x02")
        self.assertEqual(link.of("error")[-1]["code"], "VOICE_BAD_FRAME")


class Recording(unittest.IsolatedAsyncioTestCase):
    async def test_the_log_has_the_clips_shape_and_no_words(self):
        """Clipping and level are read from the log without any words in it (speech_clip has 240 ms
        of room before the voice and 240 ms after)."""
        v = make_voice(llm=FakeLLM(["Hello."]))
        link, page = await connect(v)
        with self.assertLogs("voice", "INFO") as logs:
            await say(v, page, speech_clip())
            await settle(v)
        rec = json.loads(next(l for l in logs.output if '"status"' in l).split("turn ", 1)[1])
        self.assertAlmostEqual(rec["audio"]["onset_ms"], 240, delta=40)
        self.assertAlmostEqual(rec["audio"]["tail_ms"], 240, delta=40)
        self.assertEqual(rec["words"], 3)
        self.assertNotIn("transcript", rec)
        self.assertIsNone(v.record_dir)              # nothing is written unless a session asks for it

    async def test_a_recording_session_keeps_each_turns_uplink(self):
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"OPENDUBS_VOICE_RECORD_DIR": d}):
            v = make_voice(llm=FakeLLM(["Hello."]))
            link, page = await connect(v)
            clip = speech_clip()
            await say(v, page, clip)
            await settle(v)
            names = sorted(os.listdir(d))
            self.assertEqual([n.rsplit(".", 1)[1] for n in names], ["json", "s16le"])
            with open(os.path.join(d, names[1]), "rb") as f:
                self.assertEqual(f.read(), clip)
            with open(os.path.join(d, names[0])) as f:
                note = json.load(f)
            self.assertEqual((note["heard"], note["status"], note["rate"]), ("what are you", "ok", 16000))


class Uplink(unittest.IsolatedAsyncioTestCase):
    async def test_two_interleaved_streams_are_flagged(self):
        """It happened: a reconnect left the old mic source feeding, so the page sent two streams
        interleaved, about 100 frames a second, and recognition heard whine instead of a visitor."""
        for per_tick, expect in ((1, 1.0), (2, 2.0)):
            v = make_voice(llm=FakeLLM(["Hello."]))
            link, page = await connect(v)
            await v.on_text(page, '{"type":"press"}')
            clip, step = speech_clip(3000), wire.UP_FRAME_SAMPLES * 2
            for i, off in enumerate(range(0, len(clip), step)):
                await v.on_bytes(page, wire.pack_up(i, (i // per_tick) * 20, clip[off:off + step]))
            with self.assertLogs("voice", "INFO") as logs:
                await v.on_text(page, '{"type":"release"}')
                await settle(v)
            rec = json.loads(next(l for l in logs.output if '"uplink_x"' in l).split("turn ", 1)[1])
            self.assertAlmostEqual(rec["uplink_x"], expect, delta=0.05)
            self.assertEqual(any("faster than real time" in l for l in logs.output), per_tick == 2)


if __name__ == "__main__":
    unittest.main()
