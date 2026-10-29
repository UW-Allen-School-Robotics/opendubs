#!/usr/bin/env python3
"""head_joints names every movable joint in the URDF, so camera_link can connect. No ROS.
Run: python3 software/ros/tests/test_head_joints.py"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import head_joints as h  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
URDF = ROOT / "description" / "opendubs" / "opendubs.urdf"


class HeadJointsTest(unittest.TestCase):
    def test_the_zeros_are_exactly_the_movable_joints(self):
        names = re.findall(r'<joint name="([^"]+)" type="(?!fixed)', URDF.read_text())
        self.assertEqual(set(names), set(h.JOINTS))


if __name__ == "__main__":
    unittest.main()
