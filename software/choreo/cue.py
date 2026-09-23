#!/usr/bin/env python3
"""Cue sheets: the data a choreography is made of, and the checks it must pass.

    python3 cue.py validate shows/*.yaml            check, print what would run
    python3 cue.py validate shows/hello.yaml -v     ... with every resolved waypoint

A show is DATA, not code, so it can be checked before anything moves. Everything here is pure
computation — no ROS, no network, no robot — which is the point: the same validation runs in CI,
on a laptop, and on the robot right before a show is allowed to start.

WHAT THIS DOES NOT DO: command anything. Execution, slot claims and preflight live in the
runner; this module only decides whether a show is allowed to exist.

The checks are the ones the hand-written demos earned:
  * every waypoint inside the arm's own limits with a margin (joint_limits.yaml, from the arm
    software — never a copy of the numbers in this repo)
  * a per-joint speed cap, because "over 3 seconds" hides a 45 deg/s elbow
  * grippers clamped per side, since the two sides were zeroed in different physical states
  * the head's travel, and the SHOULDER/HEAD CLEARANCE RULE measured on the robot: with the
    elbow folded the shoulder reaches the head at 182 deg, and nothing in the arm controller
    stops it — the only Cartesian guard is a 1.2 m box, which that pose satisfies
  * one owner per slot at a time: two cues driving the same thing is the bug class that made
    the lift relays chatter (see the Tab5 STOP-stream fix), not a blend
"""
from __future__ import annotations

import argparse
import glob
import math
import sys

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml: pip install pyyaml")

# ---- what a robot will accept -------------------------------------------------------------
SLOTS = ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT")
# DRIVE is deliberately absent. The base does not move until odometry can say where it is:
# opendubs2 currently reports y_mm as NaN with a climbing failure count, so "keep X and Y clear"
# would be advice the robot itself cannot honour. Adding the slot is not the hard part; earning
# it is.

MARGIN_DEG = 5.0
SPEED_CAP_DEG_S = {1: 25, 2: 30, 3: 35, 4: 30, 5: 35, 6: 60, 7: 70}

# Measured on opendubs2, per side. Both open on a POSITIVE command, but their zeros sit at
# opposite physical ends, so one range cannot serve both.
GRIP_RANGE_M = {"GRIP_L": (-0.050, 0.002), "GRIP_R": (0.000, 0.040)}
GRIP_EFFORT_MAX = 5.0          # the controller's own max_effort; we stay at or under it

HEAD_RANGE_DEG = (10, 170)     # pan and tilt, clamped well inside the servos' 0..180

# Shoulder/head clearance, measured: contact at J2 182 deg with the elbow folded (~100 deg).
SHOULDER_HEAD_CONTACT_DEG = 182.0
SHOULDER_MARGIN_DEG = 25.0     # what a show must leave
ELBOW_FOLD_MIN_DEG = 60.0      # below this the arm is "extended" and the 1.2 m box bites first
SHOULDER_EXTENDED_MAX_DEG = 135.0   # measured: 140 passed, 146 was rejected on the box

SOUND_BAND_HZ = (1800, 4500)   # what the head's speaker actually carries, judged on the robot
LIFT_MAX_S = 8.0               # a single continuous lift command

MIRRORED_JOINTS = (1, 2, 3)    # left travels negative where right travels positive


def _deg(x):
    return float(x)


class ShowError(Exception):
    pass


class Show:
    def __init__(self, doc: dict, path: str):
        self.path = path
        self.id = doc.get("id") or ""
        self.name = doc.get("name") or self.id
        self.requires = doc.get("requires") or {}
        self.cues = doc.get("cues") or []
        self.doc = doc

    def by_time(self) -> list:
        """Cues in execution order. Stable, so two cues at the same instant keep the order the
        author wrote them in."""
        return sorted([c for c in self.cues if isinstance(c.get("t"), (int, float))],
                      key=lambda c: float(c["t"]))

    # ---- the checks ------------------------------------------------------------------------
    def validate(self, limits: dict) -> list[str]:
        """Every problem found, as human sentences. Empty list means the show may run."""
        p: list[str] = []
        if not self.id:
            p.append("show has no id")
        if not self.cues:
            p.append("show has no cues")
        declared = set(self.doc.get("slots") or [])
        for s in declared:
            if s not in SLOTS:
                p.append(f"unknown slot {s!r} (known: {', '.join(SLOTS)})"
                         + ("; DRIVE is not available until odometry is trusted"
                            if s == "DRIVE" else ""))

        used = set()
        # per-slot arm state, so speed is measured between what actually happens
        arm_state: dict[str, dict[int, float]] = {}
        arm_time: dict[str, float] = {}
        owner_until: dict[str, float] = {}

        # Sorted by time, not required to be WRITTEN that way: a show reads better grouped by
        # limb ("here is what the right arm does"), and the runner works from the timeline
        # regardless. Ordering was never a safety property — a mistyped time is still a valid
        # time — so demanding it only punished the readable way to write one.
        for i, c in enumerate(self.by_time()):
            where = f"cue {i}"
            t = c.get("t")
            if not isinstance(t, (int, float)):
                p.append(f"{where}: missing or non-numeric t")
                continue
            where = f"cue at t={t:.1f}s"
            slot = c.get("slot")
            if slot not in SLOTS:
                p.append(f"{where}: unknown slot {slot!r}")
                continue
            used.add(slot)

            # one owner at a time per slot
            busy_until = owner_until.get(slot, -1.0)
            if t < busy_until - 1e-9:
                p.append(f"{where}: {slot} is still busy until t={busy_until:.1f}s — "
                         "two cues on one slot is a collision, not a blend")
            owner_until[slot] = t + float(c.get("hold_s", 0.0) or 0.0)

            if slot in ("ARM_L", "ARM_R"):
                p += self._check_arm(where, c, slot, limits, arm_state, arm_time)
            elif slot in GRIP_RANGE_M:
                p += self._check_grip(where, c, slot)
            elif slot == "HEAD":
                p += self._check_head(where, c)
            elif slot == "LIFT":
                p += self._check_lift(where, c)
            elif slot == "SOUND":
                p += self._check_sound(where, c)

        undeclared = used - declared
        if declared and undeclared:
            p.append("cues use slots the show did not declare: " + ", ".join(sorted(undeclared))
                     + " — the runner's preflight is built from the declaration")
        return p

    def _check_arm(self, where, c, slot, limits, arm_state, arm_time):
        p = []
        side = "left" if slot == "ARM_L" else "right"
        m = -1 if side == "left" else 1
        joints = c.get("joints")
        if not isinstance(joints, dict) or not joints:
            return [f"{where}: {slot} cue needs joints: {{n: deg, ...}}"]

        state = arm_state.setdefault(slot, {})
        t_prev = arm_time.get(slot, 0.0)
        dt = float(c["t"]) - t_prev
        if dt <= 0:
            p.append(f"{where}: {slot} has no time to move since its last cue")
            dt = 1e9   # keep checking limits; speed is meaningless here

        for jn, val in joints.items():
            try:
                j = int(jn)
            except (TypeError, ValueError):
                p.append(f"{where}: joint key {jn!r} is not a number")
                continue
            if not 1 <= j <= 7:
                p.append(f"{where}: joint {j} out of range 1..7")
                continue
            deg = _deg(val)
            # mirrored joints are written in the arm's OWN outward sense, so a show reads the
            # same for both arms; the sign is applied here, once
            signed = deg * (m if j in MIRRORED_JOINTS else 1)
            name = f"openarm_{side}_joint{j}"
            lo, hi = limits.get(name, (None, None))
            if lo is None:
                p.append(f"{where}: no limits known for {name}")
            else:
                lo_d, hi_d = math.degrees(lo) + MARGIN_DEG, math.degrees(hi) - MARGIN_DEG
                if not (lo_d <= signed <= hi_d):
                    p.append(f"{where}: {slot} J{j} {signed:+.1f} deg outside usable "
                             f"[{lo_d:+.1f}, {hi_d:+.1f}]")
            prev = state.get(j, 0.0)
            speed = abs(signed - prev) / dt
            cap = SPEED_CAP_DEG_S[j]
            if speed > cap + 1e-9:
                p.append(f"{where}: {slot} J{j} moves {speed:.0f} deg/s over {dt:.1f}s "
                         f"(cap {cap})")
            state[j] = signed

        # clearance: the shoulder is only allowed near the head with the elbow folded, and
        # never closer than the margin
        j2 = abs(state.get(2, 0.0))
        j4 = state.get(4, 0.0)
        if j2 > SHOULDER_EXTENDED_MAX_DEG and j4 < ELBOW_FOLD_MIN_DEG:
            p.append(f"{where}: {slot} shoulder {j2:.0f} deg with the elbow at {j4:.0f} deg — "
                     f"an extended arm is refused past ~{SHOULDER_EXTENDED_MAX_DEG:.0f} deg "
                     "(1.2 m workspace box); fold the elbow first")
        if j2 > SHOULDER_HEAD_CONTACT_DEG - SHOULDER_MARGIN_DEG:
            p.append(f"{where}: {slot} shoulder {j2:.0f} deg leaves under "
                     f"{SHOULDER_MARGIN_DEG:.0f} deg to the HEAD (contact measured at "
                     f"{SHOULDER_HEAD_CONTACT_DEG:.0f} deg) — nothing in the arm controller "
                     "stops this")
        arm_time[slot] = float(c["t"])
        return p

    def _check_grip(self, where, c, slot):
        p = []
        do = c.get("do")
        pos = c.get("pos")
        if do in ("open", "close") and pos is None:
            return p                      # named ends are resolved per side by the runner
        if pos is None:
            return [f"{where}: {slot} needs do: open|close, or pos: <metres>"]
        lo, hi = GRIP_RANGE_M[slot]
        if not (lo <= float(pos) <= hi):
            p.append(f"{where}: {slot} pos {float(pos):+.3f} outside [{lo:+.3f}, {hi:+.3f}] "
                     "— the two sides were zeroed in different physical states")
        eff = c.get("effort")
        if eff is not None and not (0 < float(eff) <= GRIP_EFFORT_MAX):
            p.append(f"{where}: {slot} effort {eff} outside (0, {GRIP_EFFORT_MAX}]")
        return p

    def _check_head(self, where, c):
        p = []
        for k in ("pan", "tilt"):
            v = c.get(k)
            if v is None:
                continue
            if not (HEAD_RANGE_DEG[0] <= float(v) <= HEAD_RANGE_DEG[1]):
                p.append(f"{where}: head {k} {float(v):.0f} outside {HEAD_RANGE_DEG}")
        if c.get("pan") is None and c.get("tilt") is None:
            p.append(f"{where}: head cue sets neither pan nor tilt")
        return p

    def _check_lift(self, where, c):
        p = []
        do = c.get("do")
        if do not in ("up", "down", "stop"):
            p.append(f"{where}: lift do must be up|down|stop")
        secs = c.get("secs")
        if do in ("up", "down"):
            if secs is None:
                p.append(f"{where}: lift {do} needs secs")
            elif not (0.2 <= float(secs) <= LIFT_MAX_S):
                p.append(f"{where}: lift secs {secs} outside 0.2..{LIFT_MAX_S}")
            if do == "down" and c.get("ack_pinch"):
                p.append(f"{where}: a show may not acknowledge the pinch guard — "
                         "consent through the zone is a human's decision, every time")
        return p

    def _check_sound(self, where, c):
        p = []
        do = c.get("do")
        if do == "chime":
            return p
        if do != "beep":
            return [f"{where}: sound do must be chime|beep"]
        hz = c.get("hz")
        if hz is None:
            p.append(f"{where}: beep needs hz")
        elif not (SOUND_BAND_HZ[0] <= float(hz) <= SOUND_BAND_HZ[1]):
            p.append(f"{where}: beep {float(hz):.0f} Hz outside {SOUND_BAND_HZ} — "
                     "this speaker does not carry it")
        ms = c.get("ms")
        if ms is not None and not (10 <= float(ms) <= 3000):
            p.append(f"{where}: beep ms {ms} outside 10..3000")
        return p


def load_limits(path: str) -> dict:
    """joint name -> (lower, upper) in radians, from the ARM SOFTWARE's own file."""
    raw = yaml.safe_load(open(path))
    return {k: (float(v["lower"]), float(v["upper"])) for k, v in raw.items()}


def load_show(path: str) -> Show:
    doc = yaml.safe_load(open(path))
    if not isinstance(doc, dict):
        raise ShowError(f"{path}: not a mapping")
    return Show(doc, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--limits", default="/openarm_ws/src/openarm/hw_validation/joint_limits.yaml",
                    help="the arm software's joint_limits.yaml (default: the path in the container)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    limits = load_limits(a.limits)
    paths = [q for p in a.paths for q in sorted(glob.glob(p))] or a.paths
    bad = 0
    for path in paths:
        show = load_show(path)
        problems = show.validate(limits)
        dur = max((float(c.get("t", 0)) for c in show.cues), default=0.0)
        slots = ", ".join(sorted(show.doc.get("slots") or []))
        print(f"\n{show.id or path}  ({len(show.cues)} cues, {dur:.0f}s, slots: {slots})")
        if a.verbose:
            for c in show.cues:
                print(f"   t={float(c.get('t', 0)):5.1f}  {c.get('slot', '?'):6s}  "
                      + ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("t", "slot")))
        if problems:
            bad += 1
            for q in problems:
                print("   FAIL:", q)
        else:
            print("   ok")
    print()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
