#!/usr/bin/env python3
"""Checks on the checks. Runs anywhere: no ROS, no robot.

    python3 tests/test_cue.py

Each case asserts that a specific BAD show is caught, because a validator that only ever says
"ok" is worse than none — it buys confidence it has not earned. The limits fixture is the arm
software's own joint_limits.yaml, copied for testing only; the robot reads the real one.
"""
import json
import math
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from cue import Show, joint_state_to_outward, load_limits, load_show  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIMITS = load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))

fails = []
# What a show that moves everything must declare; the test helpers default to it.
REQUIRES_ALL = {"arms": "active", "tab5": True, "rc_authority": "AUTO"}


def check(name, cues, expect, slots=None):
    """expect: a substring of the problem we require, or None meaning "must pass"."""
    show = Show({"id": "t", "slots": slots if slots is not None else
                 sorted({c["slot"] for c in cues}), "cues": cues, "requires": REQUIRES_ALL}, "test")
    problems = show.validate(LIMITS)
    if expect is None:
        ok = not problems
        detail = "; ".join(problems)
    else:
        ok = any(expect in p for p in problems)
        detail = "; ".join(problems) or "(no problems reported)"
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(f"{name}: expected {expect!r}, got: {detail}")


print("cue sheet validation")

check("plain pose passes",
      [{"t": 3.0, "slot": "ARM_R", "joints": {4: 55}}], None)

check("joint past its limit is caught",
      [{"t": 10.0, "slot": "ARM_R", "joints": {4: 200}}], "outside usable")

check("elbow moving too fast is caught",
      [{"t": 1.0, "slot": "ARM_R", "joints": {4: 120}}], "deg/s")

check("same speed over enough time passes",
      [{"t": 6.0, "slot": "ARM_R", "joints": {4: 120}}], None)

check("shoulder near the head is caught even though the arm controller allows it",
      [{"t": 5.0, "slot": "ARM_R", "joints": {4: 100}},
       {"t": 12.0, "slot": "ARM_R", "joints": {2: 170}}], "HEAD")

check("shoulder high with an EXTENDED arm is caught",
      [{"t": 8.0, "slot": "ARM_R", "joints": {2: 150}}], "fold the elbow")

check("shoulder high with a folded elbow passes",
      [{"t": 4.0, "slot": "ARM_R", "joints": {4: 100}},
       {"t": 10.0, "slot": "ARM_R", "joints": {2: 150}}], None)

check("the mirror is applied: the same show is legal for the left arm",
      [{"t": 4.0, "slot": "ARM_L", "joints": {4: 100}},
       {"t": 10.0, "slot": "ARM_L", "joints": {2: 150}}], None)

check("a gripper half open is legal on either side — the same number means the same grip",
      [{"t": 1.0, "slot": "GRIP_L", "open": 0.5}, {"t": 1.0, "slot": "GRIP_R", "open": 0.5}], None)

check("a gripper open beyond 1 is caught",
      [{"t": 1.0, "slot": "GRIP_R", "open": 1.4}], "within 0..1")

check("the old metres form is refused by name, saying what replaced it",
      [{"t": 1.0, "slot": "GRIP_L", "pos": -0.030}], "open: 0..1")

check("do and open together are ambiguous",
      [{"t": 1.0, "slot": "GRIP_L", "do": "open", "open": 0.5}], "both do and open")

check("two cues owning one slot at once is caught",
      [{"t": 1.0, "slot": "HEAD", "pan": 60, "hold_s": 4.0},
       {"t": 2.0, "slot": "HEAD", "pan": 120}], "still busy")

check("a Tab5 earcon is a known sound",
      [{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}], None)

check("an unknown sound name is caught",
      [{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "atend"}], "unknown sound")

check("SOUND plays named sounds only",
      [{"t": 1.0, "slot": "SOUND", "do": "beep"}], "sound do must be play")

check("a show may not acknowledge the pinch guard",
      [{"t": 1.0, "slot": "LIFT", "do": "down", "secs": 4, "ack_pinch": True}],
      "human's decision")

check("an over-long lift command is caught",
      [{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 30}], "outside")

check("the base is refused outright",
      [{"t": 1.0, "slot": "DRIVE", "do": "strafe"}], "unknown slot",
      slots=["DRIVE"])

check("a show may be WRITTEN grouped by limb, out of time order",
      [{"t": 5.0, "slot": "HEAD", "pan": 60},
       {"t": 2.0, "slot": "HEAD", "pan": 120}], None)

check("... and the speed check still uses real elapsed time when it does",
      [{"t": 9.0, "slot": "ARM_R", "joints": {4: 120}},
       {"t": 1.0, "slot": "ARM_R", "joints": {4: 10}}], None)

check("using an undeclared slot is caught",
      [{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}], "did not declare", slots=["HEAD"])

# ---- format v1 (notes/design/choreo-format-v1.md) -------------------------------------------
# Every case below is new in v1. The v0 cases above are unchanged and must keep passing.

MUSIC = {"bpm": 120, "offset_s": 0.5, "beats_per_bar": 4}      # a beat is 0.5 s, beat 1 at 0.5


def doc_show(cues, slots=None, **top):
    d = {"id": "t", "slots": slots if slots is not None else
         sorted({c["slot"] for c in cues if "slot" in c}
                | {c["slot"] for cl in (top.get("clips") or {}).values()
                   for c in cl.get("cues", []) if "slot" in c}), "cues": cues}
    d.setdefault("requires", REQUIRES_ALL)
    d.update(top)
    return Show(d, "test")


def check_doc(name, show, expect):
    problems = show.validate(LIMITS)
    if expect is None:
        ok, detail = not problems, "; ".join(problems)
    else:
        ok = any(expect in p for p in problems)
        detail = "; ".join(problems) or "(no problems reported)"
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}")
    if not ok:
        fails.append(f"{name}: expected {expect!r}, got: {detail}")


def check_true(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


# Gripper calibration (DEC-4): the robot's file maps 0..1 to each side's controller units.
import grippers  # noqa: E402
_cal = grippers.load(os.path.join(HERE, "gripper_calibration_fixture.yaml"))
check_true("open 0 and 1 map to each side's measured closed and open, whichever way it runs",
           grippers.to_controller(_cal, "left", 0.0) == -0.040 and grippers.to_controller(_cal, "left", 1.0) == 0.0
           and grippers.to_controller(_cal, "right", 0.0) == 0.0 and grippers.to_controller(_cal, "right", 1.0) == 0.040)
check_true("a captured controller position reads back as the same fraction",
           abs(grippers.from_controller(_cal, "left", grippers.to_controller(_cal, "left", 0.3)) - 0.3) < 1e-9
           and abs(grippers.from_controller(_cal, "right", grippers.to_controller(_cal, "right", 0.3)) - 0.3) < 1e-9)
import tempfile  # noqa: E402
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as _fh:
    _fh.write("left: {closed: 0.010, open: 0.012}\nright: {closed: 0, open: 40}\n")
try:
    grippers.load(_fh.name)
    check_true("a calibration with no real travel, or wrong units, is refused", False, "loaded")
except grippers.CalibrationError as _e:
    check_true("a calibration with no real travel, or wrong units, is refused",
               "too close" in str(_e) and "wrong units" in str(_e), str(_e))
os.unlink(_fh.name)
try:
    grippers.load("/nonexistent/grippers.yaml")
    check_true("a missing calibration says how to make one", False, "loaded")
except grippers.CalibrationError as _e:
    check_true("a missing calibration says how to make one", "measure it on the robot" in str(_e), str(_e))

# Earliest legal time (workplan 1.3): placing a taught pose.
_s = doc_show([{"t": 2.0, "slot": "ARM_R", "joints": {4: 30}}])
_r = _s.earliest_time(LIMITS, "ARM_R", {4: 90}, 2.0)
check_true("a pose needing more time than the playhead allows is moved later, by the binding joint",
           _r["moved"] and abs(_r["t"] - 4.0) < 1e-6 and _r["binding_joint"] == 4, _r)
check_true("... and the time that avoids the eased-peak warning is reported too",
           abs(_r["quiet_t"] - (2.0 + 2.0 * 1.875)) < 1e-3, _r)
_r = _s.earliest_time(LIMITS, "ARM_R", {4: 90}, 1.0)
check_true("a placement that passes another keyframe is re-solved from that keyframe",
           _r["free_at"] == 2.0 and abs(_r["t"] - 4.0) < 1e-6, _r)
_placed = doc_show([{"t": 2.0, "slot": "ARM_R", "joints": {4: 30}},
                    {"t": _r["t"], "slot": "ARM_R", "joints": {4: 90}}])
check_true("a pose placed where the helper says passes the validator", not _placed.validate(LIMITS),
           _placed.validate(LIMITS))

# DANCE (DEC-2, item 1.4): named moves only, declared floor space, back where it started.
import cue as _cue  # noqa: E402
check_true("every library move returns the base to where it started",
           all(all(abs(n) < 1e-12 for n in _cue.dance_net(m)) for m in _cue.DANCE_MOVES))
check_doc("a dance with no declared floor space is refused",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "sway"}]), "declares no floor space")
check_doc("a declared dance passes",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "sway"}], dance={"floor_radius_m": 1.5}), None)
check_doc("a move outside the library is refused — a dance names a move, never a velocity",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "zoom", "fwd": 1}], dance={"floor_radius_m": 1.5}),
          "not in the library")
check_doc("a free-form velocity on a dance cue is refused",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "sway", "fwd": 1}], dance={"floor_radius_m": 1.5}),
          "unknown key 'fwd'")
check_doc("a move longer than a gesture is refused",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "sway", "secs": 9}], dance={"floor_radius_m": 1.5}),
          "secs")
check_doc("amp above the autonomy ceiling is refused",
          doc_show([{"t": 1.0, "slot": "DANCE", "move": "sway", "amp": 1.5}], dance={"floor_radius_m": 1.5}),
          "amp")
check_doc("too much total open-loop base time is refused",
          doc_show([{"t": float(i * 4), "slot": "DANCE", "move": "rock", "secs": 4} for i in range(9)],
                   dance={"floor_radius_m": 1.5}), "cap on open-loop base motion")
check_doc("DRIVE is still not a slot",
          doc_show([{"t": 1.0, "slot": "DRIVE", "fwd": 1}], slots=["DRIVE"]), "DRIVE is not available")
_d = doc_show([{"t": 1.0, "slot": "DANCE", "move": "box", "secs": 4}], dance={"floor_radius_m": 0.5}).analyse(LIMITS)
check_true("a move that may stray past the declared radius warns (an estimate), and stays valid",
           not _d["problems"] and any("floor radius" in w.msg for w in _d["warnings"]),
           [w.msg for w in _d["warnings"]] + [q.msg for q in _d["problems"]])

# requires: (workplan 0.7) — the preflight is built from it, so it is validated.
check_doc("a typo in rc_authority is caught",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 2}],
                   requires={"rc_authority": "AUOT"}), "is not a CH5 mode")
check_doc("an old CH5 name is caught, saying what it is called now",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 2}],
                   requires={"rc_authority": "RC"}), "called MANUAL now")
check_doc("a show that moves must run in AUTO",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 2}],
                   requires={"rc_authority": "MANUAL"}), "runs in AUTO")
check_doc("a show that moves with no requires at all is caught",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 2}], requires={}), "runs in AUTO")
check_doc("a show using the arms must declare arms: active",
          doc_show([{"t": 3.0, "slot": "ARM_R", "joints": {4: 20}}],
                   requires={"rc_authority": "AUTO"}), "arms: active")
check_doc("a show using the face must declare tab5: true",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SOFT"}], requires={}), "tab5: true")
check_doc("an unknown requires key is caught",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SOFT"}],
                   requires={"tab5": True, "lidar": "on"}), "unknown key 'lidar'")
check_doc("a face-and-sound show needs no rc_authority",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SOFT"},
                    {"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}],
                   requires={"tab5": True}), None)

# Audio routing (DEC-5 / DEC-11, item 1.5): where each sound plays, and whether it can.
_m = {"file": "x.wav", "bpm": 120}
check_doc("music defaults to the tablet, which can play it",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SOFT"}], music=_m), None)
check_doc("music routed to the Tab5 is an error, not silence (it has no path for files yet)",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SOFT"}],
                   music={**_m, "output": "tab5"}), "cannot play a music file")
check_doc("an unknown output is caught",
          doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend", "output": "speaker"}]),
          "not one of")
check_doc("a Tab5 earcon sent to the tablet, with no file for it, is an error",
          doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend", "output": "tablet"}]),
          "cannot play 'attend'")
_r = doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}]).export(LIMITS, None)
check_true("an earcon with no output given defaults to the Tab5, the only device that has it",
           _r["tracks"]["SOUND"][0]["output"] == "tab5", _r["tracks"]["SOUND"])
_o = doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}], music=_m)
_o.audio_override = "tab5"
check_true("an operator override is re-checked: all audio on the Tab5 refuses a show with music",
           any("cannot play a music file" in q.msg for q in _o.analyse(LIMITS)["problems"]))
_o = doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "attend"}])
_o.audio_override = "thor"
_oa = _o.analyse(LIMITS)
check_true("... and routing an earcon to a Thor speaker is refused, saying how to fix it",
           any("cannot play 'attend'" in q.msg and "sounds/attend.wav" in q.msg for q in _oa["problems"]),
           [q.msg for q in _oa["problems"]])

# Arm speed (DEC-1): the AVERAGE over a segment is the gate; the eased peak (x PEAK_FACTOR) only
# warns until the controller's real profile is measured.
_a = doc_show([{"t": 3.0, "slot": "ARM_R", "joints": {4: 55}}]).analyse(LIMITS)
check_true("a move under the cap on average but not at its eased peak warns, and stays valid",
           not _a["problems"] and len(_a["warnings"]) == 1 and "may peak" in _a["warnings"][0].msg
           and "unsourced" in _a["warnings"][0].msg,
           [w.msg for w in _a["warnings"]] + [q.msg for q in _a["problems"]])
_a = doc_show([{"t": 8.0, "slot": "ARM_R", "joints": {4: 55}}]).analyse(LIMITS)
check_true("a move slow enough even at its eased peak does not warn", not _a["warnings"],
           [w.msg for w in _a["warnings"]])
_a = doc_show([{"t": 1.0, "slot": "ARM_R", "joints": {4: 120}}]).analyse(LIMITS)
check_true("a move over the cap on average is still a problem, not just a warning",
           any("cap 30, unsourced" in q.msg for q in _a["problems"]), [q.msg for q in _a["problems"]])

# The head turns at the firmware's 25 deg/s; a cue that arrives before the last move can finish is
# a WARNING (the show is still valid), not a problem.
_h = doc_show([{"t": 0.0, "slot": "HEAD", "pan": 90}, {"t": 1.0, "slot": "HEAD", "pan": 150},
               {"t": 1.5, "slot": "HEAD", "pan": 90}]).analyse(LIMITS)
check_true("a head move that cannot finish warns, and the show stays valid",
           not _h["problems"] and len(_h["warnings"]) == 1 and "short of 150" in _h["warnings"][0].msg,
           [w.msg for w in _h["warnings"]] + [q.msg for q in _h["problems"]])
_h = doc_show([{"t": 0.0, "slot": "HEAD", "pan": 90}, {"t": 1.0, "slot": "HEAD", "pan": 120},
               {"t": 3.0, "slot": "HEAD", "pan": 90}]).analyse(LIMITS)
check_true("a head move with time to finish does not warn", not _h["warnings"],
           [w.msg for w in _h["warnings"]])


def times(show, slot=None):
    return [round(c["t"], 6) for c in show.by_time() if slot is None or c["slot"] == slot]


print("\nformat v1: the musical grid")

s = doc_show([{"beat": 13, "slot": "HEAD", "pan": 60}], music=MUSIC)
check_true("beat 13 at 120 BPM, beat 1 at 0.5 s, resolves to 6.5 s", times(s) == [6.5], times(s))
s = doc_show([{"bar": "4.1", "slot": "HEAD", "pan": 60},
              {"bar": "4.3", "slot": "HEAD", "pan": 70}], music=MUSIC)
check_true("bar \"4.1\" is beat 13, and \"4.3\" two beats later", times(s) == [6.5, 7.5], times(s))
s = doc_show([{"beat": 13.5, "slot": "HEAD", "pan": 60}], music=MUSIC)
check_true("a fractional beat is the off-beat", times(s) == [6.75], times(s))
check_doc("beat without a music block is caught",
          doc_show([{"beat": 3, "slot": "HEAD", "pan": 60}]), "needs a music")
check_doc("two time forms on one cue is caught",
          doc_show([{"t": 1.0, "beat": 3, "slot": "HEAD", "pan": 60}], music=MUSIC), "exactly one")
check_doc("a bar past the bar's length is caught",
          doc_show([{"bar": "4.5", "slot": "HEAD", "pan": 60}], music=MUSIC), "out of range")
check_doc("sub-beats in bar notation are refused (write beat:)",
          doc_show([{"bar": "4.2.5", "slot": "HEAD", "pan": 60}], music=MUSIC), "write beat:")
check_doc("an absurd tempo is caught as a typo",
          doc_show([{"t": 1.0, "slot": "HEAD", "pan": 60}],
                   music={"bpm": 1200, "beats_per_bar": 4}), "sanity bound")
fast = dict(MUSIC, bpm=60)
a = doc_show([{"beat": 5, "slot": "HEAD", "pan": 60}, {"t": 5.0, "slot": "SOUND", "do": "play", "sound": "attend"}],
             music=MUSIC)
b = doc_show([{"beat": 5, "slot": "HEAD", "pan": 60}, {"t": 5.0, "slot": "SOUND", "do": "play", "sound": "attend"}],
             music=fast)
check_true("changing bpm moves a beat cue and leaves a t cue alone",
           times(a, "HEAD") == [2.5] and times(b, "HEAD") == [4.5]
           and times(a, "SOUND") == times(b, "SOUND") == [5.0],
           f"{times(a)} vs {times(b)}")
check_doc("a cue before the show starts is caught",
          doc_show([{"beat": -3, "slot": "HEAD", "pan": 60}], music=MUSIC), "before the show")
# v0 SILENTLY DROPPED a cue whose t was missing or not a number: by_time() filtered it out before
# the check that would have reported it could run. v1 reports it.
check_doc("a non-numeric t is reported, not silently dropped",
          doc_show([{"t": "3", "slot": "HEAD", "pan": 60}]), "non-numeric t")
check_doc("a future format version is refused, not misread",
          doc_show([{"t": 1.0, "slot": "HEAD", "pan": 60}], format=2), "reads format 1")

print("\nformat v1: clips")

WAVE = {"length_beats": 4, "cues": [
    {"beat": 1, "slot": "ARM_R", "joints": {7: 15}},
    {"beat": 2, "slot": "ARM_R", "joints": {7: -15}},
    {"beat": 3, "slot": "ARM_R", "joints": {7: 15}},
    {"beat": 4, "slot": "ARM_R", "joints": {7: 0}}]}
s = doc_show([{"bar": "3.1", "clip": "wave", "repeat": 2}], music=MUSIC, clips={"wave": WAVE})
check_true("a repeated clip expands in clip-local time, one length apart",
           times(s) == [4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0], times(s))
check_doc("... and the expanded show validates", s, None)
check_doc("a clip that is legal once is caught when time_scale makes it too fast",
          doc_show([{"bar": "3.1", "clip": "wave", "time_scale": 0.25}], music=MUSIC,
                   clips={"wave": WAVE}), "deg/s")
check_doc("repeating a clip with no declared length is caught",
          doc_show([{"t": 2.0, "clip": "w", "repeat": 2}],
                   clips={"w": {"cues": [{"t": 0, "slot": "HEAD", "pan": 60}]}}),
          "declares no length")
s = doc_show([{"bar": "3.1", "clip": "wave", "strength": 0.5}], music=MUSIC, clips={"wave": WAVE})
j7 = [c["joints"][7] for c in s.by_time()]
check_true("strength scales arm joints toward zero", j7 == [7.5, -7.5, 7.5, 0.0], j7)
check_doc("strength above 1 is refused — the dial only turns down",
          doc_show([{"bar": "3.1", "clip": "wave", "strength": 1.5}], music=MUSIC,
                   clips={"wave": WAVE}), "only turns")
check_doc("placing an unknown clip is caught",
          doc_show([{"t": 1.0, "clip": "nope"}, {"t": 1.0, "slot": "HEAD", "pan": 60}]),
          "no such clip")
check_doc("a clip may not place a clip",
          doc_show([{"t": 1.0, "clip": "outer"}],
                   clips={"outer": {"cues": [{"t": 0, "clip": "inner"}]},
                          "inner": {"cues": [{"t": 0, "slot": "HEAD", "pan": 60}]}},
                   slots=["HEAD"]), "no nesting")
check_doc("a clip's slots must be declared like any other",
          doc_show([{"bar": "3.1", "clip": "wave"}], music=MUSIC, clips={"wave": WAVE},
                   slots=["HEAD"]), "did not declare")
s = doc_show([{"t": 0.5, "slot": "HEAD", "pan": 60},
              {"bar": "3.1", "clip": "wave", "time_scale": 0.25}], music=MUSIC,
             clips={"wave": WAVE})
probs = [p for p in s.check(LIMITS) if "deg/s" in p.msg]
check_true("a problem inside a clip points at the PLACEMENT cue and names the clip",
           bool(probs) and probs[0].cue == 1 and probs[0].clip == "wave"
           and probs[0].instance == 0, probs)

print("\nformat v1: durations and spans")

check_doc("hold_s on an arm keyframe delays the next move, so the speed check sees less time",
          doc_show([{"t": 2.0, "slot": "ARM_R", "joints": {4: 30}, "hold_s": 2.0},
                    {"t": 5.0, "slot": "ARM_R", "joints": {4: 90}}]), "deg/s")
check_doc("... the same two keyframes without the hold pass",
          doc_show([{"t": 2.0, "slot": "ARM_R", "joints": {4: 30}},
                    {"t": 5.0, "slot": "ARM_R", "joints": {4: 90}}]), None)
check_doc("a lift owns its slot for its secs (v0 let a second command overlap it)",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 6},
                    {"t": 3.0, "slot": "LIFT", "do": "down", "secs": 2}]), "still busy")
check_doc("a stop inside a lift's travel is refused — shorten secs instead",
          doc_show([{"t": 1.0, "slot": "LIFT", "do": "up", "secs": 6},
                    {"t": 3.0, "slot": "LIFT", "do": "stop"}]), "still busy")
check_doc("two sounds overlapping are caught (the player refuses while busy)",
          doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "mode", "secs": 0.5},
                    {"t": 1.2, "slot": "SOUND", "do": "play", "sound": "heard"}]), "still busy")
check_doc("... and back to back is fine",
          doc_show([{"t": 1.0, "slot": "SOUND", "do": "play", "sound": "mode", "secs": 0.5},
                    {"t": 1.5, "slot": "SOUND", "do": "play", "sound": "heard"}]), None)
check_doc("an unknown key on a cue is caught, not ignored",
          doc_show([{"t": 1.0, "slot": "HEAD", "pan": 60, "hold": 3}]), "unknown key 'hold'")
check_doc("interp is reserved, and says why",
          doc_show([{"t": 3.0, "slot": "ARM_R", "joints": {4: 30}, "interp": "ease"}]),
          "linearly only")

print("\nformat v1: FACE, LIGHT, markers")

check_doc("an emote the face has passes",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "SLEEPY"}]), None)
check_doc("an emote the face does not have is caught (HAPPY is documented, not implemented)",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "emote", "name": "HAPPY"}]),
          "not one the face has")
check_doc("FACE has one verb",
          doc_show([{"t": 1.0, "slot": "FACE", "do": "theme", "name": "HALLOWEEN"}]),
          "must be emote")
check_doc("a light colour in range passes",
          doc_show([{"t": 1.0, "slot": "LIGHT", "do": "color", "rgb": [10, 20, 30], "br": 50},
                    {"t": 2.0, "slot": "LIGHT", "do": "release"}]), None)
check_doc("a light value the firmware would clamp is caught",
          doc_show([{"t": 1.0, "slot": "LIGHT", "do": "color", "rgb": [300, 0, 0]}]),
          "outside 0..255")
y = yaml.safe_load("cues: [{ t: 1.0, slot: LIGHT, do: off }]")
check_doc("YAML's off-means-false trap is named, not reported as a bad verb",
          doc_show(y["cues"]), "boolean")
check_doc("a named marker passes",
          doc_show([{"t": 1.0, "slot": "HEAD", "pan": 60}],
                   markers=[{"t": 2.0, "name": "disco_ball_on"}]), None)
check_doc("a marker that names wiring is refused",
          doc_show([{"t": 1.0, "slot": "HEAD", "pan": 60}],
                   markers=[{"t": 2.0, "name": "relay3_on"}]), "device address")

print("\nformat v1: the resolved timeline")

hello = load_show(os.path.join(HERE, "..", "shows", "hello.yaml"))
e1 = json.dumps(hello.export(LIMITS), sort_keys=True)
e2 = json.dumps(load_show(os.path.join(HERE, "..", "shows", "hello.yaml")).export(LIMITS),
                sort_keys=True)
check_true("two exports of the same show are byte-identical", e1 == e2)
tl = json.loads(e1)
arm_l = tl["tracks"]["ARM_L"]
check_true("arm keyframes carry the full pose, outward and controller-signed",
           arm_l[0]["pose"]["2"] == 20 and arm_l[0]["pose_signed"]["2"] == -20
           and arm_l[0]["pose"]["7"] == 0, arm_l[0])
check_true("an arm keyframe's span starts where the previous one ended",
           arm_l[1]["start"] == arm_l[0]["end"] and arm_l[0]["start"] == 0.0,
           [(k["start"], k["end"]) for k in arm_l[:2]])
check_true("every track is sorted by start",
           all([e["start"] for e in v] == sorted(e["start"] for e in v)
               for v in tl["tracks"].values()))
check_true("the lift's span is its travel", tl["tracks"]["LIFT"][0]["end"] == 12.0,
           tl["tracks"]["LIFT"][0])
bad = doc_show([{"t": 1.0, "slot": "ARM_R", "joints": {4: 120}}]).export(LIMITS)
check_true("an invalid show exports valid: false, with located problems",
           bad["valid"] is False and bad["problems"][0]["cue"] == 0, bad["problems"])

print("\nformat v1: capture (teaching by hand)")

out = joint_state_to_outward(["openarm_left_joint2", "openarm_left_joint4", "openarm_right_joint2"],
                             [math.radians(-20), math.radians(55), math.radians(20)], "left")
check_true("a measured LEFT pose is un-mirrored into the outward sense",
           out == {2: 20.0, 4: 55.0}, out)
out = joint_state_to_outward(["openarm_right_joint2"], [math.radians(20)], "right")
check_true("... and a right pose is left alone", out == {2: 20.0}, out)

print("\nthe real shows")
for name in ("hello.yaml", "beat-demo.yaml"):
    show = load_show(os.path.join(HERE, "..", "shows", name))
    problems = show.validate(LIMITS)
    print(f"  {'ok  ' if not problems else 'FAIL'}  shows/{name} validates")
    for p in problems:
        print("        ", p)
    if problems:
        fails.append(f"shows/{name} does not validate")

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
