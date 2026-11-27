#!/usr/bin/env python3
"""The camera feed subscribes only while frames are asked for: a raw frame is 2.7 MB, 30 a second, and
nobody watching the ARCHITECT tab must cost nothing. Stdlib only (the JPEG encoding needs OpenCV and
is exercised on the robot).

    python3 software/architect/tests/test_camera_feed.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import camera_feed as cf  # noqa: E402


class Feed(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.subs, self.drops = [], []
        self.feed = cf.Feed(lambda cb: self.subs.append(cb) or len(self.subs), self.drops.append, clock=lambda: self.t[0])
        self.feed.on_frame = self._frame                   # no OpenCV here: a frame is "encoded" as given

    def _frame(self, msg):
        self.feed.jpeg, self.feed.jpeg_at = msg, self.t[0]

    def test_subscribes_on_the_first_ask_and_drops_it_when_nobody_asks(self):
        self.assertEqual(self.subs, [])                    # nothing until a frame is wanted
        jpg, why = self.feed.ask()
        self.assertEqual((jpg, len(self.subs)), (None, 1))
        self.subs[0](b"jpeg")
        self.assertEqual(self.feed.ask(), (b"jpeg", 0.0))
        self.t[0] += cf.IDLE_S - 0.1
        self.feed.tick()
        self.assertEqual(self.drops, [])                   # still being watched
        self.t[0] += 0.2
        self.feed.tick()
        self.assertEqual(self.drops, [1])                  # nobody asked for IDLE_S: unsubscribed
        self.feed.ask()
        self.assertEqual(len(self.subs), 2)                # and back when someone looks again

    def test_a_stale_frame_is_not_served_as_current(self):
        self.feed.ask()
        self.subs[0](b"jpeg")
        self.t[0] += cf.STALE_S + 0.1
        jpg, why = self.feed.ask()
        self.assertIsNone(jpg)


if __name__ == "__main__":
    unittest.main()
