#!/usr/bin/env python3
"""dance_tracker: frames, errors and the never-chase limit. Pure Python; no ROS.
Run: python3 software/ros/tests/test_dance_tracker.py"""
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import dance_tracker as d  # noqa: E402


class FrameTest(unittest.TestCase):
    def test_relative_to_a_turned_origin(self):
        # origin at (1, 2) facing +y; a pose 0.5 m further along +y is 0.5 m FORWARD of it
        x, y, yaw = d.relative((1.0, 2.0, math.pi / 2), (1.0, 2.5, math.pi / 2))
        self.assertAlmostEqual(x, 0.5); self.assertAlmostEqual(y, 0.0); self.assertAlmostEqual(yaw, 0.0)

    def test_body_error_turns_with_the_robot(self):
        # robot at the origin facing left (+90 deg); a target 0.2 m to the origin's +y is dead ahead
        fwd, left, dyaw = d.body_error((0.0, 0.2, math.pi / 2), (0.0, 0.0, math.pi / 2))
        self.assertAlmostEqual(fwd, 0.2); self.assertAlmostEqual(left, 0.0); self.assertAlmostEqual(dyaw, 0.0)

    def test_yaw_error_wraps_the_short_way(self):
        self.assertAlmostEqual(d.body_error((0, 0, math.radians(179)), (0, 0, math.radians(-179)))[2],
                               math.radians(-2))


class CommandTest(unittest.TestCase):
    def test_feed_forward_plus_correction(self):
        vx, vy, wz = d.command((0.1, 0.0, 0.0), (0.05, 0.0, 0.0), vmax=1.0, wmax=1.0)
        self.assertAlmostEqual(vx, 0.05 + d.K_LIN * 0.1); self.assertAlmostEqual(vy, 0.0)

    def test_never_asks_for_more_than_the_cap(self):
        vx, vy, wz = d.command((5.0, 5.0, 5.0), (0.0, 0.0, 0.0), vmax=0.2, wmax=0.5)
        self.assertAlmostEqual(math.hypot(vx, vy), 0.2); self.assertAlmostEqual(wz, 0.5)

    def test_holds_rather_than_chases(self):
        self.assertTrue(d.over_limit((0.31, 0.0, 0.0)))
        self.assertTrue(d.over_limit((0.0, 0.0, math.radians(21))))
        self.assertFalse(d.over_limit((0.2, 0.1, math.radians(10))))


if __name__ == "__main__":
    unittest.main()
