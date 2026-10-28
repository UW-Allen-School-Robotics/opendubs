"""Trick or treat, the decisions: where the head looks, when a hand is offered, where the right arm goes. Pure: no
ROS, no sockets, no clock of its own (every call takes `now`), so tests/test_treat_brain.py drives it with made-up
frames. treat_follow.py does the input and output.

  HeadLaw     the neck: search for a person, centre their torso (the shoulders' midpoint, never a hand), settle,
              then stay put and correct only a torso that has clearly and lastingly moved
  Offer       per hand, per frame: is it lifted toward the robot (asking), with hysteresis so it does not flicker
  PalmFilter  the followed palm: alpha-beta (position and velocity), a jump gate, a small lead, a bounded coast
  reach_goal  where the arm's hand goes for a palm: sideways and up/down 1:1, only the depth cut to the arm's reach
  Smoother    the commanded point: a critically damped spring, speed and acceleration limited
  Brain       the arm: wait -> home <-> follow, with hold for everything uncertain

FRAMES: every point is in arm_mount (x forward toward the person, y left, z up, metres). HeadLaw's inputs are the
shoulders' midpoint as degrees off the camera's axis (below +, right +, the tracker's hand/state).

SIDES are the person's own hands (hand_tracker/palm.py). Facing the robot, the person's LEFT is the robot's right (-y).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SIDES = ("left", "right")
SHOULDER_WIDTH = 0.36         # m, an adult's; only to place a shoulder the camera does not see from the one it does


# -- the head -----------------------------------------------------------------------------------------------------

@dataclass
class HeadParams:
    tilt_min: float = 71.0      # deg, the neck's own (71: this robot's fully down)
    tilt_max: float = 108.0
    pan: bool = False           # pan as well as tilt (off: pan held where it is)
    pan_range: float = 30.0     # deg either side of 90
    pan_sign: float = -1.0      # pan = pan + pan_sign * gain * right_deg: the dashboard's PAN_SIGN (pan falls to the right)
    gain: float = 0.4           # of the torso's offset, per step
    max_step: float = 6.0       # deg per step at most
    start_deg: float = 5.0      # a settled head moves when the torso is this far off...
    lock_deg: float = 10.0      # ...or this far while the arm follows (the person reaching moves the shoulders a little)
    stop_deg: float = 2.0       # a moving head stops within this
    persist_s: float = 0.4      # a settled head waits this long with the torso off before it moves (a lean, not a twitch)
    wait_s: float = 0.5         # s after a move before a frame counts (the tracker's frames lag; earlier ones are blurred)
    settle_n: int = 4           # centred frames in a row before the head is settled
    search_after_s: float = 2.0  # s with nobody seen before the head sweeps
    search_dps: float = 8.0
    period: float = 0.1


class HeadLaw:
    """Step-and-wait: aim at where the torso is seen now, wait for a frame taken after the move, repeat. Nothing
    integrates, so a stale or wrong frame costs one step, never a runaway.

    Modes: off, centre (moving onto the person), settled (still; `acquired` from the first settle until the person is
    lost), recentre (acquired, following a torso that moved), search (nobody for search_after_s: sweep the tilt, and the pan with --head-pan), freeze (nobody seen but the
    arm is out: the head keeps still so the held arm's frame does not move under it)."""

    def __init__(self, p: HeadParams):
        self.p = p
        self.mode, self.why, self.acquired = "off", "not started", False
        self.pan = self.tilt = None
        self.moving, self.count, self.off_since = True, 0, None
        self.moved_at, self.seen_at, self.sweep_dir, self.pan_slot = -1e9, 0.0, 1.0, 0

    def start(self, now, pan, tilt):
        self.pan, self.tilt = pan, tilt
        self.mode, self.acquired, self.moving, self.count, self.off_since = "centre", False, True, 0, None
        self.seen_at = now

    def stop(self):
        self.mode, self.acquired = "off", False

    def reacquire(self, why):
        """A different person, or one lost: centre afresh, and the arm waits for it."""
        self.acquired, self.moving, self.count, self.off_since = False, True, 0, None
        self.why = why

    def quiet_since(self):
        """When the head last moved: frames taken before this + wait_s saw the world from a moving head."""
        return self.moved_at

    def step(self, now, mid, mid_at, hold_still=False):
        """(pan, tilt) to send now, or None. `mid` {below_deg, right_deg} of a frame received at `mid_at`, or None.
        hold_still: the arm is out with nobody seen; the head must not search."""
        p = self.p
        if self.mode == "off" or self.tilt is None:
            return None
        fresh = mid is not None and now - mid_at <= 0.5 and mid_at - self.moved_at >= p.wait_s
        if mid is not None and now - mid_at <= 0.5:
            self.seen_at = now
        if fresh:
            e_t = float(mid["below_deg"])
            e_p = float(mid.get("right_deg") or 0.0) if p.pan else 0.0
            err = math.hypot(e_t, e_p)
            if self.mode == "search":
                self.mode, self.moving, self.count = "centre", True, 0
            if self.moving:
                self.moving = err > p.stop_deg
            else:
                limit = p.lock_deg if hold_still else p.start_deg
                self.off_since = (self.off_since or now) if err > limit else None
                self.moving = self.off_since is not None and now - self.off_since >= p.persist_s
            if not self.moving:
                self.off_since = None
                self.count += 1
                if self.count >= p.settle_n:
                    self.mode, self.acquired = "settled", True
                self.why = f"torso {e_t:+.1f} deg below, {e_p:+.1f} right; tilt {self.tilt:.1f} pan {self.pan:.1f}"
                return None
            self.count = 0
            self.mode = "centre" if not self.acquired else "recentre"
            d_t = max(-p.max_step, min(p.max_step, p.gain * e_t))
            d_p = max(-p.max_step, min(p.max_step, p.gain * e_p)) if p.pan else 0.0
            tilt = max(p.tilt_min, min(p.tilt_max, self.tilt - d_t))           # below -> tilt down (smaller)
            pan = max(90 - p.pan_range, min(90 + p.pan_range, self.pan + p.pan_sign * d_p))
            self.why = f"centring: torso {e_t:+.1f} deg below, {e_p:+.1f} right"
            if abs(tilt - self.tilt) < 0.2 and abs(pan - self.pan) < 0.2:      # at a limit: nothing to send
                self.moving = False
                return None
            self.moved_at = now
            return pan, tilt
        if now - self.seen_at > p.search_after_s:
            if hold_still:
                self.mode, self.why = "freeze", "nobody seen, the arm is out: the head keeps still"
                return None
            # SEARCH: sweep the tilt between its limits; with pan, step the pan at each end of the sweep
            self.mode, self.acquired, self.moving, self.count = "search", False, True, 0
            tilt = self.tilt + self.sweep_dir * p.search_dps * p.period
            pan = self.pan
            if not p.tilt_min <= tilt <= p.tilt_max:
                self.sweep_dir = -self.sweep_dir
                tilt = max(p.tilt_min, min(p.tilt_max, tilt))
                if p.pan:
                    self.pan_slot = (self.pan_slot + 1) % 3
                    pan = 90 + (0, 0.66, -0.66)[self.pan_slot] * p.pan_range
            self.why = f"nobody seen: searching, tilt {tilt:.1f} pan {pan:.1f}"
            self.moved_at = now
            return pan, tilt
        return None

    def sent(self, pan, tilt):
        """What the neck says it will run (clamped by its own limits)."""
        self.pan, self.tilt = pan, tilt


# -- is the hand offered ------------------------------------------------------------------------------------------

@dataclass
class OfferParams:
    fwd_in: float = 0.15        # m the palm is nearer the robot than its shoulder: lifted toward it
    fwd_out: float = 0.08       # ...and stays lifted until it is less than this
    low_in: float = -0.50       # m: a forward palm counts only above this, relative to its shoulder (not a hand on a knee)
    low_out: float = -0.58
    up_in: float = -0.15        # m: a palm this high (near shoulder height or over) counts however far forward
    up_out: float = -0.25
    max_from_shoulder: float = 0.9   # m: farther from the shoulder is someone else's hand


class Offer:
    """A hand hanging at the side, in a pocket or on the hips is ~0.45-0.6 m below its shoulder and level with the body:
    not offered. A hand held out (a treat bag, an open palm) is ahead of the body toward the robot, or raised: offered.
    Separate thresholds in and out, so a hand at the edge does not flicker."""

    def __init__(self, p: OfferParams):
        self.p, self.lifted = p, {s: False for s in SIDES}

    def check(self, side, palm, shoulder):
        """(lifted, fwd, up). fwd: m nearer the robot than the shoulder; up: m above it."""
        p = self.p
        if float(np.linalg.norm(palm - shoulder)) > p.max_from_shoulder:
            self.lifted[side] = False
            return False, None, None
        fwd, up = float(shoulder[0] - palm[0]), float(palm[2] - shoulder[2])
        if self.lifted[side]:
            ok = (fwd >= p.fwd_out and up >= p.low_out) or up >= p.up_out
        else:
            ok = (fwd >= p.fwd_in and up >= p.low_in) or up >= p.up_in
        self.lifted[side] = ok
        return ok, fwd, up

    def reset(self):
        self.lifted = {s: False for s in SIDES}


# -- the palm -----------------------------------------------------------------------------------------------------

class PalmFilter:
    """Alpha-beta on the palm (12-15 frames a second, a centimetre or two of noise, ~0.1 s late): position and velocity.
    estimate() leads by `lead_s` to make up some of the lag, and extrapolates at most `coast_s` past the last frame, so a
    lost palm leaves the arm where the hand was heading, never running on.

    A palm more than `gate_m` from where it was expected is held back one frame: a stray landmark on the background is
    dropped; a second one near it is the hand really there (a fast move), and the filter jumps to it."""

    def __init__(self, alpha=0.65, beta=0.3, gate_m=0.30, lead_s=0.08, coast_s=0.20, vmax=3.0):
        self.alpha, self.beta, self.gate, self.lead, self.coast, self.vmax = alpha, beta, gate_m, lead_s, coast_s, vmax
        self.x = self.v = None
        self.t = None
        self.pending = None

    def reset(self, z=None, t=None):
        self.x = None if z is None else np.asarray(z, float).copy()
        self.v = None if z is None else np.zeros(3)
        self.t, self.pending = t, None

    def update(self, z, t):
        """True if the palm was taken."""
        z = np.asarray(z, float)
        if self.x is None:
            self.reset(z, t)
            return True
        dt = max(1e-3, t - self.t)
        xp = self.x + self.v * min(dt, self.coast)
        r = z - xp
        if float(np.linalg.norm(r)) > self.gate:
            if self.pending is not None and float(np.linalg.norm(z - self.pending)) <= self.gate:
                self.reset(z, t)           # two in a row agree: the hand is there
                return True
            self.pending = z
            return False
        self.pending = None
        self.x = xp + self.alpha * r
        self.v = self.v + (self.beta / dt) * r
        n = float(np.linalg.norm(self.v))
        if n > self.vmax:
            self.v *= self.vmax / n
        self.t = t
        return True

    def estimate(self, now):
        if self.x is None:
            return None
        ahead = min(max(0.0, now - self.t) + self.lead, self.coast)
        return self.x + self.v * ahead


def reach_goal(palm, shoulder, r_min=0.25, r_max=0.57, standoff=0.10, min_fwd=0.30, max_up=0.35, box=None):
    """The TCP goal for a palm (arm_mount). Left/right and up/down follow the palm 1:1 while the arm can reach them
    (beyond, scaled onto the reach); only the depth is cut: toward the palm as far as the arm reaches, `standoff` short of
    it, never nearer the robot than `min_fwd` ahead of the shoulder, nor over `max_up` above it. A person 1.2 m away moving their hand 30 cm
    sideways moves the arm's hand 30 cm sideways, which reaching TOWARD the palm along a line from the shoulder does
    not (it compresses that to a few centimetres).

    Why the limits: r_max 0.57 keeps the elbow bent ~50 deg or more (0.62 is nearly straight: singular), and min_fwd and
    max_up keep the hand out of the space straight above the shoulder. In the simulation the arm service's controller
    wedged there, the hand 30-70 cm from every later target, and its guard stopped it for joint speed (opendubs2,
    2026-10-28)."""
    d = np.asarray(palm, float) - shoulder
    d[0] -= standoff
    d[2] = min(d[2], max_up)
    lat = d[1:].copy()
    nl = float(np.linalg.norm(lat))
    lat_max = math.sqrt(max(r_max ** 2 - min_fwd ** 2, 0.0))
    if nl > lat_max:
        lat *= lat_max / nl
        nl = lat_max
    x_max = math.sqrt(max(r_max ** 2 - nl ** 2, 0.0))
    dx = min(max(d[0], min_fwd), x_max)
    g = np.array([dx, lat[0], lat[1]])
    n = float(np.linalg.norm(g))
    if n < r_min:                                   # too close to the shoulder: out along x
        g[0] = math.sqrt(max(r_min ** 2 - nl ** 2, g[0] ** 2))
    goal = shoulder + g
    if box is not None:
        goal = np.array([min(max(goal[i], box[i][0]), box[i][1]) for i in range(3)])
    return goal


class Smoother:
    """The point actually commanded: a critically damped spring toward the goal (time constant `tau`), its speed and
    acceleration limited. A stepped or jumping goal becomes a smooth path: the arm service's vibration guard stops a
    follow on stepped targets."""

    def __init__(self, tau=0.07, vmax=0.5, amax=3.0):
        self.w, self.vmax, self.amax = 1.0 / tau, vmax, amax
        self.p = self.v = None

    def reset(self, p):
        self.p, self.v = np.asarray(p, float).copy(), np.zeros(3)

    def step(self, goal, dt):
        w = self.w
        acc = w * w * (np.asarray(goal, float) - self.p) - 2.0 * w * self.v
        na = float(np.linalg.norm(acc))
        if na > self.amax:
            acc *= self.amax / na
        self.v = self.v + acc * dt
        nv = float(np.linalg.norm(self.v))
        if nv > self.vmax:
            self.v *= self.vmax / nv
        self.p = self.p + self.v * dt
        return self.p.copy()

    def pin(self, reachable):
        """The arm cannot go where the spring is: keep the spring there (no windup) and halve its speed."""
        self.p, self.v = np.asarray(reachable, float).copy(), self.v * 0.5


# -- the arm ------------------------------------------------------------------------------------------------------

@dataclass
class Frame:
    """One tracker frame, in arm_mount. person: a pose was seen. palms: followable palms only."""
    t: float
    person: bool
    shoulders: dict = field(default_factory=dict)
    palms: dict = field(default_factory=dict)
    trusted: bool = True        # taken from a still head with a good transform; False: positions not to be used


@dataclass
class BrainParams:
    offer_s: float = 0.12       # s a hand is lifted before the arm goes to it (two frames)
    away_s: float = 0.45        # s a seen hand is down before the arm goes home
    palm_grace_s: float = 0.5   # s a lost palm is held (fast moves and occlusion lose it for a few frames)
    person_grace_s: float = 0.6  # s the person may be unseen and still count as there
    lost_home_s: float = 3.0    # s with nobody seen before the arm goes home from wherever it holds
    stale_s: float = 0.25       # s a palm counts as current
    shoulder_keep_s: float = 1.0  # s a shoulder seen counts, standing in when the camera loses it
    switch_s: float = 0.4       # s the followed hand is gone/down before the other lifted hand takes over
    new_person_m: float = 0.6   # m the torso jumps between frames: someone else...
    new_person_n: int = 3       # ...in this many frames in a row that agree (one noisy frame, or a side-on person's
                                # shoulders swapping, is not someone else: seen on opendubs2, 2026-10-28)


class Brain:
    """WAIT (the head has not settled on anyone yet: the arm holds where it is) -> HOME (watching: the arm in its neutral
    pose) <-> FOLLOW (a hand offered: the arm's hand goes to it, live), and HOLD where nothing can be trusted.

      FOLLOW  palm current and lifted                 -> follow the filtered palm
              palm lost < palm_grace_s                -> follow the filter's coast (bounded): a fast move or occlusion
              palm seen but down for away_s           -> HOME
              palm lost longer, the person still seen -> HOME (a dropped hand is usually lost by the landmarker)
              nobody seen                             -> HOLD; after lost_home_s HOME
      HOME    a hand lifted for offer_s               -> FOLLOW (from where the arm is: no jump)
    tick() says what to do now: ("hold", None) | ("home", None) | ("follow", goal palm)."""

    def __init__(self, p: BrainParams, offer: Offer, palm_filter: PalmFilter):
        self.p, self.offer, self.filter = p, offer, palm_filter
        self.mode, self.why = "wait", "the head has not settled on anyone"
        self.side = None
        self.person_at = self.palm_at = -1e9
        self.lift_since = self.down_since = self.other_since = None
        self.shoulder_seen = {s: (None, -1e9) for s in SIDES}
        self.torso, self.torso_at = None, -1e9
        self.candidate, self.candidate_n = None, 0
        self.events = []

    def _say(self, mode, why):
        if mode != self.mode:
            self.events.append(f"{self.mode} -> {mode}: {why}")
        self.mode, self.why = mode, why

    def _shoulder(self, side, f):
        """This side's shoulder: seen now; or seen in the last shoulder_keep_s; or placed from the other one."""
        if f.shoulders.get(side) is not None:
            return f.shoulders[side]
        xyz, at = self.shoulder_seen[side]
        if xyz is not None and f.t - at <= self.p.shoulder_keep_s:
            return xyz
        other = "right" if side == "left" else "left"
        if f.shoulders.get(other) is not None:
            return f.shoulders[other] + np.array([0.0, SHOULDER_WIDTH if side == "right" else -SHOULDER_WIDTH, 0.0])
        return None

    def update(self, f: Frame):
        """One tracker frame. Returns True when it is a different person (the head should centre afresh)."""
        p = self.p
        if f.person:
            self.person_at = f.t
        if not f.trusted:
            return False
        for s in SIDES:
            if f.shoulders.get(s) is not None:
                self.shoulder_seen[s] = (f.shoulders[s], f.t)
        seen = [f.shoulders[s] for s in SIDES if f.shoulders.get(s) is not None]
        switched = False
        if len(seen) == 2:
            torso = 0.5 * (seen[0] + seen[1])
            jumped = (self.torso is not None and f.t - self.torso_at < 1.5
                      and float(np.linalg.norm(torso - self.torso)) > p.new_person_m)
            if jumped:
                agree = self.candidate is not None and float(np.linalg.norm(torso - self.candidate)) <= p.new_person_m / 2
                self.candidate, self.candidate_n = torso, (self.candidate_n + 1 if agree else 1)
                switched = self.candidate_n >= p.new_person_n
            else:
                self.candidate, self.candidate_n = None, 0
            if switched:
                self.candidate, self.candidate_n = None, 0
                self.offer.reset()
                self.side, self.lift_since, self.down_since = None, None, None
                self.shoulder_seen = {s: (f.shoulders.get(s), f.t) for s in SIDES}
                self.filter.reset()
                self.palm_at = -1e9
                if self.mode == "follow":
                    self._say("hold", "someone else is in front: holding until the head settles on them")
            if not jumped or switched:
                self.torso, self.torso_at = torso, f.t
        lifted = {}
        for s in SIDES:
            palm = f.palms.get(s)
            sh = self._shoulder(s, f) if palm is not None else None
            if palm is None or sh is None:
                continue
            ok, fwd, _ = self.offer.check(s, palm, sh)
            if ok:
                lifted[s] = fwd
        # which hand: the one followed while it stays lifted; another only once that one is gone for switch_s
        if self.side is not None and self.side not in lifted and lifted:
            self.other_since = self.other_since or f.t
            if f.t - self.other_since >= p.switch_s:
                self.side, self.other_since = max(lifted, key=lifted.get), None
                self.filter.reset()
        else:
            self.other_since = None
        if self.side is None and lifted:
            self.side = max(lifted, key=lifted.get)
            self.filter.reset()
        if self.side is not None and f.palms.get(self.side) is not None:
            if self.side in lifted:
                if self.filter.update(f.palms[self.side], f.t):
                    self.palm_at = f.t
                self.lift_since, self.down_since = self.lift_since or f.t, None
            else:
                self.lift_since, self.down_since = None, self.down_since or f.t
        return switched

    def tick(self, now, acquired):
        """(motion, goal palm or None). acquired: the head has settled on the person (since they were found)."""
        p = self.p
        person = now - self.person_at <= p.person_grace_s
        # the person seen after the palm was lost: the hand dropped (a person lost too is nobody seen, not a drop)
        dropped = person and self.person_at > self.palm_at + 0.05
        palm_fresh = now - self.palm_at <= p.stale_s
        palm_recent = now - self.palm_at <= p.palm_grace_s
        lifted = self.lift_since is not None and palm_fresh
        if self.mode == "wait":
            if not acquired:
                return "hold", None
            self._say("home", "the head has settled on the person: watching for a hand")
        if self.mode == "follow":
            if self.down_since is not None and now - self.down_since >= p.away_s:
                self._say("home", "the hand went down")
                self.side = None
            elif lifted or palm_recent:
                self.why = "following the hand" if lifted else "the hand is lost for a moment: coasting"
                return "follow", self.filter.estimate(now)
            elif dropped:
                self._say("home", "the hand went down" if self.down_since is not None else
                          "the hand is gone, the person is still there")
                self.side = None
            elif now - self.person_at < p.lost_home_s:
                self._say("hold", "nobody seen: holding")
                return "hold", None
            else:
                self._say("home", "nobody seen for a while")
                self.side = None
        elif self.mode == "hold":
            if lifted and acquired:
                self._say("follow", "the hand is back")
                return "follow", self.filter.estimate(now)
            if now - self.person_at >= p.lost_home_s:
                self._say("home", "nobody seen for a while")
                self.side = None
            elif not acquired or not dropped:
                return "hold", None          # someone else, or nobody: until the head has settled on them
            else:
                self._say("home", "the hand is gone")
                self.side = None
        # HOME
        if lifted and acquired and now - self.lift_since >= p.offer_s:
            self._say("follow", f"the {self.side} hand is held out")
            return "follow", self.filter.estimate(now)
        if self.mode != "home":
            self._say("home", self.why)
        return "home", None

    def status(self):
        return {"mode": self.mode, "why": self.why, "hand": self.side}
