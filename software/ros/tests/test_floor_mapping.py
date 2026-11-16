#!/usr/bin/env python3
"""floor_mapping: the saved map image is the right way up, and the depth camera's streams are found.
A map written upside down would only show up as a failed fit on the day; a missed camera topic only
as a bag without it. Pure Python, no ROS. Run: python3 software/ros/tests/test_floor_mapping.py"""
import datetime
import json
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
        self.assertEqual(c["thin"], [("color", "/camera/color/image_raw/compressed", "pass"),
                                     ("depth", "/camera/depth/image_raw/compressedDepth", "pass")])
        self.assertEqual(c["whole"], ["/camera/color/camera_info", "/camera/depth/camera_info",
                                      "/camera/gyro_accel/sample"])

    def test_raw_only_is_compressed_here_per_stream_and_never_takes_the_tablet(self):
        # the robot's depth camera publishes raw depth only (no compressedDepth): that stream alone is encoded
        topics = {k: v for k, v in self.TOPICS.items() if not k.endswith("compressedDepth")}
        self.assertEqual(fm.camera_topics(topics)["thin"],
                         [("color", "/camera/color/image_raw/compressed", "pass"),
                          ("depth", "/camera/depth/image_raw", "png")])
        topics = {k: v for k, v in self.TOPICS.items() if "compressed" not in k or k.startswith("/tablet")}
        self.assertEqual(fm.camera_topics(topics)["thin"],
                         [("color", "/camera/color/image_raw", "jpeg"),
                          ("depth", "/camera/depth/image_raw", "png")])


class ProfileStreamsTest(unittest.TestCase):
    def test_a_label_from_the_match_makes_one_stream_per_topic(self):
        # another robot's profile (Spot: one grey and one depth stream per body camera) must not collapse to one
        cam = {"streams": [{"label": "grey_{name}", "try": [{"match": r"^(?P<base>/spot/camera/(?P<name>\w+))/image$", "how": "jpeg"}]},
                           {"label": "depth_{name}", "try": [{"match": r"^(?P<base>/spot/depth/(?P<name>\w+))/image$", "how": "png"}]}],
               "whole_types": ["sensor_msgs/msg/CameraInfo"]}
        topics = {f"/spot/{k}/{c}/{t}": ty for k in ("camera", "depth") for c in ("frontleft", "back")
                  for t, ty in (("image", fm.IMAGE_T), ("camera_info", "sensor_msgs/msg/CameraInfo"))}
        got = fm.cc.camera_topics(topics, cam)
        self.assertEqual(sorted(l for l, _, _ in got["thin"]),
                         ["depth_back", "depth_frontleft", "grey_back", "grey_frontleft"])
        self.assertEqual(len(got["whole"]), 4)


class NameTest(unittest.TestCase):
    def test_dated_and_never_overwrites(self):
        d = datetime.date(2026, 11, 10)
        self.assertEqual(fm.map_name("bldg_f1", set(), d), "bldg_f1-20261110")
        self.assertEqual(fm.map_name("bldg_f1", {"bldg_f1-20261110"}, d), "bldg_f1-20261110-2")

    def test_a_1970_clock_gives_no_date(self):
        self.assertEqual(fm.map_name("bldg_f1", set(), datetime.date(1970, 1, 1)), "bldg_f1-run")


# `ros2 bag info` as Jazzy prints it (from a recording of the fake robot, trimmed to three topics)
BAG_INFO = """
Files:             gates_fG-20261110_0.mcap
Bag size:          4.3 MiB
Storage id:        mcap
ROS Distro:        jazzy
Duration:          83.379416057s
Start:             Nov 10 2026 16:49:50.469299154 (1794329390.469299154)
End:               Nov 10 2026 16:51:13.848715211 (1794329473.848715211)
Messages:          30280
Topic information: Topic: /mapping/camera/color/image_raw/compressed | Type: sensor_msgs/msg/CompressedImage | Count: 839 | Serialization Format: cdr
                   Topic: /odom/raw | Type: std_msgs/msg/String | Count: 830 | Serialization Format: cdr
                   Topic: /scan | Type: sensor_msgs/msg/LaserScan | Count: 574 | Serialization Format: cdr
Service:           0
"""


class CaptureManifestTest(unittest.TestCase):
    """The written capture.yaml must validate against opendubs.capture/1. tests/capture.schema.json is
    a copy of the site repo's schemas/capture.schema.json; refresh it when that schema changes."""

    def test_floor_codes(self):
        self.assertEqual(fm.parse_frame("bldg_f2"), ("bldg", "02"))
        self.assertEqual(fm.parse_frame("bldg_fG"), ("bldg", "0G"))
        self.assertEqual(fm.parse_frame("bldg_fB1"), ("bldg", "B1"))
        self.assertIsNone(fm.parse_frame("bldg2"))

    def test_deployed_branch_and_dirty(self):
        main = fm.parse_deployed("source=main @ 3cdaead0abcd\nsha=3cdaead0abcd\nbranch=main\n")
        self.assertEqual(main, {"opendubs_sha": "3cdaead0abcd", "dirty": False})
        br = fm.parse_deployed("source=branch x @ 1234567 , WITH UNCOMMITTED CHANGES\nsha=1234567\nbranch=x\n")
        self.assertEqual(br, {"opendubs_sha": "1234567", "branch": "x", "dirty": True})

    def test_written_manifest_validates(self):
        try:
            import jsonschema
            import yaml
        except ImportError:
            self.skipTest("needs pyyaml and jsonschema")
        duration, topics = fm.parse_bag_info(BAG_INFO)
        self.assertAlmostEqual(duration, 83.379416057)
        self.assertEqual(topics[1], {"name": "/odom/raw", "type": "std_msgs/msg/String", "count": 830})
        recorded = {t["name"]: t["count"] for t in topics}
        h = "0" * 64
        self._last = dict(
            cid=fm.capture_id(datetime.date(2026, 11, 10), "gates", "0G", "gates_fG-20261110-2"),
            key="gates", fms="6502", floor_code="0G",
            start=datetime.datetime(2026, 11, 10, 9, 0, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2026, 11, 10, 9, 40, tzinfo=datetime.timezone.utc), ntp=False,
            robot={"name": "robot", "description_sha256": h},
            code=dict(fm.parse_deployed("sha=3cdaead0abcd\nbranch=main\n"), slam=fm.profile()["slam"]["description"]),
            operator="TL", landmark="elevator lobby", room=None,
            sensors=fm.sensors_block(recorded, [("color_head", ["/mapping/camera/color/image_raw/compressed"],
                                                 "camera_color_optical_frame")]),
            map_name_="gates_fG-20261110-2", resolution=0.05,
            map_files=[{"path": "gates_fG-20261110-2.pgm", "sha256": h, "bytes": 10}],
            bag_files=[{"host": "robot", "path": "/b/metadata.yaml", "sha256": h, "bytes": 5}],
            duration_s=duration, topics=topics, color="present")
        doc = fm.capture_manifest(**self._last, map_kind="slam_toolbox")
        self.assertEqual(doc["id"], "20261110-gates-0G-2")
        self.assertEqual([s["id"] for s in doc["sensors"]], ["lidar_front", "odom_pinpoint", "color_head"])
        schema = json.loads((pathlib.Path(__file__).parent / "capture.schema.json").read_text())
        jsonschema.validate(yaml.safe_load(yaml.safe_dump(doc, sort_keys=False)), schema)
        # a robot without SLAM (slam: none, no --map): the manifest has no map, and is still valid
        bare = fm.capture_manifest(**dict(self._last, map_files=[]))
        self.assertNotIn("map", bare)
        jsonschema.validate(yaml.safe_load(yaml.safe_dump(bare, sort_keys=False)), schema)


if __name__ == "__main__":
    unittest.main()
