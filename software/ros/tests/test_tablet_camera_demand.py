#!/usr/bin/env python3
"""tablet_camera_demand.Demand: when the camera-demand node POSTs. Pure Python; no ROS needed.
Run: python3 software/ros/tests/test_tablet_camera_demand.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import tablet_camera_demand as tcd  # noqa: E402


class DemandTest(unittest.TestCase):
    def test_body_is_a_bool_per_camera_plus_counts(self):
        b = tcd.Demand().body({"front": 0, "rear": 2})
        self.assertEqual(b, {"front": False, "rear": True, "counts": {"front": 0, "rear": 2}})

    def test_missing_camera_counts_as_no_demand(self):
        self.assertEqual(tcd.Demand().body({})["rear"], False)

    def test_first_body_is_always_sent(self):
        d = tcd.Demand()
        self.assertTrue(d.due(d.body({}), now=0.0))

    def test_unchanged_demand_waits_for_the_heartbeat(self):
        d = tcd.Demand(heartbeat_s=2.0)
        b = d.body({"rear": 1})
        d.accepted(b, now=10.0)
        self.assertFalse(d.due(d.body({"rear": 1}), now=11.0))
        self.assertFalse(d.due(d.body({"rear": 3}), now=11.5))   # count changed, demand did not
        self.assertTrue(d.due(d.body({"rear": 1}), now=12.0))

    def test_a_change_of_demand_is_sent_at_once(self):
        d = tcd.Demand(heartbeat_s=2.0)
        d.accepted(d.body({"rear": 1}), now=10.0)
        self.assertTrue(d.due(d.body({"rear": 0}), now=10.1))
        self.assertTrue(d.due(d.body({"rear": 1, "front": 1}), now=10.1))

    def test_a_failed_post_is_retried_next_tick(self):
        d = tcd.Demand(heartbeat_s=2.0)
        d.accepted(d.body({}), now=10.0)
        b = d.body({"front": 1})
        self.assertTrue(d.due(b, now=10.5))   # not accepted (POST failed) ...
        self.assertTrue(d.due(b, now=11.5))   # ... so still due on the next tick


if __name__ == "__main__":
    unittest.main()
