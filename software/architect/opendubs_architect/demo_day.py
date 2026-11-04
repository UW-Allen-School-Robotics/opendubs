"""The Halloween demo day: one coordinator decides what the robot is doing, so that two things that
must never overlap (the base driving and a show moving the arms) never do. The one overlap allowed is
the rest show (REST), which only brings the arms home while the base patrols.

    python3 -m opendubs_architect.demo_day --choreo monster_mash ghostbusters them_bones
    python3 -m opendubs_architect.demo_day --people fake --palette fake    # a desk: nothing is read or moved

THE STATES. One at a time, IDLE_NAV the default:

    IDLE_NAV   the base patrols the sectioned-off area (Nav.patrol), waiting for someone
    APPROACH   a person has stood within NEAR_M for SEEN_FOR_S: the base goes slowly toward them and
               stops STANDOFF_M away (Nav.approach). Nobody seen for LOST_S: back to IDLE_NAV
    ENGAGED    stopped in front of them, waiting for "trick or treat". Nobody for ENGAGED_S: IDLE_NAV
    CANDY      the candy show (the listener's --show routine): asked for by the listener on "trick or
               treat" (POST /api/request {"want": "candy"}), granted only once the base is still
    REST       an arm away from rest while patrolling: the rest show (--rest) takes each arm back to rest
               (the runner's slow start-pose move, started at once while the deadman is held) while the
               base keeps patrolling (2026-10-29: the base and the arms may move at once for this show
               alone); the arm controllers hold rest from then on. Checked on every tick of
               IDLE_NAV, so an arm pushed away is put back. After a candy show that stopped part-way, with
               an arm still away and NOT on the show's own last move home, it PAUSES instead: the hand may
               be in or over the bucket, and a straight move to rest from there hits its wall (found
               2026-10-28), so a person clears it first. On that last move (from the show's last pose
               straight to rest, the move its clearance stamp covers) the rest show only finishes it
    CHOREO     a preprogrammed palette show (arms, music, the face). A random pick every CHOREO_EVERY_S
               of patrolling, from the SHORT shows (--random: wave-and-nod, robotic-smile, celebrate,
               9-33 s), so a visitor never waits out a long dance; the full dances (--choreo:
               monster_mash, ghostbusters, them_bones, 3-4 min) when asked ("OpenDubs, dance", "...
               play the monster mash"); any show of either list by its name
    PAUSED     the operator's pause (POST /api/pause), or the e-stop: everything stops. Only the
               operator's POST /api/resume leaves it, so an e-stop released does not restart the base.
               The boot unit starts here (--start-paused): a robot switched on for other work never
               arms a show by itself

THE RULES, each kept here and tested (tests/test_demo_day.py):
  * No show is started, and no candy granted, until the base is stopped and still (Nav.still).
  * The base moves only in IDLE_NAV and APPROACH, and only while the show palette can be read and
    nothing on it runs: a show started from the Shows page stops the base too (an EXTERNAL show).
  * Anything unknown stops the base: the palette or the robot's state unreadable, a driver's error.
  * Every show starts the way the Shows page starts it, with `start: deadman`: the supervisor's fresh
    press is the go. A random pick never moves an arm by itself. The rest show is the exception: it
    starts with `countdown` while the deadman is held (refused when it is not), since it only brings the
    arms home at the runner's warning-free speed.
  * "Trick or treat" wins over a show still waiting for its go (armed, or being checked: nothing has
    moved): that show is cancelled and the candy granted. A show past its go (COUNTDOWN on) is never
    cancelled for candy: stopping a dance part-way leaves the arms where they are.

WHO DRIVES THE BASE is not decided here. The robot moves its base for software only in CH5 AUTO
while a person holds the deadman (docs/interfaces/navigation.md), at most at the robot's speed cap,
and the drive bridge's people guard refuses motion toward anything within 0.5 m. Nav is the door:
until the nav stack (the Thor stack lane's, in progress) is here, StubNav says what it would do and
moves nothing. A real Nav claims the base lease (/base/claim) under its own name and releases it
on stop(), so a show's dance tracker can take it.

WHO IS NEAR comes from the perception services for now (PerceptionPeople: /perception/locate
"person", in the chassis frame, distance from base_link), until the skeleton feed
(software/choreo/skeleton_feed.py) has a message type.

GET /api/state is the coordinator's state for the dashboard and the face: {state, since_s, why,
person, nav, show, choreo}.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NEAR_M = 3.0                 # a person this near (from base_link, metres) is worth approaching
SEEN_FOR_S = 1.0             # ... once seen that near for this long: a passer-by is not a visitor
STANDOFF_M = 1.2             # APPROACH stops this far from them (the people guard's own stop is 0.5 m)
LOST_S = 10.0                # APPROACH gives up after this long without seeing them
ENGAGED_S = 20.0             # ENGAGED goes back to patrolling after this long with nobody near
SHOW_START_S = 15.0          # a show granted or posted must show on the palette within this. The palette
                             # lists a run as running from the moment it is ARMED (waiting for the deadman
                             # press, up to the runner's own ARM_TIMEOUT_S, 300 s), so the base stays put
                             # the whole time a show waits for its go
STILL_WAIT_S = 3.0           # how long a request waits for the base to come to rest
CHOREO_EVERY_S = (240.0, 480.0)   # a random pick after this long patrolling (uniform), reset by any show
TICK_S = 0.2
REST_TOL_DEG = 10.0          # a joint further than this from rest (0, signed degrees) is away: the runner's own
                             # at-rest tolerance (SKIPPED_ARM_TOL_DEG); a resting arm sags about 4 degrees at J4
HOME_TOL_DEG = 6.0           # an arm this close (each joint) to the candy show's last move home is on it
REST_RETRY_S = 15.0          # a rest that did not happen (the deadman let go, a refusal) is tried again after this
BASE_LINK_IN_CHASSIS = (0.324, 0.0, -0.024)   # description/opendubs/opendubs.urdf: datum is 0.324 m behind

STATES = ("IDLE_NAV", "APPROACH", "ENGAGED", "CANDY", "CHOREO", "REST", "PAUSED")
MOVING = ("IDLE_NAV", "APPROACH", "REST")        # the only states in which the base may move
NOT_STARTED = ("VALIDATING", "PREFLIGHT", "ARMED_WAITING")   # the runner's, before the deadman's GO

_SHOW_WORDS = re.compile(r"\b(dance|dancing|show|song|music|perform)\b", re.I)


class Person:
    def __init__(self, distance_m: float, bearing_deg: float, chassis=None):
        self.distance_m, self.bearing_deg, self.chassis = distance_m, bearing_deg, chassis

    def doc(self) -> dict:
        return {"distance_m": round(self.distance_m, 2), "bearing_deg": round(self.bearing_deg, 1)}


def person_from_chassis(p) -> Person:
    """A point in the chassis frame -> its distance and bearing from base_link (+ is to the left)."""
    x, y = p[0] - BASE_LINK_IN_CHASSIS[0], p[1] - BASE_LINK_IN_CHASSIS[1]
    return Person(math.hypot(x, y), math.degrees(math.atan2(y, x)), list(p))


def match_show(text: str, shows: list):
    """The show the words name ("monster mash", "ghost busters", "the bones one", "wave and nod"), or None."""
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    squashed = re.sub(r"[^a-z]", "", (text or "").lower())
    for name in shows:                           # the whole name: "monster mash", "ghost busters"
        if re.sub(r"[^a-z]", "", name.lower()) in squashed:
            return name
    for name in shows:                           # one distinctive word is enough: "play the bones one"
        if any(w in words for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 4):
            return name
    return None


def pick_choreo(text: str, choreo: list, last: str | None = None, rng=random):
    """The show a spoken request names, else a random one, not `last` if there is a choice. None when
    the words ask for no show."""
    named = match_show(text, choreo)
    if named:
        return named
    if text is not None and not _SHOW_WORDS.search(text):
        return None
    pool = [c for c in choreo if c != last] or list(choreo)
    return rng.choice(pool) if pool else None


class Coordinator:
    """Transport-free. `tick()` every TICK_S; `request(want, text)` from the listener; `pause()` and
    `resume()` from the operator. The drivers:

      nav      patrol(), approach(person, standoff_m), stop(), still() -> bool
      people   nearest() -> Person or None (raises when it cannot be read)
      palette  running() -> routine id or None (raises when it cannot be read),
               run(routine) -> (ok, why), stop()
      safety   ok() -> (bool, why): False on an e-stop or when the robot's state cannot be read
      arms     away() -> [the arms away from rest] (raises when they cannot be read); None: not checked;
               poses() -> {side: 7 signed degrees}, for the candy show's way home
      candy_home  {side: the candy show's last pose before its final rest, 7 signed degrees}: an arm on
               the straight line from there to rest may be rested after the show stopped part-way
    """

    def __init__(self, nav, people, palette, safety, candy: str, choreo: list, clock=time.monotonic,
                 rng=None, log=None, choreo_every=CHOREO_EVERY_S, shorts=None, arms=None, rest=None,
                 candy_home=None):
        self.nav, self.people, self.palette, self.safety = nav, people, palette, safety
        self.candy, self.choreo = candy, list(choreo)
        self.shorts = list(shorts) if shorts else list(choreo)   # the random picks: short, so a visitor waits little
        self.clock, self.rng, self._log_fn = clock, rng or random.Random(), log
        self.choreo_every = choreo_every
        self.still_wait_s = STILL_WAIT_S
        self.lock = threading.RLock()
        self.state, self.since, self.why = None, clock(), ""
        self.person, self.near_since, self.seen_at = None, None, None
        self.show, self.show_seen, self.last_choreo = None, False, None
        self.next_choreo = None
        self.nav_doing = None
        self._people_err = None
        self.arms, self.rest = arms, rest          # the arms' measured pose, and the show that rests them
        self.candy_home = dict(candy_home or {})
        self.rest_after = 0.0                      # no rest tried before this (a retry's wait)
        self.rest_blocked = None                   # why the arms must not be rested by a show (a person must)
        self._arms_err = None
        self._enter("IDLE_NAV", "start")

    # ---- the states -------------------------------------------------------------------------------
    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    def _enter(self, state: str, why: str) -> None:
        old, self.state, self.since, self.why = self.state, state, self.clock(), why
        if state == "PAUSED" and hasattr(self.nav, "pause"):
            try:
                self.nav.pause()                 # first: the nav may not drive, and a free base is left free
            except Exception as e:  # noqa: BLE001
                self._log("nav_error", what="pause", error=str(e))
        if state not in MOVING:
            self._stop_base()
        if state == "PAUSED":
            self.next_choreo = None              # a resume starts a fresh wait, never a show at once
        if state == "IDLE_NAV":
            self.near_since = None
            if self.next_choreo is None:
                self.next_choreo = self.clock() + self.rng.uniform(*self.choreo_every)
        if state in ("CANDY", "CHOREO"):
            self.next_choreo = None              # a show resets the random pick's clock
        if state not in ("CANDY", "CHOREO", "REST"):
            self.show, self.show_seen = None, False
        self._log("state", state=state, was=old, why=why)

    def _stop_base(self) -> None:
        if self.nav_doing is not None:
            self.nav_doing = None
            try:
                self.nav.stop()
            except Exception as e:  # noqa: BLE001  a nav that cannot be told is reported, never retried blind
                self._log("nav_error", what="stop", error=str(e))

    def _drive(self, what: str, *args) -> None:
        """Ask the nav to patrol or approach; any error stops the base instead."""
        try:
            getattr(self.nav, what)(*args)
            self.nav_doing = what
        except Exception as e:  # noqa: BLE001
            self._log("nav_error", what=what, error=str(e))
            self.nav_doing = "error"
            self._stop_base()

    def _base_still(self, wait_s: float) -> bool:
        self.nav_doing = "stop"                  # told again even if it was told before: a show waits on it
        self._stop_base()
        end = time.monotonic() + wait_s          # real time: the base takes real time to stop
        while True:
            try:
                if self.nav.still():
                    return True
            except Exception as e:  # noqa: BLE001
                self._log("nav_error", what="still", error=str(e))
                return False
            if time.monotonic() >= end:
                return False
            time.sleep(0.05)

    def _running(self):
        """(readable, the routine running or None)."""
        try:
            return True, self.palette.running()
        except Exception as e:  # noqa: BLE001
            self._log("palette_error", error=str(e))
            return False, None

    def tick(self) -> None:
        with self.lock:
            now = self.clock()
            ok, why = self._safe()
            if not ok and self.state != "PAUSED":
                self._enter("PAUSED", why)
            if self.state == "PAUSED":
                return
            readable, running = self._running()
            ours = self.rest if self.state == "REST" else None   # the rest show plays while the base patrols
            if self.state in MOVING and (not readable or (running and running != ours)):
                if running:                          # started elsewhere (the Shows page): it owns the robot now
                    self._enter("CHOREO", f"external show {running}")
                    self.show, self.show_seen = running, True
                else:
                    self._stop_base()                # unknown: stay put until the palette answers
                return
            self._see(now)
            getattr(self, "_tick_" + self.state.lower())(now, running)

    def _safe(self):
        try:
            return self.safety.ok()
        except Exception as e:  # noqa: BLE001
            return False, f"the robot's state cannot be read ({e})"

    def _see(self, now: float) -> None:
        try:
            p = self.people.nearest()
        except Exception as e:  # noqa: BLE001  unseen is not "nobody": it just does not count as seen
            if str(e) != self._people_err:       # once per kind of failure, not five times a second
                self._people_err = str(e)
                self._log("people_error", error=str(e))
            self.person, self.near_since = None, None
            return
        if self._people_err is not None:
            self._people_err = None
            self._log("people_ok")
        self.person = p
        if p is not None and p.distance_m <= NEAR_M:
            self.seen_at = now
            if self.near_since is None:
                self.near_since = now
        else:
            self.near_since = None

    def _arms_away(self):
        """The arms away from rest, [] when none is, or None when it is not known (not checked, or
        unreadable: then patrol goes on as before, and the runner's own checks still hold every show)."""
        if self.arms is None or self.rest is None:
            return None
        try:
            away = self.arms.away()
        except Exception as e:  # noqa: BLE001
            if str(e) != self._arms_err:
                self._arms_err = str(e)
                self._log("arms_error", error=str(e))
            return None
        self._arms_err = None
        return away

    def _off_the_way_home(self, away) -> list:
        """The arms of `away` that are not on the candy show's last move home (all of them when that
        move, or the arms' poses, are not known)."""
        try:
            poses = self.arms.poses() if hasattr(self.arms, "poses") else {}
        except Exception as e:  # noqa: BLE001
            self._log("arms_error", what="poses", error=str(e))
            return list(away)
        return [side for side in away
                if not (side in self.candy_home and side in poses and on_way_home(poses[side], self.candy_home[side]))]

    def _rest_arms(self, now, away) -> bool:
        """An arm is away from rest: stop the base and play the rest show (True), or pause when a
        person must clear the arm first (True), or nothing yet, a retry still to wait for (False)."""
        if self.rest_blocked:
            off = self._off_the_way_home(away)
            if off:
                self._enter("PAUSED", f"{', '.join(off)} away from rest: {self.rest_blocked}")
                return True
            self._log("rest_unblocked", away=away, why="on the candy show's own last move home")
            self.rest_blocked = None
        if now < self.rest_after:
            return False
        self.rest_after = now + REST_RETRY_S
        self._enter("REST", f"{', '.join(away)} away from rest")
        self.show, self.rest_before = self.rest, self._palette_last()
        try:
            ok, how = self.palette.run(self.rest, start="countdown")
        except Exception as e:  # noqa: BLE001
            ok, how = False, str(e)
        self._log("rest", routine=self.rest, away=away, ok=ok, why=how)
        if not ok:
            self._enter("IDLE_NAV", f"{self.rest} refused: {how}")
            self.rest_after = now + REST_RETRY_S
        return True

    def _palette_last(self):
        try:
            return self.palette.last() if hasattr(self.palette, "last") else None
        except Exception as e:  # noqa: BLE001
            self._log("palette_error", what="last", error=str(e))
            return None

    def _tick_idle_nav(self, now, running) -> None:
        away = self._arms_away()
        if away and self._rest_arms(now, away):
            return
        if self.near_since is not None and now - self.near_since >= SEEN_FOR_S:
            self._enter("APPROACH", f"a person {self.person.distance_m:.1f} m away")
            return self._tick_approach(now, running)
        if self.shorts and self.next_choreo is not None and now >= self.next_choreo:
            return self._start_choreo(pick_choreo(None, self.shorts, self.last_choreo, self.rng), "random pick")
        if self.nav_doing != "patrol":
            self._drive("patrol")

    def _tick_approach(self, now, running) -> None:
        if self.seen_at is None or now - self.seen_at > LOST_S:
            return self._enter("IDLE_NAV", "lost them")
        p = self.person
        if p is not None and p.distance_m <= STANDOFF_M + 0.15:
            return self._enter("ENGAGED", f"stopped {p.distance_m:.1f} m from them")
        if p is not None:
            self._drive("approach", p, STANDOFF_M)

    def _tick_engaged(self, now, running) -> None:
        last = self.seen_at if self.seen_at is not None else self.since
        if now - max(last, self.since) > ENGAGED_S:
            self._enter("IDLE_NAV", "nobody came")

    def _tick_show(self, now, running) -> None:
        if running == self.show:
            self.show_seen = True
        elif self.show_seen:                     # it ran and has ended (or another took its place)
            after = "ENGAGED" if self.state == "CANDY" else "IDLE_NAV"
            return self._enter(after, self._how_it_ended())
        elif now - self.since > SHOW_START_S:
            try:                                 # never leave a run behind that a later press could start
                self.palette.stop()
            except Exception as e:  # noqa: BLE001
                self._log("palette_error", what="stop", error=str(e))
                return                           # cannot be sure it is disarmed: the base stays put
            after = "ENGAGED" if self.state == "CANDY" else "IDLE_NAV"
            return self._enter(after, f"{self.show} never reached the palette within {SHOW_START_S:.0f} s")

    def _how_it_ended(self) -> str:
        """The palette's own word on the run that just ended: a refusal says why (the runner's code and
        cause), so the dashboard does not show a refused show as one that played."""
        last = None
        try:
            last = self.palette.last() if hasattr(self.palette, "last") else None
        except Exception as e:  # noqa: BLE001
            self._log("palette_error", what="last", error=str(e))
        if isinstance(last, dict) and last.get("id") == self.show and last.get("status") not in (None, "COMPLETED"):
            if self.show == self.candy and last.get("status") in ("ABORTED", "ESTOPPED"):
                self.rest_blocked = (f"the candy show stopped part-way ({last.get('code') or last.get('status')}); "
                                     "the hand may be in the bucket: clear it and park the arm, then resume")
            why = f"{self.show} {str(last.get('status')).lower()}: {last.get('code') or ''} {last.get('cause') or ''}".strip()
            self._log("show_failed", routine=self.show, status=last.get("status"), code=last.get("code"),
                      cause=last.get("cause"))
            return why[:300]
        return f"{self.show} ended"

    _tick_candy = _tick_show
    _tick_choreo = _tick_show
    def _tick_rest(self, now, running) -> None:
        if self.nav_doing != "patrol":           # the base patrols on while the arms come home
            self._drive("patrol")
        last = None if running == self.rest else self._palette_last()
        if (not self.show_seen and isinstance(last, dict) and last != self.rest_before
                and last.get("id") == self.rest and last.get("status") in ("REFUSED", "ABORTED", "ESTOPPED")):
            # refused at preflight (the deadman let go): out at once, so a visitor is not told "busy"
            self._enter("IDLE_NAV", f"{self.rest} {str(last.get('status')).lower()}: {(last.get('cause') or '')[:200]}")
        else:
            self._tick_show(now, running)
        if self.state != "REST":
            self.rest_after = now + REST_RETRY_S   # the next try waits from the end of this one

    def _start_choreo(self, routine, why: str):
        if routine is None:
            return False, "no show to play"
        if not self._base_still(self.still_wait_s):
            self._enter("IDLE_NAV", "the base did not come to rest")
            return False, "the base did not come to rest"
        self._enter("CHOREO", why)
        self.show = self.last_choreo = routine
        try:
            ok, how = self.palette.run(routine)
        except Exception as e:  # noqa: BLE001
            ok, how = False, str(e)
        self._log("choreo", routine=routine, ok=ok, why=how)
        if not ok:
            self._enter("IDLE_NAV", f"{routine} refused: {how}")
            return False, how
        return True, f"{routine} armed: the supervisor's deadman press starts it"

    # ---- the outside world's calls ----------------------------------------------------------------
    def request(self, want: str, text: str = ""):
        """The listener's: "candy" before it starts the candy show, "choreo" for a spoken request.
        (ok, why)."""
        with self.lock:
            if self.state == "PAUSED":
                return False, "paused"
            if want == "candy" and self.state == "CHOREO" and self._waiting_for_go():
                self._preempt()                      # nothing has moved yet: the visitor comes first
            if self.state in ("CANDY", "CHOREO", "REST"):
                return False, f"busy: {self.show or self.state}"
            readable, running = self._running()
            if not readable or running:
                return False, "a show is running" if running else "the show palette cannot be read"
            if want == "candy":
                if not self._base_still(self.still_wait_s):
                    return False, "the base did not come to rest"
                self._enter("CANDY", "trick or treat")
                self.show = self.candy
                return True, "granted: the base is still"
            if want == "choreo":
                pick = match_show(text, self.shorts) or pick_choreo(text, self.choreo, self.last_choreo, self.rng)
                return self._start_choreo(pick, f"asked: {text!r}")
            return False, f"unknown request {want!r}"

    def _waiting_for_go(self) -> bool:
        """This state's show is armed and waiting for the deadman's GO (or still being checked): nothing
        has moved. From COUNTDOWN on (the GO given) it has started, and is never cancelled for candy."""
        try:
            st = self.palette.status() if hasattr(self.palette, "status") else None
        except Exception as e:  # noqa: BLE001
            self._log("palette_error", what="status", error=str(e))
            return False
        return isinstance(st, dict) and st.get("running") == self.show and st.get("status") in NOT_STARTED

    def _preempt(self) -> None:
        """Cancel the armed show (the Shows page's stop) and wait, at most STILL_WAIT_S, for the palette
        to be free; the caller then grants the candy as from any other state."""
        show = self.show
        try:
            self.palette.stop()
        except Exception as e:  # noqa: BLE001
            self._log("palette_error", what="stop", error=str(e))
            return
        end = time.monotonic() + self.still_wait_s
        while time.monotonic() < end:
            readable, running = self._running()
            if readable and not running:
                self._enter("ENGAGED", f"{show} cancelled before it started: trick or treat")
                self._log("preempted", routine=show)
                return
            time.sleep(0.05)

    def pause(self, why: str = "the operator") -> None:
        with self.lock:
            if self.state in ("CANDY", "CHOREO", "REST"):
                try:
                    self.palette.stop()
                except Exception as e:  # noqa: BLE001
                    self._log("palette_error", what="stop", error=str(e))
            self._enter("PAUSED", why)

    def resume(self):
        with self.lock:
            if self.state != "PAUSED":
                return False, f"not paused ({self.state})"
            ok, why = self._safe()
            if not ok:
                return False, why
            self.rest_blocked = None             # the operator has looked: a rest show may bring the arms home
            self._enter("IDLE_NAV", "the operator resumed")
            return True, "resumed"

    def doc(self) -> dict:
        with self.lock:
            nxt = self.next_choreo
            return {"state": self.state, "since_s": round(self.clock() - self.since, 1), "why": self.why,
                    "person": self.person.doc() if self.person else None, "nav": self.nav_doing,
                    "nav_stub": isinstance(self.nav, StubNav),
                    "nav_state": self.nav.status() if hasattr(self.nav, "status") else None,
                    "show": self.show, "choreo": self.choreo, "random": self.shorts, "candy": self.candy,
                    "next_choreo_s": round(nxt - self.clock()) if nxt is not None and self.state == "IDLE_NAV" else None}


# ---- the drivers ------------------------------------------------------------------------------------
class StubNav:
    """The nav stack's door, until it is here: says what it would do, moves nothing, and is always
    still. A real one claims the base lease (/base/claim, a name of its own), patrols the area on
    patrol(), drives toward the person on approach() (slowly, stopping standoff_m short), and on
    stop() sends a zero Twist and releases the lease; still() is /odom's twist near zero."""

    def __init__(self, log=None):
        self._log_fn, self.doing = log, None

    def _say(self, what: str, **kw) -> None:
        if self.doing != what or kw:
            self.doing = what
            if self._log_fn:
                self._log_fn({"t": round(time.time(), 3), "event": "nav_stub", "would": what, **kw})

    def patrol(self) -> None:
        self._say("patrol")

    def approach(self, person: Person, standoff_m: float) -> None:
        self._say("approach", **person.doc(), standoff_m=standoff_m)

    def stop(self) -> None:
        self._say("stop")

    def still(self) -> bool:
        return True


class HoundNav:
    """The robot's nav stack (hound_nav's robot_bridge.py, in opendubs-gates-sim), behind the four calls.

    THE BASE IS TAKEN, NOT ASKED FOR (2026-10-29). The bridge renews its `nav` lease whenever it knows where it
    is, so nothing it can be asked stops it. stop() takes the base over instead, with the base selector's own
    rules (base_selector.py): the operator's release ({"owner": "*"}, which frees the base whoever holds it),
    then at once a claim as `demo_day`, renewed every RENEW_S. The selector never takes the base from a live
    holder, so while it is ours the nav's claims are refused and its commands go nowhere, and the handover
    sends one zero command first. If the nav wins the race, the takeover is tried again. patrol() releases
    it, the nav's next renewal takes it back, and its mission, still running all along, carries on.

    still() is true only when the base is OURS and /odom, fresh, says it is not moving: until the takeover has
    landed, no show and no candy is granted. The base is taken from the nav or from nobody, never from another
    owner (a teleop, a show's tracker): then still() stays false and the coordinator waits.

    /hound_nav/hold (std_msgs/Bool, transient local) is still published, held or not: the topic asked of the
    bridge (sidtalia/hound_nav#1), so that once it honours it the nav's planner stops too, not only its wheels.

    approach() holds the base for now: the bridge takes goals only from its page, so the robot waits where it
    is and a visitor is ENGAGED by walking up."""

    NAV = "nav"                  # the bridge's owner name
    OURS = "demo_day"
    TTL_S = 2.0                  # our claim's lease, renewed every RENEW_S
    RENEW_S = 0.5
    RETAKE_S = 1.0               # a takeover the nav won is tried again after this
    ODOM_STALE_S = 0.6           # /odom is ~8.7 Hz
    OWNER_STALE_S = 1.5          # /base/owner is 2 Hz, and on every change
    STILL_LIN = 0.02             # m/s
    STILL_ANG = 0.05             # rad/s
    RESEND_S = 1.0               # the hold is said again this often, so a restarted bridge hears it

    def __init__(self, log=None, publish=None, claim=None, release=None, clock=time.monotonic):
        """`publish(bool)` sends the hold, `claim(dict)` and `release(dict)` the selector's messages; None: ROS
        (start())."""
        self._log_fn, self.publish, self.claim, self.release, self.clock = log, publish, claim, release, clock
        self.lock = threading.Lock()
        self.hold, self.doing = True, None       # held until told to patrol: a coordinator starting never drives
        self.owner, self.owner_at = None, None
        self.twist, self.odom_at = None, None
        self.sent_at, self.renewed_at, self.took_at = None, None, None
        self.paused = False                      # paused: taken from the nav only, a free base left free

    def _log(self, event: str, **kw) -> None:
        if self._log_fn:
            self._log_fn({"t": round(time.time(), 3), "event": event, **kw})

    # ---- what the robot says
    def on_owner(self, data: str) -> None:
        try:
            owner = (json.loads(data) or {}).get("owner")
        except ValueError:
            return
        with self.lock:
            was, self.owner, self.owner_at = self.owner, owner, self.clock()
        if owner != was:
            self._log("base_owner", owner=owner, was=was)

    def on_odom(self, vx: float, vy: float, wz: float) -> None:
        with self.lock:
            self.twist, self.odom_at = (vx, vy, wz), self.clock()

    # ---- the base
    def _take(self) -> None:
        """Ours if it is the nav's or nobody's: the operator's release, then our claim."""
        with self.lock:
            if self.owner_at is None:
                return                           # who holds it not heard yet: a claim now could be refused
            owner, self.took_at = self.owner, self.clock()
        if owner not in (self.NAV, None, self.OURS):
            return                               # someone else's (a teleop, a show): not taken from them
        if owner is None and self.paused:
            return                               # paused: a free base stays free (teleop, the Shows page)
        if owner == self.NAV:
            self.release({"owner": "*"})
            time.sleep(0.05)                     # the release first: the two are on different topics
        self.claim({"owner": self.OURS, "ttl_s": self.TTL_S})
        with self.lock:
            self.renewed_at = self.clock()
        self._log("base_take", from_owner=owner)

    def _give(self) -> None:
        with self.lock:
            ours, self.renewed_at = self.owner == self.OURS, None
        self.release({"owner": self.OURS})
        if ours:
            self._log("base_give")

    def _set(self, hold: bool, what: str, **kw) -> None:
        with self.lock:
            changed = hold != self.hold or self.sent_at is None
            self.hold = hold
        if changed:
            self._send()
            self._take() if hold else self._give()
        if self.doing != what:
            self.doing = what
            self._log("nav", did=what, hold=hold, **kw)

    def _send(self) -> None:
        with self.lock:
            hold, self.sent_at = self.hold, self.clock()
        self.publish(hold)

    def tick(self) -> None:
        """The ROS thread's timer: renew our claim while held, take the base back if the nav won it, say the hold."""
        now = self.clock()
        with self.lock:
            hold, owner, renewed, took = self.hold, self.owner, self.renewed_at, self.took_at
        if hold:
            if owner == self.OURS:               # ours: renewed (only a claim we have; a refused one is no lease)
                if renewed is None or now - renewed >= self.RENEW_S:
                    self.claim({"owner": self.OURS, "ttl_s": self.TTL_S})
                    with self.lock:
                        self.renewed_at = now
            elif took is not None and now - took < self.RETAKE_S:
                pass                             # a take under way: its answer comes on /base/owner
            elif self.paused and owner != self.NAV:
                pass                             # paused: only taken from the nav
            else:
                self._take()
        if self.sent_at is None or now - self.sent_at >= self.RESEND_S:
            self._send()

    # ---- the four calls, and the pause
    def pause(self) -> None:
        """The coordinator paused: the nav still may not drive (taken from it whenever it claims), but a free
        base is left free, and ours is given up."""
        self.paused = True
        self._set(True, "paused")
        with self.lock:
            ours = self.owner == self.OURS
        if ours:
            self._give()

    def patrol(self) -> None:
        self.paused = False
        self._set(False, "patrol")

    def approach(self, person: Person, standoff_m: float) -> None:
        self._set(True, "wait for them", **person.doc(), standoff_m=standoff_m)

    def stop(self) -> None:
        if self.paused:
            return                               # pause() has said how; a stop while paused changes nothing
        self._set(True, "stop")

    def still(self) -> bool:
        now = self.clock()
        with self.lock:
            if not self.hold:
                return False
            if self.owner_at is None or now - self.owner_at > self.OWNER_STALE_S or self.owner != self.OURS:
                return False                     # not ours (yet): the nav, or anyone, may still drive it
            if self.odom_at is None or now - self.odom_at > self.ODOM_STALE_S:
                return False
            vx, vy, wz = self.twist
        return math.hypot(vx, vy) < self.STILL_LIN and abs(wz) < self.STILL_ANG

    def status(self) -> dict:
        with self.lock:
            return {"hold": self.hold, "owner": self.owner, "moving": None if self.twist is None else
                    [round(v, 3) for v in self.twist]}

    # ---- ROS
    def start(self, hold_topic: str = "/hound_nav/hold") -> "HoundNav":
        import rclpy
        from nav_msgs.msg import Odometry
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from std_msgs.msg import Bool, String
        ctx = rclpy.Context()
        rclpy.init(context=ctx)
        node = rclpy.create_node("architect_demo_day_nav", context=ctx)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                             reliability=ReliabilityPolicy.RELIABLE)
        pub = node.create_publisher(Bool, hold_topic, latched)
        claim_pub = node.create_publisher(String, "/base/claim", 10)
        release_pub = node.create_publisher(String, "/base/release", 10)
        self.publish = lambda hold: pub.publish(Bool(data=bool(hold)))
        self.claim = lambda d: claim_pub.publish(String(data=json.dumps(d)))
        self.release = lambda d: release_pub.publish(String(data=json.dumps(d)))
        node.create_subscription(String, "/base/owner", lambda m: self.on_owner(m.data), latched)

        def odom(m):
            t = m.twist.twist
            self.on_odom(t.linear.x, t.linear.y, t.angular.z)
        node.create_subscription(Odometry, "/odom", odom, qos_profile_sensor_data)
        node.create_timer(0.1, self.tick)
        ex = SingleThreadedExecutor(context=ctx)
        ex.add_node(node)
        threading.Thread(target=ex.spin, daemon=True).start()
        time.sleep(1.0)                          # the latched /base/owner first, then take what the hold says
        self._send()
        self._take() if self.hold else self._give()
        return self


class PerceptionPeople:
    """The nearest person from the perception services (/perception/locate "person", the best match,
    turned into the chassis frame by perception.py, at the head's live angles). Asked on a thread of
    its own every EVERY_S, so a slow or absent service never holds up a tick: nearest() returns the
    latest answer, and raises when it is an error or older than STALE_S (unseen, not nobody).
    Not found is nobody."""

    EVERY_S = 0.5
    STALE_S = 8.0                # longer than the client's own wait for the service (5 s)

    def __init__(self, client=None, start=True):
        self.client, self.latest = client, None    # (monotonic time, Person, None or an error)
        if start:
            threading.Thread(target=self._loop, daemon=True).start()

    def ask(self):
        if self.client is None:
            from opendubs_architect.client import OpenDubsRobotClient
            self.client = OpenDubsRobotClient("architect_demo_day")
        try:
            p = self.client.locate_object("person")["position"]
            self.latest = (time.monotonic(), person_from_chassis((p["x"], p["y"], p["z"])))
        except RuntimeError as e:
            self.latest = (time.monotonic(), None if "nothing like that in view" in str(e) else e)

    def _loop(self):
        while True:
            try:
                self.ask()
            except Exception as e:  # noqa: BLE001  reported through nearest(), never fatal
                self.latest = (time.monotonic(), e)
            time.sleep(self.EVERY_S)

    def nearest(self):
        if self.latest is None or time.monotonic() - self.latest[0] > self.STALE_S:
            raise RuntimeError(f"no answer from the perception services within {self.STALE_S:.0f} s")
        if isinstance(self.latest[1], Exception):
            raise self.latest[1]
        return self.latest[1]


class Palette:
    """The dashboard's show palette, the Shows page's own calls (choreo_proxy.py)."""

    def __init__(self, dashboard: str, start: str = "deadman"):
        from opendubs_architect.listener import Shows
        self.http, self.start = Shows(dashboard, routine="").call, start

    def running(self):
        code, st = self.http("GET", "/api/choreo/palette", None)
        if code != 200 or not isinstance(st, dict):
            raise RuntimeError(f"the show palette answered {code}")
        return st.get("running")

    def status(self):
        code, st = self.http("GET", "/api/choreo/palette", None)
        return st if code == 200 and isinstance(st, dict) else None

    def last(self):
        code, st = self.http("GET", "/api/choreo/palette", None)
        return st.get("last") if code == 200 and isinstance(st, dict) else None

    def run(self, routine: str, start: str | None = None):
        code, got = self.http("POST", f"/api/choreo/run/{routine}", {"start": start or self.start})
        if code == 200:
            return True, "armed"
        return False, (got or {}).get("error") if isinstance(got, dict) else f"status {code}"

    def stop(self) -> None:
        self.http("POST", "/api/choreo/stop", None)

    def routines(self) -> dict:
        code, rs = self.http("GET", "/api/choreo/routines", None)
        if code != 200 or not isinstance(rs, list):
            raise SystemExit(f"demo_day: the show palette cannot be read ({code})")
        return {r.get("id"): r for r in rs}


class CandyRunner:
    """The palette, with the candy run carried by the ARCHITECT demo runner (demo_runner.py, the gafro
    backend) instead of a show: while the runner is RUNNING the candy routine is what runs; anything
    else (the choreo shows) is the dashboard palette's. stop() cancels a candy run and keeps its program
    armed (POST /api/cancel); last() is the runner's last outcome, in the palette's words."""

    def __init__(self, palette, runner_url: str, candy: str):
        self.palette, self.url, self.candy = palette, runner_url.rstrip("/"), candy

    def _call(self, method: str, path: str):
        req = urllib.request.Request(self.url + path, data=b"{}" if method == "POST" else None, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as r:
            return json.load(r)

    def _runner(self) -> dict:
        try:
            return self._call("GET", "/api/state")
        except (OSError, ValueError) as e:
            raise RuntimeError(f"the ARCHITECT runner does not answer ({e})") from None

    def running(self):
        if self._runner().get("state") == "RUNNING":
            return self.candy
        return self.palette.running()

    def status(self):
        if self._runner().get("state") == "RUNNING":
            return {"running": self.candy, "status": "RUNNING"}
        return self.palette.status()

    def last(self):
        last = self._runner().get("last") or {}
        if not last:
            return self.palette.last()
        status = {"done": "COMPLETED", "stopped": "ABORTED", "refused": "REFUSED"}.get(last.get("result"), "ABORTED")
        if "the run was stopped" in (last.get("error") or ""):   # cancelled (stop()), not refused
            status = "ABORTED"
        return {"id": self.candy, "status": status, "code": (last.get("result") or "").upper(),
                "cause": (last.get("error") or "")[:200]}

    def run(self, routine: str, start: str | None = None):
        return self.palette.run(routine, start)

    def stop(self) -> None:
        if self._runner().get("state") == "RUNNING":
            self._call("POST", "/api/cancel")
        else:
            self.palette.stop()

    def routines(self) -> dict:
        return self.palette.routines()


class RosArms:
    """The arms' measured pose from /joint_states (the arm stack's, in radians), on a ROS context of its
    own. away() names each arm with a joint more than REST_TOL_DEG from rest (0: the shows' rest, in
    the controller's signed sense), and raises when /joint_states is stale or lacks an arm."""

    STALE_S = 1.0
    SIDES = {"ARM_L": "left", "ARM_R": "right"}

    def __init__(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from sensor_msgs.msg import JointState
        self.lock, self.pos, self.at = threading.Lock(), {}, 0.0
        ctx = rclpy.Context()
        rclpy.init(context=ctx)
        node = rclpy.create_node("architect_demo_day_arms", context=ctx)
        node.create_subscription(JointState, "/joint_states", self._on, 20)
        ex = SingleThreadedExecutor(context=ctx)
        ex.add_node(node)
        threading.Thread(target=ex.spin, daemon=True).start()

    def _on(self, m) -> None:
        with self.lock:
            self.pos.update(zip(m.name, m.position))
            self.at = time.monotonic()

    def _fresh(self) -> dict:
        with self.lock:
            if time.monotonic() - self.at > self.STALE_S:
                raise RuntimeError(f"nothing on /joint_states within {self.STALE_S:.0f} s")
            return dict(self.pos)

    def away(self) -> list:
        return arms_away(self._fresh(), self.SIDES)

    def poses(self) -> dict:
        pos = self._fresh()
        out = {}
        for side in self.SIDES.values():
            names = [f"openarm_{side}_joint{j}" for j in range(1, 8)]
            if all(n in pos for n in names):
                out[side] = [math.degrees(pos[n]) for n in names]
        return out


def on_way_home(pose, last, tol_deg: float = HOME_TOL_DEG) -> bool:
    """Is `pose` (7 signed degrees) on the straight joint-space move from `last` to rest (0), within
    tol_deg at every joint? The runner moves to a `pose: rest` cue joint by joint in proportion, so a
    run stopped on that move stopped on this line, and resting from there retraces the show's own move."""
    if not any(last):
        return False
    s = sum(p * q for p, q in zip(pose, last)) / sum(q * q for q in last)
    s = min(1.0, max(0.0, s))
    return all(abs(p - s * q) <= tol_deg for p, q in zip(pose, last))


def candy_home(path: str) -> dict:
    """{side: the arm's last `joints` cue before its final `pose: rest`} from a show file; {} when it
    cannot be read. Only an arm whose show ends with that rest has a way home."""
    import yaml
    try:
        with open(path, encoding="utf-8") as f:
            cues = (yaml.safe_load(f) or {}).get("cues") or []
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    out = {}
    for slot, side in RosArms.SIDES.items():
        mine = sorted((c for c in cues if isinstance(c, dict) and c.get("slot") == slot), key=lambda c: c.get("t", 0))
        if len(mine) >= 2 and mine[-1].get("pose") == "rest" and isinstance(mine[-2].get("joints"), dict):
            j = mine[-2]["joints"]
            out[side] = [float(j.get(k, j.get(str(k), 0.0))) for k in range(1, 8)]
    return out


def arms_away(pos: dict, sides: dict, tol_deg: float = REST_TOL_DEG) -> list:
    """The arms (of `sides`, slot -> side) with a joint more than tol_deg from rest; raises for an arm
    whose seven joints are not all in `pos` (joint name -> radians)."""
    out = []
    for slot, side in sides.items():
        names = [f"openarm_{side}_joint{j}" for j in range(1, 8)]
        if not all(n in pos for n in names):
            raise RuntimeError(f"/joint_states has no {side} arm")
        if any(abs(math.degrees(pos[n])) > tol_deg for n in names):
            out.append(side)
    return out


class Safety:
    """The e-stop, from the dashboard's StamPLC state: estopped, or the state stale, is not ok."""

    def __init__(self, dashboard: str):
        self.url = dashboard.rstrip("/") + "/api/stamplc"

    def ok(self):
        with urllib.request.urlopen(self.url, timeout=1.0) as r:
            s = (json.load(r) or {}).get("safety") or {}
        if s.get("estopped"):
            return False, "e-stop: " + (s.get("source") or "pressed")
        if s.get("stale") or not s.get("fresh", False):
            return False, "the robot's safety state is stale"
        return True, ""


class FakePalette:
    def __init__(self):
        self.now = None

    def status(self):
        return {"running": self.now, "status": "ARMED_WAITING" if self.now else None}

    def running(self):
        return self.now

    def run(self, routine, start=None):
        self.now = routine
        return True, "armed (fake)"

    def stop(self):
        self.now = None


class Nobody:
    def nearest(self):
        return None


class AlwaysSafe:
    def ok(self):
        return True, ""


# ---- the process ------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dashboard", default="http://127.0.0.1:8080")
    ap.add_argument("--candy", default="architect-bb75cd239645", help="the candy show (the listener's --show)")
    ap.add_argument("--choreo", nargs="+", default=["monster_mash", "ghostbusters", "them_bones"],
                    help="the full shows, played when asked for (\"OpenDubs, dance\", or by name)")
    ap.add_argument("--random", nargs="+", default=["wave-and-nod", "robotic-smile", "celebrate"],
                    help="the short shows the random pick chooses from (also playable by name)")
    ap.add_argument("--every", type=float, nargs=2, default=list(CHOREO_EVERY_S), metavar=("MIN_S", "MAX_S"),
                    help="a random choreo pick after this long patrolling")
    ap.add_argument("--people", choices=("perception", "fake"), default="perception")
    ap.add_argument("--nav", choices=("stub", "hound"), default="stub",
                    help="stub (moves nothing) or hound: the robot's nav stack, told to hold and carry on over "
                         "/hound_nav/hold (HoundNav: the bridge must honour it, or nothing is ever granted)")
    ap.add_argument("--palette", choices=("dashboard", "fake"), default="dashboard")
    ap.add_argument("--candy-runner", metavar="URL",
                    help="the candy run is the ARCHITECT runner's program (e.g. http://127.0.0.1:8098, the gafro "
                         "backend), not a show: --candy is then only its name")
    ap.add_argument("--rest", default="architect-78d41e435106", metavar="ROUTINE",
                    help="the show that brings both arms to rest, played when one is away from rest while "
                         "patrolling (examples/arms_rest.py); '' to leave the arms alone")
    ap.add_argument("--shows", default="/var/lib/opendubs/shows", metavar="DIR",
                    help="the robot's shows folder, read-only: the candy show's last move home (candy_home)")
    ap.add_argument("--start-paused", action="store_true",
                    help="start PAUSED (the boot unit's way): nothing is armed until POST /api/resume")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8096)
    args = ap.parse_args(argv)

    def log(ev):
        print(json.dumps(ev), flush=True)

    if args.palette == "fake":
        palette, safety = FakePalette(), AlwaysSafe()
    else:
        palette, safety = Palette(args.dashboard), Safety(args.dashboard)
        have = palette.routines()
        shows = [*args.choreo, *args.random] if args.candy_runner else [args.candy, *args.choreo, *args.random]
        shows += [args.rest] if args.rest else []
        if args.candy_runner:
            palette = CandyRunner(palette, args.candy_runner, args.candy)
        for r in shows:
            if r not in have:
                raise SystemExit(f"demo_day: no routine {r!r}; the robot has {', '.join(sorted(have))}")
            if not have[r].get("valid"):
                raise SystemExit(f"demo_day: {r!r} does not validate: {'; '.join(have[r].get('problems') or [])}")
    people = PerceptionPeople() if args.people == "perception" else Nobody()
    arms = RosArms() if args.rest and args.palette != "fake" else None
    nav = HoundNav(log).start() if args.nav == "hound" else StubNav(log)
    co = Coordinator(nav, people, palette, safety, args.candy, args.choreo, log=log,
                     choreo_every=tuple(args.every), shorts=args.random, arms=arms, rest=args.rest or None,
                     candy_home=candy_home(os.path.join(args.shows, args.candy + ".yaml")))
    if args.start_paused:
        co.pause("started paused: POST /api/resume to begin the demo day")

    def loop():
        while True:
            try:
                co.tick()
            except Exception as e:  # noqa: BLE001  one bad tick never stops the coordinator
                log({"t": round(time.time(), 3), "event": "tick_failed", "error": str(e)})
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/state":
                return self._send(200, co.doc())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "not JSON"})
            if self.path == "/api/request":
                ok, why = co.request(str(body.get("want", "")), str(body.get("text", "")))
                return self._send(200, {"ok": ok, "why": why, "state": co.state})
            if self.path == "/api/pause":
                co.pause(str(body.get("why") or "the operator"))
                return self._send(200, co.doc())
            if self.path == "/api/resume":
                ok, why = co.resume()
                return self._send(200 if ok else 409, {"ok": ok, "why": why, **co.doc()})
            self._send(404, {"error": "not found"})

    log({"t": round(time.time(), 3), "event": "listening", "port": args.port, "candy": args.candy,
         "choreo": args.choreo, "nav": args.nav, "people": args.people})
    ThreadingHTTPServer((args.host, args.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
