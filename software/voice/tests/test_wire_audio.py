#!/usr/bin/env python3
"""The wire format, the speech gate on fake audio, and the sentence splitter.

    python3 software/voice/tests/test_wire_audio.py
"""
import json
import unittest

from fakes import fan_clip, silent_clip, speech_clip

import audio
import wire
from sentences import Splitter


class Wire(unittest.TestCase):
    def test_uplink_round_trip(self):
        pcm = speech_clip(20)
        self.assertEqual(len(pcm), wire.UP_FRAME_SAMPLES * 2)
        f = wire.pack_up(7, 140, pcm)
        self.assertEqual(len(f), 648)                   # the voice note's figure
        self.assertEqual(wire.parse_up(f), (7, 140, pcm))

    def test_uplink_little_endian(self):
        self.assertEqual(wire.pack_up(1, 2, b"")[:8], b"\x01\0\0\0\x02\0\0\0")

    def test_bad_uplink_frames(self):
        for bad in (b"", b"\0" * 7, b"\0" * 9, b"\0" * (8 + wire.MAX_UP_PCM_BYTES + 2)):
            with self.assertRaises(wire.WireError):
                wire.parse_up(bad)

    def test_downlink(self):
        pcm = b"\1\0" * wire.DOWN_CHUNK_SAMPLES
        self.assertEqual(wire.parse_down(wire.pack_down(3, 9, pcm)), (3, 9, pcm))

    def test_control(self):
        self.assertEqual(wire.parse_control('{"type":"press"}'), {"type": "press"})
        for bad in ("[]", '{"type":"shutdown"}', "x", '{"no":1}', '{"type":"press","pad":"' + "a" * 5000 + '"}'):
            with self.assertRaises(wire.WireError):
                wire.parse_control(bad)
        ok = {"type": "hello", "protocol": 1, "up": {"rate": 16000, "format": "s16le", "channels": 1}}
        self.assertIsNone(wire.check_hello(ok))
        self.assertIn("protocol", wire.check_hello({**ok, "protocol": 2}))
        self.assertIn("uplink", wire.check_hello({**ok, "up": {"rate": 16000, "format": "f32", "channels": 1}}))

    def test_ready_describes_both_directions(self):
        r = json.loads(wire.ready(True, None, 12.0))
        self.assertEqual(r["down"]["rate"], 24000)
        self.assertEqual(r["up"]["frame_ms"], 20)


class SpeechGate(unittest.TestCase):
    def test_speech_silence_and_a_steady_hum(self):
        self.assertTrue(audio.has_speech(speech_clip(), wire.UP_RATE))
        self.assertFalse(audio.has_speech(silent_clip(), wire.UP_RATE))
        self.assertFalse(audio.has_speech(fan_clip(), wire.UP_RATE))       # loud, but steady
        self.assertFalse(audio.has_speech(speech_clip(200), wire.UP_RATE))  # a tap on the button

    def test_levels(self):
        peak, floor = audio.level(speech_clip(), wire.UP_RATE)
        self.assertGreater(peak - floor, audio.PEAK_OVER_FLOOR_DB)
        self.assertEqual(audio.level(b"", wire.UP_RATE), (-120.0, -120.0))

    def test_chunks(self):
        pcm = audio.silence(250, wire.DOWN_RATE)
        sizes = [len(c) for c in audio.chunks(pcm, wire.DOWN_RATE, 100)]
        self.assertEqual(sizes, [4800, 4800, 2400])


class Sentences(unittest.TestCase):
    def test_streamed_tokens(self):
        sp, out = Splitter(), []
        for tok in ["Hel", "lo there", ". I am", " a robot! Are", " you 3.5 m", " tall?"]:
            out += sp.feed(tok)
        self.assertEqual(out, ["Hello there.", "I am a robot!"])     # "tall?" may not be the end yet
        out += sp.feed(" Bye")
        self.assertEqual(out[-1], "Are you 3.5 m tall?")             # "3.5" did not end a sentence
        self.assertEqual(sp.flush(), ["Bye"])

    def test_a_run_on_is_cut(self):
        sp = Splitter()
        out = sp.feed("word, " * 60)
        self.assertTrue(out)
        self.assertTrue(all(len(s) <= 180 for s in out))

    def test_line_breaks_and_quotes(self):
        self.assertEqual(Splitter().feed('He said "hi." Then\nleft '), ['He said "hi."', "Then"])


if __name__ == "__main__":
    unittest.main()
