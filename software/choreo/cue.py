#!/usr/bin/env python3
"""Cue sheets: the data a choreography is made of, and the checks it must pass.

    python3 cue.py validate shows/*.yaml            check, print what would run
    python3 cue.py validate shows/hello.yaml -v     ... with every resolved cue
    python3 cue.py export shows/beat-demo.yaml      the resolved JSON timeline (see below)

A show is DATA, not code, so it can be checked before anything moves. Everything here is pure
computation — no ROS, no network, no robot — which is the point: the same validation runs in CI,
on a laptop, and on the robot right before a show is allowed to start.

WHAT THIS DOES NOT DO: command anything. Execution, slot claims and preflight live in the
runner; this module only decides whether a show is allowed to exist, and says what it means.

The format is specified in notes/design/choreo-format-v1.md. In short, v1 adds to v0 (which
shows/hello.yaml is written in, and which still validates unchanged):
  * a `music:` block, and cue times written as `beat:` or `bar:` as well as `t:` seconds
  * every cue occupies a SPAN [start, end]; `hold_s` is defined; a lift owns its slot for `secs`
  * clips: named fragments in clip-local time, placed with repeat / time_scale / strength, and
    validated AFTER expansion
  * closed vocabularies for FACE and LIGHT, and named markers that may not be device addresses
  * `export`: the resolved timeline every other tool consumes

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
import hashlib
import json
import math
import os
import re
import sys
import wave

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml: pip install pyyaml")

FORMAT_VERSION = 1
EXPORT_SCHEMA = "opendubs.choreo.timeline/1"

# ---- what a robot will accept -------------------------------------------------------------
SLOTS = ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT")
# DRIVE is deliberately absent. The base does not move until odometry can say where it is:
# opendubs2 currently reports y_mm as NaN with a climbing failure count, so "keep X and Y clear"
# would be advice the robot itself cannot honour. Adding the slot is not the hard part; earning
# it is. (A bounded open-loop alternative is written up, not built: choreo-format-v1.md 12.)

ARM_SLOTS = ("ARM_L", "ARM_R")

MARGIN_DEG = 5.0
# UNSOURCED. These came over from showcase_demo.py with the rest of the checks and carry no note
# of how they were chosen; measuring or sourcing them is workplan item 6.1. Every message that
# cites one says "unsourced" until then.
SPEED_CAP_DEG_S = {1: 25, 2: 30, 3: 35, 4: 30, 5: 35, 6: 60, 7: 70}
# INTERIM, agreed 2026-10-07 (workplan DEC-1, item 1.1). The AVERAGE speed over a segment is the
# hard gate. Its average times this factor — the peak of a minimum-jerk (quintic) move, the worst
# profile the arm controller plausibly uses — is checked as a WARNING: the real profile is
# unmeasured (experiment T4, workplan 6.1), and failing shows that have run on the robot on an
# unmeasured factor would be the validator claiming more than it knows. When the profile is
# measured, this becomes its peak-to-average ratio (1.0 for constant speed, 1.5 for a cubic) and
# the peak becomes the gate.
PEAK_FACTOR = 1.875

# Measured on opendubs2, per side. Both open on a POSITIVE command, but their zeros sit at
# opposite physical ends, so one range cannot serve both.
GRIP_RANGE_M = {"GRIP_L": (-0.050, 0.002), "GRIP_R": (0.000, 0.040)}
GRIP_EFFORT_MAX = 5.0          # the controller's own max_effort; we stay at or under it

HEAD_RANGE_DEG = (10, 170)     # pan and tilt, clamped well inside the servos' 0..180
HEAD_NEUTRAL_DEG = 90.0        # what a clip's `strength` scales head angles toward

# Shoulder/head clearance, measured: contact at J2 182 deg with the elbow folded (~100 deg).
SHOULDER_HEAD_CONTACT_DEG = 182.0
SHOULDER_MARGIN_DEG = 25.0     # what a show must leave
ELBOW_FOLD_MIN_DEG = 60.0      # below this the arm is "extended" and the 1.2 m box bites first
SHOULDER_EXTENDED_MAX_DEG = 135.0   # measured: 140 passed, 146 was rejected on the box

# SOUND plays a NAMED sound effect on the show's chosen output device (format v1, DEC-11 in
# notes/workplan-choreography.md). A name is either one of the Tab5's built-in earcons -- the
# `Cue` enum in firmware/tab5_idf/app_face/cues.h -- or a sound file in the `sounds/` directory
# beside the show, named without its extension. Music is not a SOUND cue; it is the `music:` track.
TAB5_EARCONS = ("heard", "attend", "refuse", "fault", "mode", "boot")
SOUND_FILE_EXTS = (".wav", ".mp3", ".ogg", ".flac")
SOUND_DIR = "sounds"
# Only sizes the slot's span when neither `secs` nor a readable WAV header gives a length.
SOUND_DEFAULT_S = 0.5
SOUND_SECS_SANITY = (0.05, 600.0)
# The head turns toward each target at this rate, each axis independently: the Tab5 firmware's own
# slew limit (kSlewDps in firmware/tab5_idf/app_face/pantilt.cpp), which no caller can override.
# Used to WARN when a head cue arrives before the previous move can have finished; sim_preview.py
# uses the same constant to show where the head actually is.
HEAD_SLEW_DPS = 25.0
HEAD_REACH_TOL_DEG = 1.5       # the firmware's deadband (kDeadbandDeg): closer than this is "there"
LIFT_MAX_S = 8.0               # a single continuous lift command

# FACE: the emote vocabulary as it exists in firmware, and nothing else. Sourced from kEmotes[]
# in firmware/tab5_idf/app_face/app_face.cpp on feat/tab5-idf-subtractive (0e47085). Five, not
# the six or seven the notes describe. No firmware has a network verb for an emote yet.
FACE_EMOTES = ("NEUTRAL", "ALERT", "SQUINT", "SOFT", "SLEEPY")

# LIGHT: the PRISM base colour, as the StamPLC's `LED_COLOR r g b br` parser takes it
# (firmware/rc_serial_probe/src/main.cpp). The firmware CLAMPS; the validator REJECTS, so an
# out-of-range value is a visible mistake rather than a quietly different colour.
LIGHT_RGB_MAX = 255
LIGHT_BR_MAX = 100

MIRRORED_JOINTS = (1, 2, 3)    # left travels negative where right travels positive

# Sanity bounds on the new v1 dials. None of them is a measurement.
BPM_SANITY = (20.0, 300.0)
BEATS_PER_BAR_SANITY = (1, 16)
TIME_SCALE_SANITY = (0.1, 10.0)
REPEAT_MAX = 64

# Keys a cue may carry, per slot. Anything else is refused: a typo such as `hold: 3` used to be
# ignored silently, which is a field only the author thinks matters.
TIME_KEYS = ("t", "beat", "bar")
COMMON_KEYS = set(TIME_KEYS) | {"slot", "hold_s"}
SLOT_KEYS = {
    "ARM_L": {"joints"}, "ARM_R": {"joints"},
    "GRIP_L": {"do", "pos", "effort"}, "GRIP_R": {"do", "pos", "effort"},
    "HEAD": {"pan", "tilt"},
    "LIFT": {"do", "secs", "ack_pinch"},     # ack_pinch is KNOWN so that it is refused by name
    "SOUND": {"do", "sound", "secs"},
    "FACE": {"do", "name"},
    "LIGHT": {"do", "rgb", "br"},
}
PLACEMENT_KEYS = set(TIME_KEYS) | {"clip", "repeat", "strength", "time_scale"}
CLIP_KEYS = {"cues", "length_s", "length_beats", "notes"}
TOP_KEYS = {"format", "id", "name", "slots", "requires", "music", "notes", "clips", "markers",
            "cues"}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# A marker that reads as wiring has put per-unit hardware into a committed show (workplan 5.6).
ADDRESS_RE = re.compile(r"^(relay|r\d|gpio|pin|ch\d|udp|di\d|do\d|servo\d)")

# What a show may use that NOTHING ON THE ROBOT EXECUTES YET. Valid is not executable; these are
# reported as notices on every validation so nobody mistakes the one for the other.
NOT_EXECUTABLE = {
    "FACE": "FACE emote is valid but no firmware implements it yet: neither face takes an emote "
            "verb over the network (workplan 3.3)",
    "LIGHT": "LIGHT is valid but not executable yet: state_out.py already writes LED_COLOR on "
             "every state change, so the runner must LAYER it before a show may (two writers "
             "on one register otherwise)",
    "SOUND": "SOUND is valid but not executable yet: no output device takes a play-sound "
             "command over the network (workplan 1.5, 4.2)",
}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _deg(x):
    return float(x)


def _r(x, nd=6):
    """Round for export, and turn -0.0 into 0.0 so two exports cannot differ by a sign bit."""
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


class ShowError(Exception):
    pass


class Problem:
    """One problem: the sentence, and where it came from so an editor can point at it."""
    __slots__ = ("msg", "cue", "clip", "instance")

    def __init__(self, msg, cue=None, clip=None, instance=None):
        self.msg, self.cue, self.clip, self.instance = msg, cue, clip, instance

    def as_dict(self):
        return {"msg": self.msg, "cue": self.cue, "clip": self.clip, "instance": self.instance}

    def __repr__(self):
        return f"Problem({self.msg!r}, cue={self.cue})"


class Show:
    def __init__(self, doc: dict, path: str):
        self.path = path
        self.id = doc.get("id") or ""
        self.name = doc.get("name") or self.id
        self.requires = doc.get("requires") or {}
        self.cues = doc.get("cues") or []
        self.doc = doc

    # ---- time ------------------------------------------------------------------------------
    def _music(self, P):
        """The music block, checked. Returns None, or a dict with beat_s resolved."""
        m = self.doc.get("music")
        if m is None:
            return None
        if not isinstance(m, dict):
            P(Problem("music: must be a mapping (file, bpm, offset_s, beats_per_bar)"))
            return None
        out = {"file": m.get("file"), "bpm": m.get("bpm"), "offset_s": m.get("offset_s", 0.0),
               "beats_per_bar": m.get("beats_per_bar", 4)}
        for k in m:
            if k not in out:
                P(Problem(f"music: unknown key {k!r} (known: file, bpm, offset_s, beats_per_bar)"))
        ok = True
        bpm = out["bpm"]
        if not _num(bpm):
            P(Problem("music: bpm must be a number"))
            ok = False
        elif not BPM_SANITY[0] <= bpm <= BPM_SANITY[1]:
            P(Problem(f"music: bpm {bpm} outside the sanity bound {BPM_SANITY} — a typo or a "
                      "unit mix-up, not a physical limit"))
            ok = False
        if not _num(out["offset_s"]) or out["offset_s"] < 0:
            P(Problem("music: offset_s must be a number >= 0 (where beat 1 falls, in seconds)"))
            ok = False
        bpb = out["beats_per_bar"]
        if not isinstance(bpb, int) or isinstance(bpb, bool) or \
                not BEATS_PER_BAR_SANITY[0] <= bpb <= BEATS_PER_BAR_SANITY[1]:
            P(Problem(f"music: beats_per_bar must be an integer in {BEATS_PER_BAR_SANITY}"))
            ok = False
        if out["file"] is not None and not isinstance(out["file"], str):
            P(Problem("music: file must be a path relative to the show"))
            ok = False
        if not ok:
            return None
        out["beat_s"] = 60.0 / float(bpm)
        return out

    def _time(self, c, music, local: bool, what: str, P, ref):
        """Resolve one cue's time form to seconds. Returns (seconds, label) or (None, None).

        Arrangement time: t is itself; beat 1 = music.offset_s. Clip-local time (local=True):
        t is from the clip's start; beat 1 / bar "1.1" = the clip's start."""
        forms = [k for k in TIME_KEYS if k in c]
        if len(forms) != 1:
            P(Problem(f"{what}: needs exactly one of t / beat / bar, has "
                      f"{', '.join(forms) or 'none'}", *ref))
            return None, None
        form = forms[0]
        v = c[form]
        if form == "t":
            if not _num(v):
                P(Problem(f"{what}: missing or non-numeric t", *ref))
                return None, None
            return float(v), f"t {v}"
        if music is None:
            P(Problem(f"{what}: {form}: needs a music: block (bpm) to mean anything", *ref))
            return None, None
        if form == "beat":
            if not _num(v):
                P(Problem(f"{what}: beat must be a number (1-based, may be fractional)", *ref))
                return None, None
            beat = float(v)
            label = f"beat {v}"
        else:
            s = str(v)
            mm = re.fullmatch(r"\s*(\d+)(?:\.(\d+(?:\.\d+)?))?\s*", s)
            if not mm or "." in (mm.group(2) or ""):
                P(Problem(f"{what}: bar {v!r} is not \"<bar>.<beat>\" (1-based, e.g. \"4.1\"); "
                          "write beat: for sub-beats", *ref))
                return None, None
            bar, bib = int(mm.group(1)), int(mm.group(2) or 1)
            if bar < 1 or not 1 <= bib <= music["beats_per_bar"]:
                P(Problem(f"{what}: bar {v!r} out of range — bars count from 1 and a bar has "
                          f"{music['beats_per_bar']} beats", *ref))
                return None, None
            beat = (bar - 1) * music["beats_per_bar"] + bib
            label = f"bar {s.strip()}"
        secs = (beat - 1.0) * music["beat_s"] + (0.0 if local else float(music["offset_s"]))
        return secs, label

    # ---- resolution: clips expanded, times in seconds ---------------------------------------
    def _clips(self, music, P):
        raw = self.doc.get("clips") or {}
        if not isinstance(raw, dict):
            P(Problem("clips: must be a mapping of name -> clip"))
            return {}
        out = {}
        for name, cl in raw.items():
            if not isinstance(name, str) or not NAME_RE.match(name):
                P(Problem(f"clip {name!r}: names are lower_snake_case identifiers", clip=name))
                continue
            if not isinstance(cl, dict) or not isinstance(cl.get("cues"), list) or not cl["cues"]:
                P(Problem(f"clip {name!r}: needs cues: [...]", clip=name))
                continue
            for k in cl:
                if k not in CLIP_KEYS:
                    P(Problem(f"clip {name!r}: unknown key {k!r}", clip=name))
            length = None
            if "length_s" in cl and "length_beats" in cl:
                P(Problem(f"clip {name!r}: give length_s or length_beats, not both", clip=name))
            elif "length_s" in cl:
                if _num(cl["length_s"]) and cl["length_s"] > 0:
                    length = float(cl["length_s"])
                else:
                    P(Problem(f"clip {name!r}: length_s must be > 0", clip=name))
            elif "length_beats" in cl:
                if music is None:
                    P(Problem(f"clip {name!r}: length_beats needs a music: block", clip=name))
                elif _num(cl["length_beats"]) and cl["length_beats"] > 0:
                    length = float(cl["length_beats"]) * music["beat_s"]
                else:
                    P(Problem(f"clip {name!r}: length_beats must be > 0", clip=name))
            local = []
            for j, lc in enumerate(cl["cues"]):
                ref = (None, name, None)
                if not isinstance(lc, dict):
                    P(Problem(f"clip {name!r} cue {j}: not a mapping", *ref))
                    continue
                if "clip" in lc:
                    P(Problem(f"clip {name!r} cue {j}: a clip may not place a clip (no nesting "
                              "in format v1)", *ref))
                    continue
                t, label = self._time(lc, music, True, f"clip {name!r} cue {j}", P, ref)
                if t is None:
                    continue
                if t < 0:
                    P(Problem(f"clip {name!r} cue {j}: before the clip's own start", *ref))
                    continue
                local.append((t, j, lc, label))
            out[name] = {"length": length, "cues": local}
        return out

    def _resolve(self, P):
        """Every cue as an event with an absolute time, clips expanded. Returns (events, music,
        markers). An event: {t, slot, fields, src, where, seq}."""
        music = self._music(P)
        clips = self._clips(music, P)
        events = []
        seq = 0
        for i, c in enumerate(self.cues):
            if not isinstance(c, dict):
                P(Problem(f"cue {i}: not a mapping", i))
                continue
            if "clip" in c:
                ref = (i, c.get("clip"), None)
                what = f"cue {i} (clip {c.get('clip')!r})"
                for k in c:
                    if k not in PLACEMENT_KEYS:
                        P(Problem(f"{what}: unknown key {k!r} on a clip placement", *ref))
                start, label = self._time(c, music, False, what, P, ref)
                if start is None:
                    continue
                if start < 0:
                    P(Problem(f"{what}: resolves to t={start:.2f}s, before the show starts", *ref))
                    continue
                cl = clips.get(c["clip"])
                if cl is None:
                    if c["clip"] not in (self.doc.get("clips") or {}):
                        P(Problem(f"{what}: no such clip", *ref))
                    continue
                rep = c.get("repeat", 1)
                ts = c.get("time_scale", 1.0)
                st = c.get("strength", 1.0)
                if not isinstance(rep, int) or isinstance(rep, bool) or not 1 <= rep <= REPEAT_MAX:
                    P(Problem(f"{what}: repeat must be an integer 1..{REPEAT_MAX}", *ref))
                    continue
                if not _num(ts) or not TIME_SCALE_SANITY[0] <= ts <= TIME_SCALE_SANITY[1]:
                    P(Problem(f"{what}: time_scale must be in {TIME_SCALE_SANITY} (sanity bound)",
                              *ref))
                    continue
                if not _num(st) or not 0 < st <= 1:
                    P(Problem(f"{what}: strength must be in (0, 1] — the dial only turns "
                              "amplitude down", *ref))
                    continue
                if rep > 1 and cl["length"] is None:
                    P(Problem(f"{what}: clip is repeated but declares no length_s/length_beats",
                              *ref))
                    continue
                for k in range(rep):
                    base = start + (k * cl["length"] * ts if rep > 1 else 0.0)
                    for (lt, j, lc, llabel) in cl["cues"]:
                        f = {kk: vv for kk, vv in lc.items() if kk not in TIME_KEYS and kk != "slot"}
                        if "hold_s" in f and _num(f["hold_s"]):
                            f["hold_s"] = float(f["hold_s"]) * ts
                        if st != 1.0:
                            f = _apply_strength(lc.get("slot"), f, float(st))
                        t = base + lt * ts
                        events.append({
                            "t": t, "slot": lc.get("slot"), "fields": f, "seq": seq,
                            "src": {"index": i, "time": label, "clip": c["clip"], "instance": k,
                                    "local_index": j},
                            "where": f"cue at t={t:.2f}s (clip {c['clip']} #{k + 1}, "
                                     f"its cue {j})",
                            "keys": set(lc)})
                        seq += 1
                continue
            t, label = self._time(c, music, False, f"cue {i}", P, (i, None, None))
            if t is None:
                continue
            if t < 0:
                P(Problem(f"cue {i}: resolves to t={t:.2f}s, before the show starts", i))
                continue
            where = f"cue at t={t:.1f}s" if label.startswith("t ") else f"cue at t={t:.2f}s ({label})"
            events.append({
                "t": t, "slot": c.get("slot"),
                "fields": {k: v for k, v in c.items() if k not in TIME_KEYS and k != "slot"},
                "seq": seq, "where": where, "keys": set(c),
                "src": {"index": i, "time": label, "clip": None, "instance": None,
                        "local_index": None}})
            seq += 1
        markers = self._markers(music, P)
        events.sort(key=lambda e: (e["t"], e["seq"]))
        return events, music, markers

    def _markers(self, music, P):
        raw = self.doc.get("markers") or []
        out = []
        if not isinstance(raw, list):
            P(Problem("markers: must be a list"))
            return out
        for i, m in enumerate(raw):
            if not isinstance(m, dict):
                P(Problem(f"marker {i}: not a mapping"))
                continue
            name = m.get("name")
            if not isinstance(name, str) or not NAME_RE.match(name):
                P(Problem(f"marker {i}: name {name!r} must be a lower_snake_case signal name"))
                continue
            if ADDRESS_RE.match(name):
                P(Problem(f"marker {i}: {name!r} reads as a device address — a marker names a "
                          "signal (disco_ball_on), and the robot maps it to wiring"))
                continue
            for k in m:
                if k not in set(TIME_KEYS) | {"name"}:
                    P(Problem(f"marker {i}: unknown key {k!r}"))
            t, label = self._time(m, music, False, f"marker {i}", P, (None, None, None))
            if t is None:
                continue
            out.append({"t": t, "name": name, "src": {"index": i, "time": label}})
        out.sort(key=lambda x: (x["t"], x["src"]["index"]))
        return out

    def by_time(self) -> list:
        """Resolved cues in execution order, as flat dicts with `t` in seconds. Stable, so two
        cues at the same instant keep the order the author wrote them in."""
        events, _, _ = self._resolve(lambda p: None)
        return [{"t": e["t"], "slot": e["slot"], **e["fields"]} for e in events]

    # ---- the checks ------------------------------------------------------------------------
    def analyse(self, limits: dict) -> dict:
        """Resolve, check and span the whole show. Returns {problems, notices, events, music,
        markers}. Events carry start/end and, for arms, the full resolved pose."""
        problems: list[Problem] = []
        warnings: list[Problem] = []   # pointed at a cue like a problem, but never invalidating
        notices: list[str] = []
        P = problems.append

        fmt = self.doc.get("format", FORMAT_VERSION)
        if fmt != FORMAT_VERSION:
            P(Problem(f"format: {fmt!r} — this validator reads format {FORMAT_VERSION}"))
        if not self.id:
            P(Problem("show has no id"))
        if not self.cues:
            P(Problem("show has no cues"))
        if not isinstance(self.cues, list):
            P(Problem("cues: must be a list"))
            self.cues = []
        for k in self.doc:
            if k not in TOP_KEYS:
                notices.append(f"top-level key {k!r} is not part of format v1 and is ignored")
        declared = set(self.doc.get("slots") or [])
        for s in sorted(declared, key=str):
            if s not in SLOTS:
                P(Problem(f"unknown slot {s!r} (known: {', '.join(SLOTS)})"
                          + ("; DRIVE is not available until odometry is trusted"
                             if s == "DRIVE" else "")))

        events, music, markers = self._resolve(P)

        if music and music.get("file") and self.path and os.path.isfile(self.path):
            audio = os.path.join(os.path.dirname(os.path.abspath(self.path)), music["file"])
            if not os.path.isfile(audio):
                notices.append(f"music file {music['file']!r} not found next to the show — motion "
                               "still validates; generate or fetch the audio to hear it")

        used = set()
        # per-slot arm state, so speed is measured between what actually happens
        arm_state: dict[str, dict[int, float]] = {}
        arm_free_at: dict[str, float] = {}
        owner_until: dict[str, float] = {}
        # where the head is and where it is going, as the firmware would slew it from centre
        head_pos = {"pan": 90.0, "tilt": 90.0}
        head_tgt = dict(head_pos)
        head_at = 0.0

        # Sorted by time, not required to be WRITTEN that way: a show reads better grouped by
        # limb ("here is what the right arm does"), and the runner works from the timeline
        # regardless. Ordering was never a safety property — a mistyped time is still a valid
        # time — so demanding it only punished the readable way to write one.
        for e in events:
            where, t, slot, c = e["where"], e["t"], e["slot"], e["fields"]
            ref = (e["src"]["index"], e["src"]["clip"], e["src"]["instance"])

            def p(msg, _ref=ref):
                P(Problem(msg, *_ref))

            if slot not in SLOTS:
                p(f"{where}: unknown slot {slot!r}")
                e["bad"] = True
                continue
            used.add(slot)
            if slot in NOT_EXECUTABLE and NOT_EXECUTABLE[slot] not in notices:
                notices.append(NOT_EXECUTABLE[slot])

            for k in sorted(e["keys"] - COMMON_KEYS - SLOT_KEYS[slot], key=str):
                p(f"{where}: unknown key {k!r} on a {slot} cue"
                  + ("; interp is reserved — format v1 interpolates linearly only"
                     if k == "interp" else ""))
            hold = c.get("hold_s", 0.0) or 0.0
            if not _num(hold) or hold < 0:
                p(f"{where}: hold_s must be a number >= 0")
                hold = 0.0
            hold = float(hold)

            # one owner at a time per slot
            busy_until = owner_until.get(slot, -1.0)
            if t < busy_until - 1e-9:
                p(f"{where}: {slot} is still busy until t={busy_until:.1f}s — "
                  "two cues on one slot is a collision, not a blend")

            if slot in ARM_SLOTS:
                start = arm_free_at.get(slot, 0.0)
                arm_warn: list[str] = []
                for q in self._check_arm(where, c, slot, limits, arm_state, start, t, arm_warn):
                    p(q)
                for q in arm_warn:
                    warnings.append(Problem(q, *ref))
                e["kind"] = "keyframe"
                e["start"], e["end"] = min(start, t), t + hold
                st = arm_state.get(slot, {})
                e["pose"] = {j: st.get(j, 0.0) for j in range(1, 8)}
                arm_free_at[slot] = t + hold
                owner_until[slot] = t + hold
                continue

            intrinsic = 0.0
            if slot in GRIP_RANGE_M:
                msgs = self._check_grip(where, c, slot)
            elif slot == "HEAD":
                msgs = self._check_head(where, c)
                if not msgs:
                    dt = max(0.0, t - head_at)
                    short = []
                    for ax in ("pan", "tilt"):
                        d = head_tgt[ax] - head_pos[ax]
                        step = HEAD_SLEW_DPS * dt
                        head_pos[ax] = head_tgt[ax] if abs(d) <= step else head_pos[ax] + math.copysign(step, d)
                        left = abs(head_tgt[ax] - head_pos[ax])
                        if left > HEAD_REACH_TOL_DEG and c.get(ax) is not None:
                            short.append(f"{ax} is still {left:.0f}° short of {head_tgt[ax]:.0f}°")
                    if short:
                        warnings.append(Problem(
                            f"{where}: the head has not finished its last move — "
                            + " and ".join(short) + f" (it turns at {HEAD_SLEW_DPS:.0f}°/s), "
                            "so it will turn toward this target from where it is", *ref))
                    head_at = t
                    for ax in ("pan", "tilt"):
                        if c.get(ax) is not None:
                            head_tgt[ax] = float(c[ax])
            elif slot == "LIFT":
                msgs = self._check_lift(where, c)
                if c.get("do") in ("up", "down") and _num(c.get("secs")):
                    intrinsic = float(c["secs"])
            elif slot == "SOUND":
                msgs = self._check_sound(where, c)
                intrinsic = self._sound_length(c)
            elif slot == "FACE":
                msgs = self._check_face(where, c)
            else:
                msgs = self._check_light(where, c)
            for q in msgs:
                p(q)
            e["kind"] = "command"
            e["start"], e["end"] = t, t + max(intrinsic, hold)
            owner_until[slot] = e["end"]

        undeclared = used - declared
        if declared and undeclared:
            P(Problem("cues use slots the show did not declare: " + ", ".join(sorted(undeclared))
                      + " — the runner's preflight is built from the declaration"))
        return {"problems": problems, "warnings": warnings, "notices": notices, "events": events, "music": music,
                "markers": markers, "declared": declared, "used": used}

    def check(self, limits: dict) -> list[Problem]:
        """Every problem, with where it came from."""
        return self.analyse(limits)["problems"]

    def validate(self, limits: dict) -> list[str]:
        """Every problem found, as human sentences. Empty list means the show may run."""
        return [q.msg for q in self.check(limits)]

    def _check_arm(self, where, c, slot, limits, arm_state, seg_start, t, warn=None):
        p = []
        side = "left" if slot == "ARM_L" else "right"
        m = -1 if side == "left" else 1
        joints = c.get("joints")
        if not isinstance(joints, dict) or not joints:
            return [f"{where}: {slot} cue needs joints: {{n: deg, ...}}"]

        state = arm_state.setdefault(slot, {})
        # The move ARRIVES at t. It starts when this arm's previous keyframe ended: that
        # keyframe's t plus its hold_s (choreo-format-v1.md 5). 0 for the first keyframe.
        dt = t - seg_start
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
            if not _num(val):
                p.append(f"{where}: {slot} J{j} is not a number of degrees")
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
            avg = abs(signed - prev) / dt
            peak = avg * PEAK_FACTOR
            cap = SPEED_CAP_DEG_S[j]
            if avg > cap + 1e-9:
                p.append(f"{where}: {slot} J{j} moves {avg:.0f} deg/s over {dt:.1f}s "
                         f"(cap {cap}, unsourced)")
            elif peak > cap + 1e-9 and warn is not None:
                need = abs(signed - prev) * PEAK_FACTOR / cap
                warn.append(f"{where}: {slot} J{j} may peak at {peak:.0f} deg/s if the arm eases "
                            f"(average {avg:.0f} x {PEAK_FACTOR}; cap {cap}, unsourced) — "
                            f"{need:.1f}s would be safe either way")
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
        return p

    def _check_grip(self, where, c, slot):
        p = []
        do = c.get("do")
        pos = c.get("pos")
        if do in ("open", "close") and pos is None:
            return p                      # named ends are resolved per side by the runner
        if pos is None:
            return [f"{where}: {slot} needs do: open|close, or pos: <metres>"]
        if not _num(pos):
            return [f"{where}: {slot} pos must be a number of metres"]
        lo, hi = GRIP_RANGE_M[slot]
        if not (lo <= float(pos) <= hi):
            p.append(f"{where}: {slot} pos {float(pos):+.3f} outside [{lo:+.3f}, {hi:+.3f}] "
                     "— the two sides were zeroed in different physical states")
        eff = c.get("effort")
        if eff is not None and not (_num(eff) and 0 < float(eff) <= GRIP_EFFORT_MAX):
            p.append(f"{where}: {slot} effort {eff} outside (0, {GRIP_EFFORT_MAX}]")
        return p

    def _check_head(self, where, c):
        p = []
        for k in ("pan", "tilt"):
            v = c.get(k)
            if v is None:
                continue
            if not _num(v) or not (HEAD_RANGE_DEG[0] <= float(v) <= HEAD_RANGE_DEG[1]):
                p.append(f"{where}: head {k} {v} outside {HEAD_RANGE_DEG}")
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
            elif not _num(secs) or not (0.2 <= float(secs) <= LIFT_MAX_S):
                p.append(f"{where}: lift secs {secs} outside 0.2..{LIFT_MAX_S}")
        if c.get("ack_pinch"):
            p.append(f"{where}: a show may not acknowledge the pinch guard — "
                     "consent through the zone is a human's decision, every time")
        return p

    def _sounds_dir(self):
        if self.path and os.path.isfile(self.path):
            return os.path.join(os.path.dirname(os.path.abspath(self.path)), SOUND_DIR)
        return None

    def _sound_file(self, name):
        d = self._sounds_dir()
        if not d or not isinstance(name, str):
            return None
        for ext in SOUND_FILE_EXTS:
            f = os.path.join(d, name + ext)
            if os.path.isfile(f):
                return f
        return None

    def _sound_length(self, c):
        if _num(c.get("secs")):
            return float(c["secs"])
        f = self._sound_file(c.get("sound"))
        if f and f.endswith(".wav"):
            try:
                with wave.open(f) as w:
                    return w.getnframes() / float(w.getframerate())
            except (wave.Error, OSError, ZeroDivisionError):
                pass
        return SOUND_DEFAULT_S

    def _check_sound(self, where, c):
        if c.get("do") != "play":
            return [f"{where}: sound do must be play (SOUND plays a named sound effect)"]
        p = []
        name = c.get("sound")
        if not isinstance(name, str) or not name:
            p.append(f"{where}: play needs a sound name")
        elif name not in TAB5_EARCONS and not self._sound_file(name):
            p.append(f"{where}: unknown sound {name!r} — not a Tab5 earcon "
                     f"({', '.join(TAB5_EARCONS)}) and no file {SOUND_DIR}/{name}.* beside the show")
        secs = c.get("secs")
        if secs is not None and not (_num(secs) and SOUND_SECS_SANITY[0] <= float(secs) <= SOUND_SECS_SANITY[1]):
            p.append(f"{where}: sound secs {secs} outside {SOUND_SECS_SANITY}")
        return p

    def _check_face(self, where, c):
        if c.get("do") != "emote":
            return [f"{where}: face do must be emote (the only FACE verb in format v1)"]
        name = c.get("name")
        if name not in FACE_EMOTES:
            return [f"{where}: face emote {name!r} is not one the face has "
                    f"({', '.join(FACE_EMOTES)} — from kEmotes[] in the ESP-IDF face)"]
        return []

    def _check_light(self, where, c):
        do = c.get("do")
        if isinstance(do, bool):
            # YAML 1.1 reads a bare `off` / `on` as a boolean, which is why the verb is not `off`.
            return [f"{where}: light do: {do!r} — YAML read a bare off/on as a boolean; the verb "
                    "is release (hand the light back to the robot's state colour)"]
        if do == "release":
            extra = [k for k in ("rgb", "br") if k in c]
            return [f"{where}: light release takes no {', '.join(extra)}"] if extra else []
        if do != "color":
            return [f"{where}: light do must be color|release"]
        p = []
        rgb = c.get("rgb")
        if not (isinstance(rgb, list) and len(rgb) == 3 and all(
                isinstance(v, int) and not isinstance(v, bool) for v in rgb)):
            p.append(f"{where}: light rgb must be [r, g, b], integers")
        elif not all(0 <= v <= LIGHT_RGB_MAX for v in rgb):
            p.append(f"{where}: light rgb {rgb} outside 0..{LIGHT_RGB_MAX} — the firmware would "
                     "clamp it to a colour nobody wrote")
        br = c.get("br")
        if br is not None and not (_num(br) and 0 <= br <= LIGHT_BR_MAX):
            p.append(f"{where}: light br {br} outside 0..{LIGHT_BR_MAX}")
        return p

    # ---- the resolved timeline --------------------------------------------------------------
    def export(self, limits: dict, limits_path: str | None = None) -> dict:
        """The resolved timeline: what the show MEANS, with every inference made explicit.
        Schema in notes/design/choreo-format-v1.md 10. Deterministic for a given show+limits."""
        a = self.analyse(limits)
        declared = [s for s in SLOTS if s in a["declared"]] or [s for s in SLOTS if s in a["used"]]
        tracks = {s: [] for s in declared}
        for e in a["events"]:
            if e.get("bad") or "start" not in e or e["slot"] not in tracks:
                continue
            ev = {"t": _r(e["t"]), "start": _r(e["start"]), "end": _r(e["end"]),
                  "slot": e["slot"], "kind": e["kind"], "cue": _jsonable(e["fields"]),
                  "src": e["src"]}
            if e["slot"] in ARM_SLOTS:
                sign = -1 if e["slot"] == "ARM_L" else 1
                ev["pose_signed"] = {str(j): _r(v, 4) for j, v in e["pose"].items()}
                ev["pose"] = {str(j): _r(v * (sign if j in MIRRORED_JOINTS else 1), 4)
                              for j, v in e["pose"].items()}
            tracks[e["slot"]].append(ev)
        for evs in tracks.values():
            evs.sort(key=lambda x: (x["start"], x["t"]))
        music = None
        if a["music"]:
            music = {k: a["music"][k] for k in ("file", "bpm", "offset_s", "beats_per_bar")}
            music["beat_s"] = _r(a["music"]["beat_s"])
        ends = [e["end"] for e in a["events"] if "end" in e] + [m["t"] for m in a["markers"]]
        va = None
        if limits_path:
            with open(limits_path, "rb") as fh:
                va = {"limits": os.path.basename(limits_path),
                      "sha256": hashlib.sha256(fh.read()).hexdigest()}
        return {
            "schema": EXPORT_SCHEMA,
            "show": {"id": self.id, "name": self.name, "source": self.path,
                     "format": self.doc.get("format", FORMAT_VERSION)},
            "validated_against": va,
            "valid": not a["problems"],
            "problems": [q.as_dict() for q in a["problems"]],
            "warnings": [q.as_dict() for q in a["warnings"]],
            "notices": a["notices"],
            "requires": _jsonable(self.requires),
            "slots": declared,
            "music": music,
            "duration_s": _r(max(ends, default=0.0)),
            "markers": [{"t": _r(m["t"]), "name": m["name"], "src": m["src"]}
                        for m in a["markers"]],
            "tracks": tracks,
        }


def _apply_strength(slot, f, s):
    """A clip's `strength`: arm joints scale toward 0 deg, head angles toward 90 deg. Nothing
    else is touched — a gripper, a lift, a sound or a colour has no amplitude to scale."""
    f = dict(f)
    if slot in ARM_SLOTS and isinstance(f.get("joints"), dict):
        f["joints"] = {k: (v * s if _num(v) else v) for k, v in f["joints"].items()}
    elif slot == "HEAD":
        for k in ("pan", "tilt"):
            if _num(f.get(k)):
                f[k] = HEAD_NEUTRAL_DEG + (f[k] - HEAD_NEUTRAL_DEG) * s
    return f


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, float):
        return _r(x, 6)
    return x


def joint_state_to_outward(names, positions_rad, side: str, nd: int = 1) -> dict:
    """A measured sensor_msgs/JointState -> a keyframe's joints, in the arm's OWN outward sense.

    This is the inverse of the mirror the validator applies, and it lives here so there is ONE
    copy of the mirror rule. Teaching a pose by hand (choreo-teach-and-sync.md) captures measured
    positions; without this, every pose taught on the LEFT arm lands with J1-J3 inverted.
    Returns {joint_number: degrees}, rounded to `nd` decimals. Unknown names are ignored."""
    if side not in ("left", "right"):
        raise ValueError("side must be left|right")
    m = -1 if side == "left" else 1
    out = {}
    for n, pos in zip(names, positions_rad):
        mm = re.fullmatch(rf"openarm_{side}_joint([1-7])", str(n))
        if not mm or not _num(pos):
            continue
        j = int(mm.group(1))
        deg = math.degrees(pos) * (m if j in MIRRORED_JOINTS else 1)
        out[j] = _r(deg, nd)
    return out


def load_limits(path: str) -> dict:
    """joint name -> (lower, upper) in radians, from the ARM SOFTWARE's own file."""
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    return {k: (float(v["lower"]), float(v["upper"])) for k, v in raw.items()}


def load_show(path: str) -> Show:
    with open(path) as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise ShowError(f"{path}: not a mapping")
    return Show(doc, path)


def show_from_text(text: str, path: str = "") -> Show:
    """For editors: a show from unsaved text. `path` locates relative files (the audio)."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ShowError("not a mapping")
    return Show(doc, path)


DEFAULT_LIMITS = "/openarm_ws/src/openarm/hw_validation/joint_limits.yaml"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "export"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--limits", default=DEFAULT_LIMITS,
                    help="the arm software's joint_limits.yaml (default: the path in the container)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-o", "--out", help="export: write here instead of stdout")
    ap.add_argument("--allow-invalid", action="store_true",
                    help="export: write the timeline even if the show fails (valid: false)")
    a = ap.parse_args()

    limits = load_limits(a.limits)
    paths = [q for p in a.paths for q in sorted(glob.glob(p))] or a.paths

    if a.cmd == "export":
        if len(paths) != 1:
            sys.exit("export takes exactly one show")
        tl = load_show(paths[0]).export(limits, a.limits)
        if not tl["valid"] and not a.allow_invalid:
            for q in tl["problems"]:
                print("FAIL:", q["msg"], file=sys.stderr)
            sys.exit("refusing to export an invalid show (pass --allow-invalid to inspect it)")
        text = json.dumps(tl, indent=2, sort_keys=True) + "\n"
        if a.out:
            with open(a.out, "w") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        sys.exit(0 if tl["valid"] else 1)

    bad = 0
    for path in paths:
        show = load_show(path)
        a_ = show.analyse(limits)
        problems = a_["problems"]
        dur = max((e.get("end", e["t"]) for e in a_["events"]), default=0.0)
        slots = ", ".join(sorted(show.doc.get("slots") or []))
        print(f"\n{show.id or path}  ({len(show.cues)} cues, {len(a_['events'])} resolved, "
              f"{dur:.0f}s, slots: {slots})")
        if a.verbose:
            for e in a_["events"]:
                span = f"[{e['start']:6.2f}..{e['end']:6.2f}]" if "start" in e else " " * 16
                print(f"   t={e['t']:6.2f} {span}  {str(e['slot']):6s}  "
                      + ", ".join(f"{k}={v}" for k, v in e["fields"].items())
                      + (f"   <- {e['src']['time']}" if not e['src']['time'].startswith("t ")
                         else "")
                      + (f" clip {e['src']['clip']} #{e['src']['instance'] + 1}"
                         if e["src"]["clip"] else ""))
        for n in a_["notices"]:
            print("   note:", n)
        for w in a_["warnings"]:
            print("   warn:", w.msg)
        if problems:
            bad += 1
            for q in problems:
                print("   FAIL:", q.msg)
        else:
            print("   ok")
    print()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
