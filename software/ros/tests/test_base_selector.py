#!/usr/bin/env python3
"""base_selector: one owner at a time, renewal, expiry, and release. Pure Python; no ROS.
Run: python3 software/ros/tests/test_base_selector.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import base_selector as b  # noqa: E402


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.l = b.Lease()

    def test_a_free_base_is_granted(self):
        self.assertEqual(self.l.claim("show", 2, now=0), (True, "granted"))
        self.assertEqual(self.l.current(1), "show")

    def test_a_held_base_is_never_taken(self):
        self.l.claim("show", 2, now=0)
        ok, why = self.l.claim("nav", 2, now=1)
        self.assertFalse(ok); self.assertIn("held by show", why)
        self.assertEqual(self.l.current(1), "show")

    def test_the_holder_renews(self):
        self.l.claim("show", 2, now=0)
        self.assertEqual(self.l.claim("show", 2, now=1.5), (True, "renewed"))
        self.assertEqual(self.l.current(3), "show")          # renewed at 1.5 for 2 s: still held

    def test_a_dead_holder_frees_the_base(self):
        self.l.claim("show", 2, now=0)
        self.assertIsNone(self.l.current(2.0))
        self.assertTrue(self.l.claim("nav", 2, now=2.1)[0])

    def test_ttl_is_capped(self):
        self.l.claim("nav", 1000, now=0)
        self.assertIsNone(self.l.current(b.TTL_MAX_S))

    def test_release_by_the_holder_and_by_the_operator(self):
        self.l.claim("show", 2, now=0)
        self.assertFalse(self.l.release("nav", now=0.5))     # not yours
        self.assertTrue(self.l.release("show", now=0.5))
        self.l.claim("nav", 2, now=0.6)
        self.assertTrue(self.l.release("*", now=0.7))        # the operator's release
        self.assertIsNone(self.l.current(0.8))

    def test_bad_claims_are_refused(self):
        for owner, ttl in (("Nav", 1), ("1nav", 1), ("nav-stack", 1), ("", 1), (None, 1), ("nav", 0), ("nav", "x")):
            self.assertFalse(self.l.claim(owner, ttl, now=0)[0], (owner, ttl))


class ParseTest(unittest.TestCase):
    def test_json_or_a_bare_name(self):
        self.assertEqual(b.parse('{"owner": "nav", "ttl_s": 2}')["owner"], "nav")
        self.assertEqual(b.parse("nav")["owner"], "nav")
        self.assertEqual(b.parse('"nav"')["owner"], "nav")


if __name__ == "__main__":
    unittest.main()
