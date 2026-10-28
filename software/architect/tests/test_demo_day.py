#!/usr/bin/env python3
"""The demo day's promises: the base and the arms never move at once, anything unknown stops the
base, a show that never starts is disarmed before the base drives again, and an e-stop stays a
pause until the operator resumes.

    python3 software/architect/tests/test_demo_day.py      # stdlib only
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from opendubs_architect import demo_day as dd  # noqa: E402
from opendubs_architect import listener as ls  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Nav:
    def __init__(self):
        self.moving, self.calls, self.stuck = False, [], False

    def patrol(self):
        self.moving = True
        self.calls.append("patrol")

    def approach(self, person, standoff_m):
        self.moving = True
        self.calls.append(("approach", round(person.distance_m, 1)))

    def stop(self):
        self.moving = self.stuck
        self.calls.append("stop")

    def still(self):
        return not self.moving


class People:
    def __init__(self):
        self.p = None

    def nearest(self):
        if isinstance(self.p, Exception):
            raise self.p
        return self.p


class Palette:
    def __init__(self):
        self.now, self.runs, self.stops, self.refuse, self.broken = None, [], 0, None, False

    def running(self):
        if self.broken:
            raise RuntimeError("the palette answered 503")
        return self.now

    def run(self, routine):
        self.runs.append(routine)
        if self.refuse:
            return False, self.refuse
        self.now = routine
        return True, "armed"

    def stop(self):
        self.stops += 1
        self.now = None


class Safety:
    def __init__(self):
        self.estop = False

    def ok(self):
        return (False, "e-stop: button") if self.estop else (True, "")


class Day(unittest.TestCase):
    def setUp(self):
        self.clock, self.nav, self.people, self.pal, self.safe = Clock(), Nav(), People(), Palette(), Safety()
        self.co = dd.Coordinator(self.nav, self.people, self.pal, self.safe, "candy_show",
                                 ["monster_mash", "ghostbusters", "them_bones"], clock=self.clock,
                                 rng=random.Random(0), choreo_every=(300.0, 300.0))
        self.co.still_wait_s = 0.05

    def run_for(self, seconds):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += dd.TICK_S
            self.co.tick()

    def test_a_visitor_is_approached_and_the_base_stops_short_of_them(self):
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("IDLE_NAV", True))
        self.people.p = dd.Person(2.5, 0.0)
        self.run_for(0.6)
        self.assertEqual(self.co.state, "IDLE_NAV")          # a passer-by is not a visitor yet
        self.run_for(0.6)
        self.assertEqual(self.co.state, "APPROACH")
        self.assertIn(("approach", 2.5), self.nav.calls)
        self.people.p = dd.Person(1.3, 5.0)
        self.run_for(0.4)
        self.assertEqual((self.co.state, self.nav.moving), ("ENGAGED", False))
        self.people.p = None
        self.run_for(dd.ENGAGED_S + 1)
        self.assertEqual(self.co.state, "IDLE_NAV")

    def test_candy_is_granted_only_with_the_base_still(self):
        self.co.tick()
        self.nav.stuck = True                                # told to stop, still rolling
        ok, why = self.co.request("candy")
        self.assertFalse(ok)
        self.assertIn("rest", why)
        self.nav.stuck = False
        ok, _ = self.co.request("candy")
        self.assertTrue(ok)
        self.assertEqual((self.co.state, self.nav.moving), ("CANDY", False))
        self.assertEqual(self.co.request("choreo", "dance")[0], False)     # busy
        self.pal.now = "candy_show"                          # the listener started it; it runs, then ends
        self.run_for(1)
        self.pal.now = None
        self.run_for(0.4)
        self.assertEqual((self.co.state, self.nav.moving), ("ENGAGED", False))

    def test_a_show_started_elsewhere_stops_the_base(self):
        self.co.tick()
        self.pal.now = "ghostbusters"                        # the Shows page's button
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("CHOREO", False))
        self.pal.now = None
        self.run_for(0.4)
        self.assertEqual(self.co.state, "IDLE_NAV")

    def test_an_unreadable_palette_or_person_never_drives(self):
        self.co.tick()
        self.pal.broken = True
        self.run_for(1)
        self.assertFalse(self.nav.moving)
        self.pal.broken = False
        self.people.p = RuntimeError("perception down")
        self.run_for(5)
        self.assertEqual(self.co.state, "IDLE_NAV")          # unseen is not approached
        self.assertTrue(self.nav.moving)

    def test_a_random_pick_waits_for_the_deadman_and_a_refusal_goes_back_to_patrol(self):
        self.co.tick()
        self.run_for(300)
        self.assertEqual(self.co.state, "CHOREO")
        self.assertFalse(self.nav.moving)
        first = self.pal.runs[-1]
        self.pal.now = None                                  # armed, never pressed, and not listed either
        self.run_for(dd.SHOW_START_S + 1)
        self.assertEqual(self.co.state, "IDLE_NAV")
        self.assertGreaterEqual(self.pal.stops, 1)           # disarmed before the base drove again
        self.pal.refuse = "ch5 is 'MANUAL'"
        self.run_for(301)
        self.assertNotEqual(self.pal.runs[-1], first)        # not the same show twice running
        self.assertEqual(self.co.state, "IDLE_NAV")

    def test_an_estop_pauses_until_the_operator_resumes(self):
        self.co.tick()
        self.safe.estop = True
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("PAUSED", False))
        self.assertFalse(self.co.request("candy")[0])
        self.safe.estop = False
        self.run_for(2)
        self.assertEqual(self.co.state, "PAUSED")            # released is not resumed
        self.assertTrue(self.co.resume()[0])
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("IDLE_NAV", True))

    def test_a_resume_after_a_long_pause_does_not_start_a_show_at_once(self):
        self.co.tick()
        self.co.pause()
        self.clock.t += 3600                                 # paused longer than the random pick's wait
        self.co.resume()
        self.run_for(5)
        self.assertEqual((self.co.state, self.pal.runs), ("IDLE_NAV", []))

    def test_a_refused_show_says_why(self):
        """opendubs2, 2026-10-27: ghostbusters refused at preflight (the head's face page not up) read
        "ghostbusters ended" on the dashboard."""
        self.pal.last = lambda: {"id": "ghostbusters", "status": "REFUSED", "code": "SHOW_HEAD_NOT_UP",
                                 "cause": "the head is not ready for a show"}
        self.co.request("choreo", "play ghostbusters")
        self.co.tick()
        self.pal.now = None
        self.co.tick()
        self.assertEqual(self.co.state, "IDLE_NAV")
        self.assertIn("SHOW_HEAD_NOT_UP", self.co.why)

    def test_a_pause_stops_a_running_show(self):
        self.co.request("choreo", "play the monster mash")
        self.assertEqual(self.pal.runs, ["monster_mash"])
        self.co.pause()
        self.assertEqual((self.co.state, self.pal.stops), ("PAUSED", 1))


class Words(unittest.TestCase):
    C = ["monster_mash", "ghostbusters", "them_bones"]

    def test_named_and_unnamed_requests(self):
        rng = random.Random(1)
        self.assertEqual(dd.pick_choreo("play the monster mash", self.C, rng=rng), "monster_mash")
        self.assertEqual(dd.pick_choreo("ghost busters please", self.C, rng=rng), "ghostbusters")
        self.assertEqual(dd.pick_choreo("do the bones one", self.C, rng=rng), "them_bones")
        self.assertIn(dd.pick_choreo("dance for us", self.C, rng=rng), self.C)
        self.assertIsNone(dd.pick_choreo("what time is it", self.C, rng=rng))
        self.assertTrue(ls.wants_show("dance for us"))
        self.assertFalse(ls.wants_show("stop the music"))


class ListenerGate(unittest.TestCase):
    def test_a_refused_candy_request_starts_no_run(self):
        calls = []

        class No:
            def request(self, want, text=""):
                return False, "busy: ghostbusters"

        shows = ls.Shows("http://x", "candy_show", call=lambda *a: calls.append(a) or (200, {}), day=No())
        self.assertEqual(shows.post("trigger", "trick or treat"), "ARMED")
        self.assertFalse([c for c in calls if c[0] == "POST"])


if __name__ == "__main__":
    unittest.main()
