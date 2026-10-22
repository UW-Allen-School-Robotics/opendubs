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
        vx, vy, wz = d.command((0.1, 0.0, 0.0), (0.05, 0.0, 0.0), 1.0, 1.0, 1.0)
        self.assertAlmostEqual(vx, 0.05 + d.K_LIN * 0.1); self.assertAlmostEqual(vy, 0.0)

    def test_never_asks_for_more_than_the_cap(self):
        # forward + strafe + turn, each in units of its speed at the cap, stay within 1
        vx, vy, wz = d.command((5.0, 5.0, 5.0), (0.0, 0.0, 0.0), 0.2, 0.15, 0.5)
        self.assertAlmostEqual(abs(vx) / 0.2 + abs(vy) / 0.15 + abs(wz) / 0.5, 1.0)

    def test_a_strafe_is_trimmed_against_its_bleed(self):
        vx, vy, wz = d.command((0.0, 0.0, 0.0), (0.0, 0.05, 0.0), 1.0, 1.0, 1.0, fwd_bleed=0.2, yaw_bleed=0.4)
        self.assertAlmostEqual(vx, -0.01); self.assertAlmostEqual(wz, -0.02)
        # per direction: only a RIGHT strafe (vy < 0) is trimmed when only the right value is set
        _, _, wz_l = d.command((0.0, 0.0, 0.0), (0.0, 0.05, 0.0), 1.0, 1.0, 1.0, yaw_bleed=(0.0, 1.46))
        _, _, wz_r = d.command((0.0, 0.0, 0.0), (0.0, -0.05, 0.0), 1.0, 1.0, 1.0, yaw_bleed=(0.0, 1.46))
        self.assertEqual(wz_l, 0.0); self.assertAlmostEqual(wz_r, 1.46 * 0.05)

    def test_settling_overcomes_static_friction(self):
        # 3 cm short of home, no feed-forward: 1.5 x 0.03 = 0.045 m/s is raised to the minimum, same direction
        vx, vy, _ = d.command((0.03, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 1.0, 1.0, min_lin=0.07)
        self.assertAlmostEqual(vx, 0.07); self.assertEqual(vy, 0.0)
        vx, vy, _ = d.command((-0.02, 0.02, 0.0), (0.0, 0.0, 0.0), 1.0, 1.0, 1.0, min_lin=0.07)
        self.assertAlmostEqual(math.hypot(vx, vy), 0.07); self.assertAlmostEqual(vx, -vy)
        _, _, wz = d.command((0.0, 0.0, math.radians(-3)), (0.0, 0.0, 0.0), 1.0, 1.0, 1.0, min_ang=0.10)
        self.assertAlmostEqual(wz, -0.10)

    def test_minimum_command_stays_out_of_the_way(self):
        # off by default; not inside the settle band; never during a move; never past the cap
        self.assertAlmostEqual(d.command((0.03, 0, 0), (0, 0, 0), 1.0, 1.0, 1.0)[0], 1.5 * 0.03)
        self.assertAlmostEqual(d.command((0.01, 0, 0), (0, 0, 0), 1.0, 1.0, 1.0, min_lin=0.07)[0], 1.5 * 0.01)
        self.assertAlmostEqual(d.command((-0.02, 0, 0), (0.05, 0, 0), 1.0, 1.0, 1.0, min_lin=0.07)[0], 0.02)
        # a heading error during a strafe (turn feed-forward zero, strafe not) gets the plain gain only
        self.assertAlmostEqual(d.command((0, 0, math.radians(3)), (0, 0.09, 0), 1.0, 1.0, 1.0, min_ang=0.15)[2],
                               1.5 * math.radians(3))
        vx, _, wz = d.command((0.03, 0, math.radians(3)), (0, 0, 0), 0.05, 0.05, 0.08, min_lin=0.07, min_ang=0.10)
        self.assertLessEqual(abs(vx) / 0.05 + abs(wz) / 0.08, 1.0 + 1e-9)

    def test_holds_rather_than_chases(self):
        self.assertTrue(d.over_limit((0.31, 0.0, 0.0)))
        self.assertTrue(d.over_limit((0.0, 0.0, math.radians(21))))
        self.assertFalse(d.over_limit((0.2, 0.1, math.radians(10))))


if __name__ == "__main__":
    unittest.main()
