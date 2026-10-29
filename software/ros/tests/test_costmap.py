#!/usr/bin/env python3
"""The costmap config: both scans, map frame, same cell size as the SLAM map. No ROS.
Run: python3 software/ros/tests/test_costmap.py"""
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent


class CostmapTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        self.p = yaml.safe_load((HERE / "costmap.yaml").read_text())["costmap"]["ros__parameters"]
        self.slam = yaml.safe_load((HERE / "slam_toolbox.yaml").read_text())["slam_toolbox"]["ros__parameters"]
        self.scan = yaml.safe_load((HERE / "depth_scan.yaml").read_text())["depth_scan"]["ros__parameters"]

    def test_the_grid_is_the_scan_in_the_map_frame(self):
        self.assertEqual(self.p["global_frame"], "map")
        self.assertTrue(self.p["rolling_window"])
        self.assertEqual((self.p["width"], self.p["height"]), (16, 16))
        self.assertNotIn("static_layer", self.p["plugins"])
        layer = self.p["obstacle_layer"]
        self.assertEqual(layer["observation_sources"], "scan scan_rear depth")
        self.assertEqual(
            (layer["scan"]["topic"], layer["scan_rear"]["topic"]),
            ("/scan_filtered", "/scan_rear_filtered"),
        )
        self.assertEqual(layer["depth"]["topic"], "/scan_depth")
        self.assertEqual(layer["depth"]["obstacle_max_range"], 5.0)
        self.assertEqual(layer["depth"]["raytrace_max_range"], 5.0)
        self.assertTrue(layer["footprint_clearing_enabled"])
        self.assertNotIn("footprint", self.p)
        self.assertTrue(layer["scan"]["inf_is_valid"])
        self.assertTrue(layer["scan_rear"]["inf_is_valid"])
        self.assertTrue(layer["depth"]["inf_is_valid"])
        # A no-return is inserted just short of the lidar's 12 m. Marking has to stop
        # inside that, or every empty ray leaves a lethal cell at the far end.
        self.assertLess(layer["scan"]["obstacle_max_range"], layer["scan"]["raytrace_max_range"])
        self.assertLess(layer["scan_rear"]["obstacle_max_range"], layer["scan_rear"]["raytrace_max_range"])
        self.assertGreater(self.scan["range_max"], layer["depth"]["obstacle_max_range"])
        self.assertEqual(self.p["resolution"], self.slam["resolution"])
        self.assertEqual(self.scan["target_frame"], "base_link")
        self.assertEqual((self.scan["min_height"], self.scan["max_height"]), (0.2, 1.0))
        self.assertEqual(self.scan["range_max"], 7.0)
        # The beam is above the floor. The source default of 0 would drop every return.
        self.assertGreaterEqual(layer["scan"]["max_obstacle_height"], 0.3)
        self.assertGreaterEqual(layer["scan_rear"]["max_obstacle_height"], 0.3)
        self.assertTrue(self.p["always_send_full_costmap"])


if __name__ == "__main__":
    unittest.main()
