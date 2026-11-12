#!/usr/bin/env python3
"""head_joints: the pan sign is applied to pan only, and a message without both joints is dropped.
Pure Python, no ROS. Run: python3 software/ros/tests/test_head_joints.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import head_joints as hj  # noqa: E402


class HeadJointsTest(unittest.TestCase):
    def test_flipped_pan_only(self):
        names, pos = hj.map_joints(["head_pan", "head_tilt"], [0.3, -0.2], hj.pan_sign({"OPENDUBS_HEAD_PAN_URDF_SIGN": "-1"}))
        self.assertEqual(names, ["head_pan", "head_tilt", "zaxis"])
        self.assertEqual(pos, [-0.3, -0.2, 0.0])

    def test_default_sign_and_missing_joint(self):
        self.assertEqual(hj.pan_sign({}), 1.0)
        self.assertIsNone(hj.map_joints(["head_pan"], [0.1], 1.0))


if __name__ == "__main__":
    unittest.main()
