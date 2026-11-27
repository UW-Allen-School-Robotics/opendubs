#!/usr/bin/env python3
"""Show music on the Jabra: the music starts on the show's beat, the robot's speech ducks it, stop fades
and cut silences, a show is played only once it is loaded here, and losing the role stops the music.

    python3 software/architect/tests/test_show_music.py      # needs numpy; no device, no network
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402

from opendubs_architect import show_music as sm  # noqa: E402

TONE = np.full(sm.RATE * 10, 10000, dtype=np.int16)        # 10 s of a constant level: easy to read back


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class Mixing(unittest.TestCase):
    def setUp(self):
        self.m = sm.Mixer(clock=Clock())

    def level(self, heard_t):
        return float(np.abs(self.m.block(heard_t).astype(np.float32)).mean())

    def test_the_music_starts_on_the_beat(self):
        self.m.play([sm.Track(TONE, at=2.0)], t0=200.0)      # this item from show time 2 s
        self.assertEqual(self.level(201.9), 0.0)             # before it is due: silence
        self.assertAlmostEqual(self.level(202.5), 10000 * sm.MUSIC_GAIN, delta=1)
        b = self.m.block(202.0 - 0.01)                       # due half way through a 20 ms block
        self.assertEqual(int(b[0]), 0)
        self.assertGreater(int(b[-1]), 0)

    def test_speech_ducks_the_music_and_its_done_is_set(self):
        self.m.play([sm.Track(TONE, at=0.0)], t0=0.0)
        done = self.m.say(np.full(sm.BLOCK * 2, 1000, dtype=np.int16).tobytes())
        lv = self.level(1.0)
        self.assertAlmostEqual(lv, 10000 * sm.DUCK_GAIN + 1000, delta=1)
        self.assertFalse(done.is_set())
        self.level(1.02)
        self.assertTrue(done.is_set())
        self.assertAlmostEqual(self.level(1.04), 10000 * sm.MUSIC_GAIN, delta=1)   # back up after

    def test_stop_fades_and_cut_silences(self):
        self.m.play([sm.Track(TONE, at=0.0)], t0=0.0)
        self.m.clock.t = 1.0
        self.m.fade(1.0)                                     # from 1.1 (now + AHEAD_S) to 2.1
        mid = self.level(1.6)
        self.assertTrue(0 < mid < 10000 * sm.MUSIC_GAIN)
        self.assertEqual(self.level(2.2), 0.0)
        self.assertFalse(self.m.playing)
        self.m.play([sm.Track(TONE, at=0.0)], t0=0.0)
        self.m.cut()
        self.assertEqual(self.level(1.0), 0.0)

    def test_a_held_item_waits_for_unhold(self):
        self.m.play([sm.Track(TONE, at=0.0, held=True)], t0=0.0)
        self.assertEqual(self.level(1.0), 0.0)
        self.assertEqual(self.m.unhold(), 1)
        self.assertGreater(self.level(1.0), 0.0)


class Urls(unittest.TestCase):
    def test_a_song_with_spaces_is_escaped_once(self):
        self.assertEqual(sm.media_url("/api/choreo/media/library/songs/14 - Ghostbusters.mp3", "http://d:8080"),
                         "http://d:8080/api/choreo/media/library/songs/14%20-%20Ghostbusters.mp3")
        self.assertEqual(sm.media_url("/api/choreo/media/a%20b.mp3", "http://d"), "http://d/api/choreo/media/a%20b.mp3")


class Role(unittest.TestCase):
    def setUp(self):
        self.mixer = sm.Mixer(clock=Clock())
        self.client = sm.ShowAudio(self.mixer, fetch_fn=lambda url: b"mp3", decode_fn=lambda b: TONE)

    def test_load_then_start_plays_and_a_stranger_start_does_not(self):
        c = self.client
        c.handle({"holder": "Jabra", "you_hold": True})
        self.assertEqual(c.handle({"op": "load", "id": "g@1", "items": [{"url": "/api/choreo/media/x.mp3", "at": 0}]}),
                         {"ready": "g@1"})
        c.handle({"op": "start", "id": "other@2", "t0_mono": 5.0})
        self.assertFalse(self.mixer.playing)
        c.handle({"op": "start", "id": "g@1", "t0_mono": 5.0})
        self.assertTrue(self.mixer.playing)

    def test_a_file_that_cannot_be_decoded_is_never_ready(self):
        def bad(b):
            raise RuntimeError("not MP3")
        c = sm.ShowAudio(self.mixer, fetch_fn=lambda url: b"m4a", decode_fn=bad)
        self.assertIsNone(c.handle({"op": "load", "id": "g@1", "items": [{"url": "/x.m4a", "at": 0}]}))
        c.handle({"op": "start", "id": "g@1", "t0_mono": 5.0})
        self.assertFalse(self.mixer.playing)                 # the runner plays it silently, and says so

    def test_losing_the_role_stops_the_music(self):
        c = self.client
        c.handle({"holder": "Jabra", "you_hold": True})
        c.handle({"op": "load", "id": "g@1", "items": [{"url": "/x.mp3", "at": 0}]})
        c.handle({"op": "start", "id": "g@1", "t0_mono": 5.0})
        c.handle({"holder": "tablet", "you_hold": False})
        self.assertFalse(self.mixer.playing)


if __name__ == "__main__":
    unittest.main()
