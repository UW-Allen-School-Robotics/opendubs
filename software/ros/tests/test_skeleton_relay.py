#!/usr/bin/env python3
"""skeleton_relay: the privacy refusal, the newest-only slot and the heartbeat. Pure Python; no ROS.
Run: python3 software/ros/tests/test_skeleton_relay.py"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import skeleton_relay as sr  # noqa: E402


class PrivacyTest(unittest.TestCase):
    def test_image_and_depth_types_are_refused(self):
        for t in ("sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage", "sensor_msgs/msg/PointCloud2",
                  "sensor_msgs/msg/CameraInfo", "orbbec_msgs/msg/DepthImage", "foo_msgs/msg/VideoFrame"):
            self.assertTrue(sr.forbidden(t), t)

    def test_skeleton_types_pass(self):
        for t in ("people_msgs/msg/Skeletons", "body_msgs/msg/BodyArray", "vision_msgs/msg/Detection3DArray"):
            self.assertFalse(sr.forbidden(t), t)

    def test_an_adapter_naming_an_image_type_stops_the_relay(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "skeleton_feed.py").write_text('MSG_TYPE = "sensor_msgs/msg/Image"\n')
            sys.modules.pop("skeleton_feed", None)
            with self.assertRaises(SystemExit):
                sr.load_adapter(d)
            sys.modules.pop("skeleton_feed", None)


class UnconfiguredTest(unittest.TestCase):
    def test_msg_type_none_means_idle_not_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "skeleton_feed.py").write_text("MSG_TYPE = None\n")
            sys.modules.pop("skeleton_feed", None)
            feed, msg_type, cls = sr.load_adapter(d)
            self.assertIsNone(msg_type)
            self.assertIsNone(cls)
            sys.modules.pop("skeleton_feed", None)


class SlotTest(unittest.TestCase):
    def test_a_newer_frame_replaces_an_unsent_one(self):
        s = sr.Slot()
        s.put({"t": 1}); s.put({"t": 2})
        self.assertEqual(s.take(), {"t": 2})
        self.assertIsNone(s.take())
        self.assertEqual(s.dropped, 1)


class HeartbeatTest(unittest.TestCase):
    def test_silence_sends_an_empty_frame_once_a_second(self):
        self.assertTrue(sr.due_heartbeat(now=10.0, last_message=8.9, last_sent=8.9))
        self.assertFalse(sr.due_heartbeat(now=10.0, last_message=9.5, last_sent=8.0))   # the feed is live
        self.assertFalse(sr.due_heartbeat(now=10.0, last_message=8.0, last_sent=9.5))   # just sent one
        hb = sr.heartbeat(12.5)
        self.assertEqual(hb, {"v": 1, "t": 12.5, "frame": "skeleton_camera", "people": [], "status": "stale"})


if __name__ == "__main__":
    unittest.main()
