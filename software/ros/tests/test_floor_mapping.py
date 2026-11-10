#!/usr/bin/env python3
"""floor_mapping: the saved map image is the right way up, and the depth camera's streams are found.
A map written upside down would only show up as a failed fit on the day; a missed camera topic only
as a bag without it. Pure Python, no ROS. Run: python3 software/ros/tests/test_floor_mapping.py"""
import datetime
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import floor_mapping as fm  # noqa: E402


class MapImageTest(unittest.TestCase):
    def test_grid_row_zero_is_the_pgm_bottom_row(self):
        # 3 wide, 2 high: one occupied cell at the grid's row 0 (lowest y), column 2
        data = [-1, 0, 100,
                -1, 0, 0]
        pgm = fm.pgm_bytes(3, 2, data)
        header, pixels = pgm[:len(b"P5\n3 2\n255\n")], pgm[len(b"P5\n3 2\n255\n"):]
        self.assertEqual(header, b"P5\n3 2\n255\n")
        self.assertEqual(list(pixels), [205, 254, 254,     # top row = the grid's row 1
                                        205, 254, 0])      # bottom row = the grid's row 0


class CameraTopicsTest(unittest.TestCase):
    TOPICS = {
        "/camera/color/image_raw": fm.IMAGE_T,
        "/camera/color/image_raw/compressed": fm.COMPRESSED_T,
        "/camera/depth/image_raw": fm.IMAGE_T,
        "/camera/depth/image_raw/compressedDepth": fm.COMPRESSED_T,
        "/camera/color/camera_info": "sensor_msgs/msg/CameraInfo",
        "/camera/depth/camera_info": "sensor_msgs/msg/CameraInfo",
        "/camera/gyro_accel/sample": "sensor_msgs/msg/Imu",
        "/tablet/camera/front/image_raw/compressed": fm.COMPRESSED_T,
        "/scan": "sensor_msgs/msg/LaserScan",
    }

    def test_prefers_compressed_and_keeps_info_and_imu_whole(self):
        c = fm.camera_topics(self.TOPICS)
        self.assertFalse(c["raw"])
        self.assertEqual([t for _, t, _ in c["thin"]],
                         ["/camera/color/image_raw/compressed", "/camera/depth/image_raw/compressedDepth"])
        self.assertEqual(c["whole"], ["/camera/color/camera_info", "/camera/depth/camera_info",
                                      "/camera/gyro_accel/sample"])

    def test_falls_back_to_raw_at_half_rate_and_never_takes_the_tablet(self):
        topics = {k: v for k, v in self.TOPICS.items() if "compressed" not in k or k.startswith("/tablet")}
        c = fm.camera_topics(topics)
        self.assertTrue(c["raw"])
        self.assertEqual(c["thin"], [("color", "/camera/color/image_raw", fm.COLOR_HZ / 2),
                                     ("depth", "/camera/depth/image_raw", fm.DEPTH_HZ / 2)])


class NameTest(unittest.TestCase):
    def test_dated_and_never_overwrites(self):
        d = datetime.date(2026, 11, 10)
        self.assertEqual(fm.map_name("bldg_f1", set(), d), "bldg_f1-20261110")
        self.assertEqual(fm.map_name("bldg_f1", {"bldg_f1-20261110"}, d), "bldg_f1-20261110-2")

    def test_a_1970_clock_gives_no_date(self):
        self.assertEqual(fm.map_name("bldg_f1", set(), datetime.date(1970, 1, 1)), "bldg_f1-run")


if __name__ == "__main__":
    unittest.main()
