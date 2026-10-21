#!/usr/bin/env python3
"""scan_body_filter: which returns count as the robot. Pure Python; no ROS.
Run: python3 software/ros/tests/test_scan_body_filter.py"""
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import scan_body_filter as f  # noqa: E402

FRONT = (0.230, 0.0, math.pi)   # the front LIDAR: 230 mm ahead of base_link, its 0 deg facing back


class MaskTest(unittest.TestCase):
    def beams(self, *pairs):
        """ranges for beams at the given sensor angles (degrees), 1 deg apart from -180."""
        r = [math.inf] * 360
        for deg, rng in pairs:
            r[deg + 180] = rng
        return r

    def test_the_robot_behind_the_front_lidar_is_dropped(self):
        # sensor 0 deg faces back, at the robot: 0.2 m lands at base_link x = 0.03, inside the box
        out = f.mask(self.beams((0, 0.2)), math.radians(-180), math.radians(1), *FRONT)
        self.assertTrue(math.isinf(out[180]))

    def test_a_wall_ahead_of_the_front_lidar_is_kept(self):
        # sensor 180 deg faces forward: 0.5 m lands at base_link x = 0.73, outside the box
        out = f.mask(self.beams((180 - 360, 0.5)), math.radians(-180), math.radians(1), *FRONT)
        self.assertEqual(out[0], 0.5)

    def test_beside_the_robot_outside_the_margin_is_kept(self):
        # sensor -90 deg with yaw pi points to +y (left): 0.5 m lands at y = 0.5 > 0.4245
        out = f.mask(self.beams((-90, 0.5)), math.radians(-180), math.radians(1), *FRONT)
        self.assertEqual(out[90], 0.5)

    def test_yaw_of_a_half_turn(self):
        self.assertAlmostEqual(abs(f.yaw_of((0.0, 0.0, 1.0, 0.0))), math.pi)


if __name__ == "__main__":
    unittest.main()
