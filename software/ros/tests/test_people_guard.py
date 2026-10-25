#!/usr/bin/env python3
"""people_guard: what stops a command. Pure Python; no ROS. Run: python3 software/ros/tests/test_people_guard.py"""
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import people_guard as g  # noqa: E402

# The robot's outline in base_link is x -0.624..0.388, y +/-0.4245 (scan_body_filter.BOX).
AHEAD = (0.388 + 0.3, 0.0)        # 0.3 m in front of the outline
BEHIND = (-0.624 - 0.3, 0.0)
LEFT = (0.0, 0.4245 + 0.3)
FAR_AHEAD = (0.388 + 1.0, 0.0)


class BlockingTest(unittest.TestCase):
    def test_forward_stops_for_something_ahead_not_behind(self):
        self.assertIsNotNone(g.blocking([AHEAD], 1, 0, 0))
        self.assertIsNone(g.blocking([BEHIND], 1, 0, 0))

    def test_reverse_is_the_mirror(self):
        self.assertIsNotNone(g.blocking([BEHIND], -1, 0, 0))
        self.assertIsNone(g.blocking([AHEAD], -1, 0, 0))

    def test_strafe_left_stops_for_something_on_the_left(self):
        self.assertIsNotNone(g.blocking([LEFT], 0, 1, 0))
        self.assertIsNone(g.blocking([LEFT], 0, -1, 0))

    def test_turning_stops_for_anything_close_on_any_side(self):
        for p in (AHEAD, BEHIND, LEFT):
            self.assertIsNotNone(g.blocking([p], 0, 0, 1), p)

    def test_beyond_reach_never_stops(self):
        self.assertIsNone(g.blocking([FAR_AHEAD], 1, 0, 1))

    def test_standing_still_is_never_refused(self):
        self.assertIsNone(g.blocking([AHEAD], 0, 0, 0))


class VerdictTest(unittest.TestCase):
    def test_a_stale_scan_blinds_the_guard(self):
        ok, why = g.verdict({"/scan": (10.0, []), "/scan_rear": (8.0, [])}, 10.1, 1, 0, 0)
        self.assertFalse(ok); self.assertIn("/scan_rear", why)

    def test_a_scan_never_seen_blinds_the_guard(self):
        self.assertFalse(g.verdict({"/scan": (None, [])}, 10.0, 0, 0, 1)[0])

    def test_a_clear_view_allows(self):
        self.assertTrue(g.verdict({"/scan": (10.0, [FAR_AHEAD])}, 10.1, 1, 0, 0)[0])

    def test_stopping_is_always_allowed_even_blind(self):
        self.assertTrue(g.verdict({"/scan": (None, [])}, 10.0, 0, 0, 0)[0])


class PointsTest(unittest.TestCase):
    def test_front_lidar_points_land_in_base_link_and_its_own_body_is_dropped(self):
        # front LIDAR: 0.23 m ahead of base_link, its 0 deg facing back. 1 deg beams from -180.
        r = [math.inf] * 360
        r[0] = 0.5          # sensor -180 = forward: lands at x = 0.73
        r[180] = 0.2        # sensor 0 = back, into the chassis: dropped
        pts = g.scan_points(r, math.radians(-180), math.radians(1), 0.23, 0.0, math.pi)
        self.assertEqual(len(pts), 1)
        self.assertAlmostEqual(pts[0][0], 0.73, places=6); self.assertAlmostEqual(pts[0][1], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
