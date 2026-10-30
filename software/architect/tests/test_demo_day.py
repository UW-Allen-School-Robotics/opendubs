#!/usr/bin/env python3
"""The demo day's promises: the base and a show's arms never move at once (the rest show alone may), anything unknown stops the
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
        self.st = "ARMED_WAITING"

    def status(self):
        return {"running": self.now, "status": self.st if self.now else None}

    def running(self):
        if self.broken:
            raise RuntimeError("the palette answered 503")
        return self.now

    def run(self, routine, start=None):
        self.runs.append(routine)
        self.starts = getattr(self, "starts", []) + [start]
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


class Arms:
    def __init__(self):
        self.out, self.at = [], {}

    def poses(self):
        return dict(self.at)

    def away(self):
        if isinstance(self.out, Exception):
            raise self.out
        return list(self.out)


class Rest(unittest.TestCase):
    """2026-10-29: an arm away from rest while patrolling is brought back by the rest show, with the base
    patrolling on; after a candy show stopped part-way it is a person's to clear (the bucket)."""

    def setUp(self):
        self.clock, self.nav, self.pal, self.arms = Clock(), Nav(), Palette(), Arms()
        self.co = dd.Coordinator(self.nav, People(), self.pal, Safety(), "candy_show", ["monster_mash"],
                                 clock=self.clock, rng=random.Random(0), choreo_every=(300.0, 300.0),
                                 arms=self.arms, rest="arms_rest")
        self.co.still_wait_s = 0.05

    def run_for(self, seconds):
        end = self.clock.t + seconds
        while self.clock.t < end:
            self.clock.t += dd.TICK_S
            self.co.tick()

    def test_an_arm_away_from_rest_is_rested_while_the_base_patrols_on(self):
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("IDLE_NAV", True))
        self.arms.out = ["right"]
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("REST", True))
        self.assertEqual((self.pal.runs, self.pal.starts), (["arms_rest"], ["countdown"]))
        self.run_for(1.0)                                   # the rest show plays: not an external show
        self.assertEqual((self.co.state, self.nav.moving), ("REST", True))
        self.assertNotIn("stop", self.nav.calls)
        self.arms.out, self.pal.now = [], None              # ... and ends, the arm at rest
        self.run_for(1.0)
        self.assertEqual((self.co.state, self.nav.moving), ("IDLE_NAV", True))
        self.run_for(30.0)
        self.assertEqual(self.pal.runs, ["arms_rest"])      # at rest: nothing more is played

    def test_a_rest_refused_at_preflight_leaves_rest_at_once_and_waits_before_the_next(self):
        """opendubs2, 2026-10-29: the deadman let go, the runner refused each rest at preflight, and the
        coordinator sat in REST (busy to "trick or treat") until its 15 s ran out, then tried at once."""
        self.co.tick()
        self.arms.out = ["right"]
        self.pal.last = lambda: self.pal.__dict__.get("_last")
        self.co.tick()
        self.assertEqual(self.co.state, "REST")
        self.pal.now = None                                 # refused at preflight, never seen running
        self.pal._last = {"id": "arms_rest", "status": "REFUSED", "cause": "deadman is False — the deadman must be held"}
        self.co.tick()
        self.assertEqual(self.co.state, "IDLE_NAV")
        self.assertTrue(self.co.request("candy")[0])        # not busy
        self.co._enter("IDLE_NAV", "test")
        self.run_for(dd.REST_RETRY_S - 1)
        self.assertEqual(len(self.pal.runs), 1)
        self.run_for(2.0)
        self.assertEqual(len(self.pal.runs), 2)

    def test_a_refused_rest_is_tried_again_later_and_patrol_goes_on_meanwhile(self):
        self.co.tick()
        self.arms.out, self.pal.refuse = ["left"], "PRECONDITION_FAILED: the deadman must be held"
        self.co.tick()
        self.assertEqual(self.co.state, "IDLE_NAV")
        self.run_for(dd.REST_RETRY_S - 1)
        self.assertEqual(len(self.pal.runs), 1)
        self.assertTrue(self.nav.moving)
        self.run_for(2.0)
        self.assertEqual(len(self.pal.runs), 2)

    def test_after_a_candy_show_stopped_part_way_an_arm_away_pauses_until_resumed(self):
        self.co.tick()
        self.assertTrue(self.co.request("candy")[0])
        self.pal.now = "candy_show"
        self.co.tick()
        self.pal.now, self.arms.out = None, ["right"]
        self.pal.last = lambda: {"id": "candy_show", "status": "ABORTED", "code": "DEADMAN"}
        self.run_for(dd.ENGAGED_S + 1)                      # ENGAGED, then nobody: IDLE_NAV
        self.assertEqual((self.co.state, self.nav.moving), ("PAUSED", False))
        self.assertIn("bucket", self.co.why)
        self.assertTrue(self.co.resume()[0])                # the operator has cleared it
        self.co.tick()
        self.assertEqual((self.co.state, self.pal.runs[-1]), ("REST", "arms_rest"))

    def test_another_show_during_rest_still_stops_the_base(self):
        self.co.tick()
        self.arms.out = ["right"]
        self.co.tick()
        self.pal.now = "monster_mash"                       # started from the Shows page
        self.co.tick()
        self.assertEqual((self.co.state, self.nav.moving), ("CHOREO", False))

    def test_candy_waits_for_the_rest_show(self):
        self.co.tick()
        self.arms.out = ["right"]
        self.co.tick()
        self.assertFalse(self.co.request("candy")[0])       # busy for its few seconds: the listener holds on

    def test_after_a_candy_show_stopped_on_its_way_home_the_arm_is_rested_not_paused(self):
        """opendubs2, 2026-10-29: the deadman let go on the show's last move to rest (J4 at 21 deg), and
        the day paused for a hand nowhere near the bucket."""
        back = [-43.5, -4.5, 29.7, 133.5, -36.9, -39.5, -42.0]          # architect-93cbf2aabeca's last pose
        self.co.candy_home = {"right": back}
        self.co.tick()
        self.assertTrue(self.co.request("candy")[0])
        self.pal.now = "candy_show"
        self.co.tick()
        self.pal.now, self.arms.out = None, ["right"]
        self.arms.at = {"right": [-4.9, -0.7, 4.2, 21.4, -5.9, -6.6, -6.0]}
        self.pal.last = lambda: {"id": "candy_show", "status": "ABORTED", "code": "DEADMAN"}
        self.run_for(dd.ENGAGED_S + 1)
        self.assertEqual((self.co.state, self.pal.runs[-1]), ("REST", "arms_rest"))

    def test_after_a_candy_show_stopped_in_the_bucket_it_still_pauses(self):
        self.co.candy_home = {"right": [-43.5, -4.5, 29.7, 133.5, -36.9, -39.5, -42.0]}
        self.co.tick()
        self.assertTrue(self.co.request("candy")[0])
        self.pal.now = "candy_show"
        self.co.tick()
        self.pal.now, self.arms.out = None, ["right"]
        self.arms.at = {"right": [4.7, -4.5, 1.0, 83.0, 7.8, 7.8, -73.7]}   # the hand down in the bucket
        self.pal.last = lambda: {"id": "candy_show", "status": "ABORTED", "code": "DEADMAN"}
        self.run_for(dd.ENGAGED_S + 1)
        self.assertEqual(self.co.state, "PAUSED")

    def test_the_way_home_is_read_from_the_show(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("cues:\n- {t: 0.5, slot: ARM_R, pose: rest}\n- {t: 5, slot: ARM_R, joints: {1: 10, 4: 90}}\n"
                    "- {t: 9, slot: ARM_R, pose: rest}\n- {t: 1, slot: ARM_L, pose: rest}\n")
        self.assertEqual(dd.candy_home(f.name), {"right": [10.0, 0, 0, 90.0, 0, 0, 0]})
        os.unlink(f.name)
        self.assertEqual(dd.candy_home("/nonexistent.yaml"), {})
        self.assertTrue(dd.on_way_home([5, 0, 0, 45, 0, 0, 0], [10, 0, 0, 90, 0, 0, 0]))
        self.assertFalse(dd.on_way_home([5, 0, 0, 45, 0, 0, 30], [10, 0, 0, 90, 0, 0, 0]))

    def test_unknown_arms_leave_patrol_as_it_was(self):
        self.arms.out = RuntimeError("nothing on /joint_states")
        self.run_for(1.0)
        self.assertEqual((self.co.state, self.nav.moving, self.pal.runs), ("IDLE_NAV", True, []))

    def test_away_is_any_joint_past_the_tolerance_and_a_missing_arm_is_unknown(self):
        pos = {f"openarm_{s}_joint{j}": 0.0 for s in ("left", "right") for j in range(1, 8)}
        self.assertEqual(dd.arms_away(pos, dd.RosArms.SIDES), [])
        pos["openarm_right_joint4"] = 0.2                   # 11.5 degrees
        self.assertEqual(dd.arms_away(pos, dd.RosArms.SIDES), ["right"])
        del pos["openarm_left_joint1"]
        with self.assertRaises(RuntimeError):
            dd.arms_away(pos, dd.RosArms.SIDES)


class Selector:
    """base_selector.Lease's rules: a claim is granted when the base is free or already the claimant's, never
    taken from a live holder; a release frees it for its holder or for "*"."""

    def __init__(self, owner=None):
        self.owner, self.log, self.nav = owner, [], None

    def _say(self):
        if self.nav:
            self.nav.on_owner('{"owner": %s}' % ("null" if self.owner is None else '"%s"' % self.owner))

    def claim(self, d):
        self.log.append(("claim", d["owner"]))
        if self.owner in (None, d["owner"]):
            self.owner = d["owner"]
        self._say()

    def release(self, d):
        self.log.append(("release", d["owner"]))
        if d["owner"] in (self.owner, "*"):
            self.owner = None
        self._say()


class Hound(unittest.TestCase):
    """HoundNav (2026-10-29): the base is TAKEN from the nav for the candy (the operator's release, then our
    claim, renewed), given back to it for patrol, and counts as still only when it is ours and /odom at rest."""

    def setUp(self):
        self.clock, self.sent, self.sel = Clock(), [], Selector(owner="nav")
        self.nav = dd.HoundNav(publish=self.sent.append, claim=self.sel.claim, release=self.sel.release,
                               clock=self.clock)
        self.sel.nav = self.nav
        self.sel._say()
        self.nav.on_odom(0.0, 0.0, 0.0)

    def test_stop_takes_the_base_from_the_nav_and_patrol_gives_it_back(self):
        self.nav.patrol()
        self.sel.owner = "nav"; self.sel._say()             # the nav drives
        self.nav.stop()
        self.assertEqual(self.sel.log[-2:], [("release", "*"), ("claim", "demo_day")])
        self.assertEqual(self.sel.owner, "demo_day")
        self.assertTrue(self.nav.still())
        self.nav.patrol()
        self.assertEqual((self.sel.log[-1], self.sel.owner), (("release", "demo_day"), None))
        self.assertFalse(self.nav.still())

    def test_ours_is_renewed_and_the_navs_claims_are_refused_meanwhile(self):
        self.nav.stop()
        self.sel.claim({"owner": "nav"})                    # the bridge renews as ever: refused
        self.assertEqual(self.sel.owner, "demo_day")
        n = len(self.sel.log)
        self.clock.t += dd.HoundNav.RENEW_S
        self.nav.tick()
        self.assertEqual(self.sel.log[n:], [("claim", "demo_day")])

    def test_a_race_the_nav_won_is_taken_again(self):
        self.nav.stop()
        self.sel.owner = "nav"; self.sel._say()             # its renewal landed between the release and our claim
        self.assertFalse(self.nav.still())
        self.clock.t += dd.HoundNav.RETAKE_S
        self.nav.tick()
        self.assertEqual(self.sel.owner, "demo_day")

    def test_never_taken_from_another_owner_and_then_not_still(self):
        self.sel.owner = "teleop"; self.sel._say()
        self.nav.stop()
        self.clock.t += 5.0
        self.nav.tick()
        self.assertEqual(self.sel.owner, "teleop")
        self.assertNotIn(("release", "*"), self.sel.log)
        self.assertFalse(self.nav.still())

    def test_rolling_or_stale_is_not_still(self):
        self.nav.stop()
        self.nav.on_odom(0.3, 0.0, 0.0)
        self.assertFalse(self.nav.still())
        self.nav.on_odom(0.0, 0.0, 0.0)
        self.clock.t += 1.0                                 # /odom gone quiet
        self.assertFalse(self.nav.still())

    def test_paused_a_free_base_stays_free_but_the_nav_is_still_kept_off(self):
        self.nav.stop()
        self.nav.pause()
        self.assertIsNone(self.sel.owner)                   # ours given up: teleop, the Shows page may have it
        self.clock.t += 5.0
        self.nav.tick()
        self.assertIsNone(self.sel.owner)
        self.sel.owner = "nav"; self.sel._say()             # the nav claims it back while paused
        self.clock.t += dd.HoundNav.RETAKE_S
        self.nav.tick()
        self.assertEqual(self.sel.owner, "demo_day")

    def test_the_hold_is_still_said_for_a_bridge_that_honours_it(self):
        self.nav.patrol()
        self.nav.stop()
        self.clock.t += dd.HoundNav.RESEND_S
        self.nav.tick()
        self.assertEqual(self.sent[-3:], [False, True, True])

    def test_patrol_candy_patrol_with_the_coordinator(self):
        co = dd.Coordinator(self.nav, People(), Palette(), Safety(), "candy_show", ["monster_mash"],
                            clock=self.clock, rng=random.Random(0))
        co.still_wait_s = 0.05
        co.tick()
        self.sel.owner = "nav"; self.sel._say()             # patrolling: the nav has it
        self.assertTrue(co.request("candy")[0])             # taken, still: granted
        self.assertEqual((co.state, self.sel.owner), ("CANDY", "demo_day"))
        co.palette.now = "candy_show"; co.tick()
        co.palette.now = None; co.tick()                    # the run ends: with a visitor, still held
        self.assertEqual((co.state, self.sel.owner), ("ENGAGED", "demo_day"))
        for _ in range(int((dd.ENGAGED_S + 1) / dd.TICK_S)):
            self.clock.t += dd.TICK_S
            co.tick()
        self.assertEqual((co.state, self.sel.owner), ("IDLE_NAV", None))   # given back: the nav takes it


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

    def test_random_picks_are_the_short_shows_and_the_dances_come_when_asked(self):
        co = dd.Coordinator(self.nav, self.people, self.pal, self.safe, "candy_show",
                            ["monster_mash", "ghostbusters", "them_bones"], clock=self.clock, rng=random.Random(0),
                            choreo_every=(300.0, 300.0), shorts=["wave-and-nod", "robotic-smile"])
        co.still_wait_s = 0.05
        self.co = co
        co.tick()
        self.run_for(300.4)                                  # due, armed, and seen running
        self.assertIn(self.pal.runs[-1], ("wave-and-nod", "robotic-smile"))
        self.pal.now = None
        self.run_for(0.4)
        co.request("choreo", "OpenDubs, dance")
        self.assertIn(self.pal.runs[-1], ("monster_mash", "ghostbusters", "them_bones"))
        co.tick()                                            # seen running
        self.pal.now = None
        self.run_for(0.4)
        co.request("choreo", "do the wave and nod")
        self.assertEqual(self.pal.runs[-1], "wave-and-nod")

    def test_trick_or_treat_cancels_a_show_still_waiting_for_its_go(self):
        self.co.request("choreo", "play ghostbusters")
        self.assertEqual(self.co.state, "CHOREO")
        ok, why = self.co.request("candy")                   # armed, no deadman press yet
        self.assertTrue(ok, why)
        self.assertEqual((self.co.state, self.pal.stops, self.nav.moving), ("CANDY", 1, False))

    def test_a_started_show_is_never_cancelled_for_candy(self):
        self.co.request("choreo", "play ghostbusters")
        self.pal.st = "RUNNING"                              # the GO was given: it is moving
        ok, why = self.co.request("candy")
        self.assertFalse(ok)
        self.assertIn("busy", why)
        self.assertEqual(self.pal.stops, 0)
        self.pal.st = "COUNTDOWN"                            # pressed and counting: started too
        self.assertFalse(self.co.request("candy")[0])

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
