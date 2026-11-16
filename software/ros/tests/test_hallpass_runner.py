#!/usr/bin/env python3
"""hallpass_runner: a re-aim turns the head the way the stop check means, and a mission whose stops are in another
frame is refused. A re-aim the wrong way spends the target's one retry on a frame that is further off.
Run: python3 software/ros/tests/test_hallpass_runner.py (needs pyyaml)"""
import math
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import hallpass_runner as hr  # noqa: E402


class ReaimTest(unittest.TestCase):
    def test_right_and_down_in_the_image_aim_right_and_down(self):
        cam, point = [0.0, 0.0, 1.0], [3.0, 0.0, 1.0]        # straight ahead, level
        x, y, z = hr.reaimed(cam, point, az_deg=10.0, el_deg=5.0)
        self.assertLess(y, 0.0)                               # right of the robot: base_link y < 0
        self.assertLess(z, 1.0)                               # lower
        self.assertAlmostEqual(math.degrees(math.atan2(-y, x)), 10.0, places=6)
        self.assertAlmostEqual(math.hypot(x, y), 3.0, places=6)


class MissionTest(unittest.TestCase):
    def test_a_stop_in_another_frame_is_refused(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("needs pyyaml")
        doc = {"schema": "sitekit.navgoals/1", "frame_id": "bldg_f1",
               "poses": [{"n": 1, "header": {"frame_id": "bldg_f2"}, "pose": {}}]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.navgoals.yaml")
            with open(p, "w") as f:
                yaml.safe_dump(doc, f)
            with self.assertRaises(ValueError):
                hr.load_mission(p)
            self.assertEqual(list(hr.missions(d)), ["m"])


if __name__ == "__main__":
    unittest.main()
