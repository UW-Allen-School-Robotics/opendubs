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
# LibYAML's parser when PyYAML has it: the same documents, several times faster. Every palette listing
# re-reads every show, and the pure-Python parser made that slower than the dashboard waits for.
_SHOW_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library  # noqa: E402  the show library's index (plan 4.6), beside this file

FORMAT_VERSION = 1
EXPORT_SCHEMA = "opendubs.choreo.timeline/1"

# ---- what a robot will accept -------------------------------------------------------------
SLOTS = ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT", "DANCE", "DRIVE",
         "DEVICE", "VIDEO")
# DRIVE (the user's composer item #4, 2026-10-11) is TWIN-ONLY: shows may be authored with it and
# the twin drives the base, but the runner refuses any show that uses it on the robot, because the
# robot cannot yet say where it is (opendubs2 reported y_mm NaN with a climbing failure count) —
# "drive 1 m forward" is a claim open-loop wheels cannot keep. Earning it on the robot is still
# odometry's job (choreo-format-v1.md 12).

ARM_SLOTS = ("ARM_L", "ARM_R")

# ---- requires: what the runner's preflight checks before anything moves (workplan 0.7) ---------
# CH5's three positions, named per workplan DEC-9: STANDBY (powered, nothing commanded), MANUAL
# (a human commands live), AUTO (software commands; shows run here — standing rule 1).
CH5_MODES = ("STANDBY", "MANUAL", "AUTO")
CH5_OLD_NAMES = {"OFF": "STANDBY", "RC": "MANUAL", "RC_MANUAL": "MANUAL", "SAFE_IDLE": "STANDBY",
                 "AUTONOMOUS": "AUTO", "AUTONOMOUS_CONTROL": "AUTO", "MANUAL_CONTROL": "MANUAL"}
REQUIRES_KEYS = ("arms", "tab5", "rc_authority")
MOTION_SLOTS = ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "DANCE", "DRIVE")
TAB5_SLOTS = ("HEAD", "FACE", "DEVICE")   # the pan/tilt head, the face and its volume/brightness

# ---- DEVICE: the head's media volume and screen brightness (head protocol v5) -------------------
# Show-level `device: {volume, brightness}` sets both at the start; a DEVICE cue changes one, with an
# optional fade. Levels are 0..1. VOLUME is the head's own media volume (what the show audio plays
# at), not a gain in the player. The head will not dim below its own floor (bright_min), so a lower
# brightness is warned about: it would be raised, not obeyed. Every show end hands both back (IDLE).
DEVICE_DOS = ("volume", "brightness")
DEVICE_FADE_MAX_S = 30.0
DEVICE_BRIGHT_LOW = 0.15

# ---- DANCE: bounded open-loop base motion (workplan DEC-2, item 1.4; format spec 12) ----------
# Not DRIVE. A DANCE cue names a move from this library and nothing else: no free-form velocity is
# expressible. Each move is a fixed open-loop profile — segments of (fraction of the move's
# duration, forward, strafe, rotate), each axis in "dance units" where 1.0 is the base's AUTONOMY
# CEILING (kAutoMaxUnit = 0.35 of full command, firmware/rc_serial_probe main.cpp), which CH8
# scales further in a human's hand. Every move returns the base to where it started BY
# CONSTRUCTION (every axis integrates to zero; the validator re-checks it), so only open-loop
# drift can accumulate. Validated now; executes ONLY in the twin until the robot test (plan 6.6).
DANCE_MOVES = {
    "sway":    [(0.25, 0, +1, 0), (0.5, 0, -1, 0), (0.25, 0, +1, 0)],            # side to side
    "rock":    [(0.25, +1, 0, 0), (0.5, -1, 0, 0), (0.25, +1, 0, 0)],            # forward and back
    "twist":   [(0.25, 0, 0, +1), (0.5, 0, 0, -1), (0.25, 0, 0, +1)],            # turn and return
    "shuffle": [(0.125, 0, +1, 0), (0.25, 0, -1, 0), (0.25, 0, +1, 0), (0.25, 0, -1, 0),
                (0.125, 0, +1, 0)],                                               # two quick sways
    "box":     [(0.25, +1, 0, 0), (0.25, 0, +1, 0), (0.25, -1, 0, 0), (0.25, 0, -1, 0)],
}
DANCE_SECS = (0.5, 4.0)          # SANITY: one move is a gesture, not a journey
DANCE_DEFAULT_SECS = 2.0
DANCE_TOTAL_MAX_S = 30.0         # SANITY: the whole show's open-loop base time
FLOOR_RADIUS_SANITY_M = (0.5, 10.0)
# PLACEHOLDERS, from the simulator, NOT measured on the robot: base speed at full command
# (WHEEL_MAX_RADS 10 rad/s x the measured 70 mm wheel radius, sim/scene.py) and the matching
# mecanum turn rate. Used only to ESTIMATE how far a move strays, for a warning against the
# declared floor radius. The firmware's kAutoMaxUnit is real; these speeds are not.
BASE_M_PER_S_FULL = 0.70
BASE_RAD_PER_S_FULL = 1.40
AUTO_MAX_UNIT = 0.35
DRIVE_V_MAX = AUTO_MAX_UNIT * BASE_M_PER_S_FULL                  # m/s, PLACEHOLDER-based
DRIVE_W_MAX_DEG = math.degrees(AUTO_MAX_UNIT * BASE_RAD_PER_S_FULL)  # deg/s, PLACEHOLDER-based
# APPROXIMATE: the chassis box's half-diagonal (description/opendubs/dimensions.yaml: x -32..612 mm,
# y +-324.5 mm -> 0.457 m). The arms are NOT counted. The floor radius is clear space around the
# robot, so a move needs footprint + how far it strays.
ROBOT_FOOTPRINT_RADIUS_M = 0.46


# ---- DRIVE: a relative move of the base, TWIN-ONLY (see SLOTS) --------------------------------
# A cue moves the base by forward_m / left_m and turns it by turn_deg, over secs, in the robot's
# own frame as it is at the cue — no velocities, no room coordinates. Its speed is bounded by the
# same autonomy ceiling DANCE uses, at the same PLACEHOLDER base speeds: a move that would need more
# is refused with the time it needs.
DRIVE_SECS = (0.5, 10.0)


def drive_path(cues: list, step_s: float = 0.05):
    """[(t from the first cue's start, x, y, yaw rad)] of the base under DRIVE cues given as
    [(start_t, cue)], in the show's starting frame. Each move: constant body velocity and turn rate
    over its secs, in the frame it started in turning with it."""
    x = y = yaw = 0.0
    pts = [(0.0, 0.0, 0.0, 0.0)]
    for start, c in sorted(cues, key=lambda sc: sc[0]):
        secs = float(c.get("secs", 1.0))
        fwd, left = float(c.get("forward_m", 0) or 0) / secs, float(c.get("left_m", 0) or 0) / secs
        w = math.radians(float(c.get("turn_deg", 0) or 0)) / secs
        n = max(1, int(secs / step_s))
        dt = secs / n
        for k in range(n):
            mid = yaw + w * dt / 2
            x += (fwd * math.cos(mid) - left * math.sin(mid)) * dt
            y += (fwd * math.sin(mid) + left * math.cos(mid)) * dt
            yaw += w * dt
            pts.append((start + (k + 1) * dt, x, y, yaw))
    return pts


def dance_net(move: str) -> tuple:
    """(forward, strafe, rotate) a move integrates to, in dance-unit fractions — zero by design."""
    segs = DANCE_MOVES[move]
    return tuple(sum(f * v[i] for f, *v in segs) for i in range(3))


def dance_excursion_m(move: str, secs: float, amp: float = 1.0) -> float:
    """Farthest the base strays from where the move started, at the PLACEHOLDER speeds."""
    v = AUTO_MAX_UNIT * amp * BASE_M_PER_S_FULL
    x = y = far = 0.0
    for frac, fwd, strafe, _rot in DANCE_MOVES[move]:
        x, y = x + fwd * v * frac * secs, y + strafe * v * frac * secs
        far = max(far, math.hypot(x, y))
    return far

MARGIN_DEG = 5.0
# MEASURED, 2026-10-08 (workplan 6.1; speed_test.py on opendubs2's right arm, the left a mirror of
# it): 75% of the speed at which a joint first fell more than 5 deg behind its path in steady motion,
# or was stopped near its trip. J1/J2 failed at 120 (the shoulder fell 17 deg behind), J3 at 240
# (it overran to 252, near its 312 trip), J4-J7 at 180. Effort stayed under 60% of every limit: the
# limit is how closely the joints follow, not torque. Were 25/30/35/30/35/60/70, unsourced.
# Adopted for both arms by the user, 2026-10-08.
SPEED_CAP_DEG_S = {1: 90, 2: 90, 3: 180, 4: 135, 5: 135, 6: 135, 7: 135}
# INTERIM, agreed 2026-10-07 (workplan DEC-1, item 1.1). The AVERAGE speed over a segment is the
# hard gate. Its average times this factor — the peak of a minimum-jerk (quintic) move, the worst
# profile the arm controller plausibly uses — is checked as a WARNING: the real profile is
# unmeasured (experiment T4, workplan 6.1), and failing shows that have run on the robot on an
# unmeasured factor would be the validator claiming more than it knows. When the profile is
# measured, this becomes its peak-to-average ratio (1.0 for constant speed, 1.5 for a cubic) and
# the peak becomes the gate.
PEAK_FACTOR = 1.875
# The arm controller's own velocity TRIPS — the URDF joint limits, read from the arm stack by the
# arms plan on 2026-10-08. Past one the controller freezes the arm and aborts the whole goal, so a
# show stops mid-routine. Nothing below the trajectory clamps speed; these are trips, not limits.
# A move whose PEAK (average x PEAK_FACTOR) reaches one is refused outright — not caution: that move
# would stop the show every time. Matters once the speed caps rise (workplan 6.1).
TRIP_DEG_S = {1: 960, 2: 960, 3: 312, 4: 312, 5: 1200, 6: 1200, 7: 1200}

# Measured on opendubs2, per side. Both open on a POSITIVE command, but their zeros sit at
# opposite physical ends, so one range cannot serve both.
# Grippers are written as `open: 0..1` (0 closed, 1 open), the same on both sides (workplan DEC-4).
# The per-side mapping to controller units is measured on each robot and lives ON THE ROBOT
# (grippers.py); none of it is in this repository.
GRIP_SLOTS = ("GRIP_L", "GRIP_R")
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

# Where sound plays (workplan DEC-5 / DEC-11, item 1.5). A show may say, for its music and for each
# sound; the operator may override at run time (Show.audio_override), and the show is re-checked
# against the override — a sound an output cannot play is an ERROR, never silence.
AUDIO_OUTPUTS = ("tablet", "tab5", "thor")
# What a sound IS (notes/design/choreo-audio.md, the user's decision 2026-10-20): MUSIC (the soundtrack,
# background, effects) plays from the projector's speaker when a projector is ready; DIALOGUE (scripted
# lines, the voice assistant) always from the tablet. Unmarked is dialogue, so speech never reaches the
# projector by default — a narration track is dialogue until someone marks it music.
AUDIO_ROLES = ("music", "dialogue")
DEFAULT_AUDIO_ROLE = "dialogue"
DEFAULT_AUDIO_OUTPUT = "tablet"          # DEC-5: the tablet, unless the show or operator says otherwise
# What each output can play TODAY. The Tab5 has its six built-in earcons and no path for files yet;
# the tablet and a Thor speaker play files. No Thor speaker is fitted yet (a notice says so).
CAN_PLAY = {"tablet": ("file",), "tab5": ("earcon",), "thor": ("file",)}
# The head turns toward each target at this rate, each axis independently: the Tab5 firmware's own
# slew limit (kSlewDps in firmware/tab5_idf/app_face/pantilt.cpp), which no caller can override.
# Used to WARN when a head cue arrives before the previous move can have finished; sim_preview.py
# uses the same constant to show where the head actually is. 70 since Tab5 PR #81 (2026-10-08, the
# user's call: a nod must land on a beat; was 25), measured on opendubs2 at 74 deg/s effective.
HEAD_SLEW_DPS = 70.0
HEAD_REACH_TOL_DEG = 1.5       # the firmware's deadband (kDeadbandDeg): closer than this is "there"
LIFT_MAX_S = 8.0               # a single continuous lift command

# FACE: the emote vocabulary as it exists in firmware, and nothing else. Sourced from kEmotes[]
# in firmware/tab5_idf/app_face/app_face.cpp (feat/tab5-idf-subtractive, 12f5845). Five, not the
# six or seven the notes describe. The head takes them as `EMOTE <name>`, case-insensitively.
FACE_EMOTES = ("NEUTRAL", "ALERT", "SQUINT", "SOFT", "SLEEPY")
# Themes compiled into the face (theme::name() in the same file; "none" is the enum's own name for
# "plain", and the head accepts both). Others are AUTHORED: installed on a head at run time by its
# `CFG <url>` verb, so the validator cannot know them — a show lists the ones it uses under
# `themes:` (plan 1.10), exactly as it lists local sound files under `sounds:`.
FACE_THEMES = ("plain", "none", "halloween")
# Eye skins (plan 4.6, head protocol v2 `SKIN <name>`). The skins the face draws in CODE — one name
# each; `default` is the standard eyes and an ordinary SKIN argument. The single source is CODE_SKINS
# in software/face-web/engine/skins/index.js (the tablet lane); a test keeps this in step. Any other
# skin is a LIBRARY item (library.py), checked against the library index on this machine. A Tab5
# answers SKIN with ERR unknown; the tablet face falls back to `default` for a name it lacks, and a
# reserved robot state (DEGRADED, E_STOP) always shows `default`.
CODE_SKINS = ("default", "ring", "fireworks", "monster_stitched", "monster_vampire", "monster_wolf",
              "monster_mummy", "monster_zombie", "monster_ghoul", "monster_lab")
# `IDLE` is the head's own word for "the robot's idle skin / theme" (head protocol v5: every show end
# sends SKIN IDLE and THEME IDLE), so no skin or theme may be called `idle`, and a show never names it.
IDLE_NAME = "idle"
# Eye MOVES (head protocol v4, `MOVE <name>`, tablet face only): one-shot gestures that play over the
# held emote and return to it. A move's cue time is its PEAK (a wink's lid fully shut, the bottom of a
# bob), so it lands on the beat the author wrote: the line is SENT early by the face's published
# receipt-to-peak offset (exported as `send_t`). name: (peak_s, dur_s), MEASURED by the face, from
# MOVES in software/face-web/engine/face.js (Face & HRI); tests/test_face_moves.py keeps them in step
# with it and with head_protocol.MOVES. A new move replaces one still playing; it never queues.
# `sparkle` is the one a skin may ignore (an asset skin cannot draw it): never make a show depend on it.
FACE_MOVES = {
    "wink_l": (0.139, 0.420), "wink_r": (0.139, 0.420),
    "blink2": (0.333, 0.520),                 # its SECOND shut: a double blink reads on its second
    "look_l": (0.231, 0.700), "look_r": (0.231, 0.700),
    "roll": (0.148, 0.760), "bob": (0.138, 0.460), "sparkle": (0.124, 0.560),
    "side_eye_l": (0.198, 0.900), "side_eye_r": (0.198, 0.900),   # a sideways glance, lids part-lowered
    "to_camera": (0.164, 0.820),              # ARRIVES at centre (overrides a tracked gaze for its 0.82 s)
    "double_take": (0.632, 1.150),            # the longest lead: its peak is the snap back, wide
    "startle": (0.084, 0.700),
}

# LIGHT: the PRISM base colour, as the StamPLC's `LED_COLOR r g b br` parser takes it
# (firmware/rc_serial_probe/src/main.cpp). The firmware CLAMPS; the validator REJECTS, so an
# out-of-range value is a visible mistake rather than a quietly different colour.
LIGHT_RGB_MAX = 255
LIGHT_BR_MAX = 100
# The state light's reserved colours (workplan 1.8, decided 2026-10-07): a show may not paint the
# PRISM anything that reads as E-STOP red or DEGRADED amber — the light is how a bystander tells
# whether the robot is safe, and the face's themes are barred the same way (face-config.md,
# Reserved). Hues are the PRISM's own, from software/kiosk-dashboard/backend/state_out.py _MAP:
# E_STOP (255, 35, 35) -> 0 deg, DEGRADED (240, 150, 40) -> 33 deg.
LIGHT_RESERVED_HUES = {"E-STOP red": 0.0, "DEGRADED amber": 33.0}
# SANITY, not measured on the light: within this many degrees of hue, and at least this saturated,
# a colour reads as the reserved one. Whites, greys and pastels are not saturated enough to.
LIGHT_RESERVED_BAND_DEG = 20.0
LIGHT_RESERVED_MIN_SAT = 0.35


def light_reserved_clash(rgb) -> str | None:
    """The reserved state colour an RGB reads as, or None."""
    import colorsys
    h, s, _v = colorsys.rgb_to_hsv(*(max(0, min(255, c)) / 255.0 for c in rgb))
    if s < LIGHT_RESERVED_MIN_SAT:
        return None
    hue = h * 360.0
    for name, ref in LIGHT_RESERVED_HUES.items():
        if abs((hue - ref + 180.0) % 360.0 - 180.0) <= LIGHT_RESERVED_BAND_DEG:
            return name
    return None

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
    # reach / point_at: an IK target (ik.py); solved / solved_for: the joint angles compiled for it; planner: motion planner backend
    "ARM_L": {"joints", "pose", "reach", "point_at", "elbow", "standoff", "solved", "solved_for", "elbow_off", "planner"},
    "ARM_R": {"joints", "pose", "reach", "point_at", "elbow", "standoff", "solved", "solved_for", "elbow_off", "planner"},
    # pos is KNOWN so that the old metres form is refused by name, with what replaced it
    "GRIP_L": {"do", "open", "effort", "pos"}, "GRIP_R": {"do", "open", "effort", "pos"},
    "HEAD": {"pan", "tilt"},
    "LIFT": {"do", "secs", "ack_pinch"},     # ack_pinch is KNOWN so that it is refused by name
    "SOUND": {"do", "sound", "secs", "output", "role"},
    "FACE": {"do", "name"},
    "LIGHT": {"do", "rgb", "br"},
    "DANCE": {"move", "secs", "amp"},
    "DRIVE": {"forward_m", "left_m", "turn_deg", "secs"},
    "DEVICE": {"do", "level", "fade_s"},
    "VIDEO": {"do", "video", "from_s", "secs", "end", "output"},
}
# VIDEO (notes/design/choreo-video.md): a library video (kind `video`) on the projector, on the show's
# clock. `play` starts it at the cue's time from `from_s` into the file, for `secs` (default: to its
# end), then shows `end`: black (default) or hold (its last frame). `stop` ends whatever plays. The
# output is the cue's `output`, else the show's `video: {output}`, else the robot's default; the
# operator may override it at play time. One video at a time: one projector.
VIDEO_OUTPUTS = ("cast", "hdmi", "off")
DEFAULT_VIDEO_OUTPUT = "cast"           # the user's decision, 2026-10-20: casting first
VIDEO_ENDS = ("black", "hold")
VIDEO_KEYS = {"output", "required"}     # the show-level `video:` block
PLACEMENT_KEYS = set(TIME_KEYS) | {"clip", "repeat", "strength", "time_scale", "mirror"}
# A mirrored placement plays a clip on the other side (workplan 1.12): arm and gripper slots swap,
# and the head's pan reflects about centre. Arm joints are written in each arm's OWN outward sense,
# so the same numbers already mean the mirror-image gesture on the other arm.
MIRROR_SLOT = {"ARM_L": "ARM_R", "ARM_R": "ARM_L", "GRIP_L": "GRIP_R", "GRIP_R": "GRIP_L"}
CLIP_KEYS = {"cues", "length_s", "length_beats", "notes"}
TOP_KEYS = {"format", "id", "name", "slots", "requires", "music", "notes", "clips", "markers",
            "cues", "dance", "sounds", "themes", "palette", "timeline_notes", "clearance",
            "start_countdown_s", "device", "video"}
# How a run starts (the runner's start modes): after the GO, a countdown the operator can see on the
# face, before anything moves. A show may set its own length; a run may override it.
START_COUNTDOWN_DEFAULT_S = 5.0
START_COUNTDOWN_MAX_S = 60.0
# Timeline notes (composer plan #5): requests to whoever edits the show next — often an agent —
# anchored to a time range and optionally to slots. NON-EXECUTABLE: the runner never reads them, the
# validator checks only their shape (as warnings: a typo in a note never stops a show), and they
# reach the export only when the composer asks for them (export(with_notes=True)).
NOTE_KEYS = {"id", "at", "to", "slots", "text", "status", "reply", "by"}
NOTE_STATUS = ("open", "done", "declined")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# A marker that reads as wiring has put per-unit hardware into a committed show (workplan 5.6).
ADDRESS_RE = re.compile(r"^(relay|r\d|gpio|pin|ch\d|udp|di\d|do\d|servo\d)")

# What a show may use that NOTHING ON THE ROBOT EXECUTES YET. Valid is not executable; these are
# reported as notices on every validation so nobody mistakes the one for the other.
NOT_EXECUTABLE = {
    "DRIVE": "DRIVE runs only in the twin until odometry is trusted — the runner REFUSES this show on "
             "the robot (format 12); the distances shown use placeholder base speeds",
    "DANCE": "DANCE is valid but runs only in the twin until the robot floor test (workplan "
             "6.6): open loop, and the distances shown use placeholder base speeds",
    "LIGHT": "LIGHT plays on the robot only where the runner is armed for it (--light dashboard): "
             "layered over the state colour by the dashboard, a reserved state always winning, and "
             "colour changes closer than ~0.33 s merged (the strip shares the drive ESCs' I2C)",
    "VIDEO": "VIDEO plays on the robot only where the runner has a video output (--video cast,hdmi) and "
             "the projector is ready; otherwise the show runs without it, saying so (a show may say "
             "video: {required: true} to be refused instead)",
    "SOUND": "SOUND: files routed to the tablet play there (workplan 4.2); the Thor has no speaker "
             "yet; Tab5 earcons play in the twin only — the head's audio is off by decision "
             "(2026-10-08) and the robot's runner holds earcons back",
}


# ---- the self-collision sweep's stamp (sweep.py) — the robot gate ------------------------------
# A show that moves an arm must carry `clearance:` from sweep.py: the closest any part of the robot
# came to any other along the whole show, on the path the robot plays, for the show's motion AS IT
# IS NOW (the motion hash). Required wherever a show meets the robot (require_sweep — the runner,
# the composer, `cue.py validate`); at least SWEEP_MIN_MM or the show is refused.
SWEEP_MIN_MM = 30.0
SWEEP_SLOTS = ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "DANCE", "DRIVE")


def motion_hash(events: list) -> str:
    """A hash of everything that MOVES in an analysis — what the sweep checked. A face, a light or a
    sound does not change it; any arm, gripper, head, lift or base change does."""
    keep = []
    for e in events:
        if e.get("slot") not in SWEEP_SLOTS or e.get("bad"):
            continue
        keep.append([e["slot"], round(float(e.get("t", 0.0)), 4), round(float(e.get("start", e.get("t", 0.0))), 4),
                     round(float(e.get("end", e.get("t", 0.0))), 4), _jsonable(e.get("pose")),
                     {k: _jsonable(v) for k, v in (e.get("cue") or {}).items() if k not in ("solved_for",)}])
    keep.sort(key=lambda r: (r[0], r[1], json.dumps(r[5], sort_keys=True, default=str)))
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


def slot_requirements(slot: str) -> dict:
    """What `requires:` must say for a show that uses this slot — the same rules the validator
    applies (_check_requires), so the composer can add them when it lists a part."""
    out = {}
    if slot in MOTION_SLOTS:
        out["rc_authority"] = "AUTO"
    if slot in ("ARM_L", "ARM_R", "GRIP_L", "GRIP_R"):
        out["arms"] = "active"
    if slot in TAB5_SLOTS:
        out["tab5"] = True
    return out


# An arm target's optional `elbow:` (pose_ref.py copies arm SHAPE, not just the hand): a secondary
# goal the solver meets as closely as the hand allows. Further off than this is a warning.
ELBOW_WARN_M = 0.05


def ik_target_key(c: dict):
    """`reach:head_screen` / `point_at:[0.5, -0.2, 1.1]` (+ `elbow:[...]`) — the target an arm cue
    names, as `solved_for` records it (ik.py); None for a cue with no target."""
    def form(mode, v):
        if isinstance(v, (list, tuple)):
            try:
                return f"{mode}:[" + ", ".join(f"{float(x):g}" for x in v) + "]"
            except (TypeError, ValueError):
                return f"{mode}:{v!r}"
        return f"{mode}:{v}"
    for mode in ("reach", "point_at"):
        if mode in c:
            return form(mode, c[mode]) + ("+" + form("elbow", c["elbow"]) if "elbow" in c else "")
    return None


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
        self.audio_override = None      # the operator's run-time output choice; None = as written
        self.video_override = None      # ... and the projector output's (cast | hdmi | off)

    # ---- time ------------------------------------------------------------------------------
    def _music(self, P):
        """The music block, checked. Returns None, or a dict with beat_s resolved."""
        m = self.doc.get("music")
        if m is None:
            return None
        if not isinstance(m, dict):
            P(Problem("music: must be a mapping (song or file, bpm, offset_s, beats_per_bar)"))
            return None
        out = {"file": m.get("file"), "song": m.get("song"), "bpm": m.get("bpm"),
               "offset_s": m.get("offset_s", 0.0), "beats_per_bar": m.get("beats_per_bar", 4)}
        for k in m:
            if k not in out and k not in ("output", "role"):
                P(Problem(f"music: unknown key {k!r} (known: song, file, bpm, offset_s, beats_per_bar, output, role)"))
        out["role"] = m.get("role", DEFAULT_AUDIO_ROLE)
        if out["role"] not in AUDIO_ROLES:
            P(Problem(f"music: role {out['role']!r} is not {' or '.join(AUDIO_ROLES)} (unmarked is dialogue)"))
        written = m.get("output", DEFAULT_AUDIO_OUTPUT)
        if written not in AUDIO_OUTPUTS:
            P(Problem(f"music: output {written!r} is not one of {', '.join(AUDIO_OUTPUTS)}"))
        else:
            route = self.audio_override or written
            out["output"] = route
            if "file" not in CAN_PLAY[route]:
                P(Problem(f"music: the {route} cannot play a music file "
                          f"(it plays: {', '.join(CAN_PLAY[route])}) — choose tablet or thor"))
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
        # `song:` names a song in the show library (plan 4.6); `file:` is a file next to the show and
        # stays valid. One or the other: two would leave it unclear which plays.
        if out["song"] is not None and (not isinstance(out["song"], str) or not out["song"]):
            P(Problem("music: song must be a library song's id"))
            ok = False
        if out["song"] is not None and out["file"] is not None:
            P(Problem("music: give a song (from the library) or a file, not both"))
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
                mirror = c.get("mirror", False)
                if not isinstance(mirror, bool):
                    P(Problem(f"{what}: mirror must be true or false", *ref))
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
                        slot = lc.get("slot")
                        if mirror:
                            slot = MIRROR_SLOT.get(slot, slot)
                            if slot == "HEAD" and _num(f.get("pan")):
                                f["pan"] = 2 * HEAD_NEUTRAL_DEG - f["pan"]
                        t = base + lt * ts
                        events.append({
                            "t": t, "slot": slot, "fields": f, "seq": seq,
                            "src": {"index": i, "time": label, "clip": c["clip"], "instance": k,
                                    "local_index": j},
                            "where": f"cue at t={t:.2f}s (clip {c['clip']} #{k + 1}{', mirrored' if mirror else ''}, "
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
        self._unchecked_skins, self._skin_assets, self._lib, self._lib_error = set(), {}, False, None
        self._unchecked_song = None
        self._unchecked_videos = set()
        self._grip_effort = False
        vblock = self.doc.get("video")
        if vblock is not None:
            if not isinstance(vblock, dict):
                P(Problem("video: must be a mapping {output: cast|hdmi|off, required: true|false}"))
            else:
                for k in vblock:
                    if k not in VIDEO_KEYS:
                        P(Problem(f"video: unknown key {k!r} (known: {', '.join(sorted(VIDEO_KEYS))})"))
                if vblock.get("output") is not None and vblock["output"] not in VIDEO_OUTPUTS:
                    P(Problem(f"video: output {vblock['output']!r} is not one of {', '.join(VIDEO_OUTPUTS)}"))
                if "required" in vblock and not isinstance(vblock["required"], bool):
                    P(Problem("video: required must be true or false"))

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

        sounds = self.doc.get("sounds")
        if sounds is not None:
            if not isinstance(sounds, list) or not all(isinstance(x, str) and x for x in sounds):
                P(Problem(f"sounds: must be a list of sound names (files in {SOUND_DIR}/, no extension)"))
            elif self.path and os.path.isfile(self.path):
                missing = sorted(x for x in sounds if not self._sound_file(x))
                if missing:
                    notices.append(f"sound files not found in {SOUND_DIR}/ beside the show: "
                                   f"{', '.join(missing)} — the show still validates; supply them "
                                   "locally to hear them (they are not committed)")
        themes = self.doc.get("themes")
        if themes is not None:
            if not isinstance(themes, list) or not all(isinstance(x, str) and x for x in themes):
                P(Problem("themes: must be a list of authored face theme names"))
            else:
                if any(x.lower() == IDLE_NAME for x in themes):
                    P(Problem("themes: `idle` is reserved (THEME IDLE is the robot's idle theme) — no "
                              "theme may be called idle"))
                built_in = sorted(x for x in themes if x.lower() in FACE_THEMES)
                if built_in:
                    P(Problem(f"themes: {', '.join(built_in)} is built into the face — list only "
                              "authored themes here"))
                authored = sorted(x for x in themes if x.lower() not in FACE_THEMES)
                if authored:
                    notices.append(f"authored face theme(s) {', '.join(authored)}: install them on the "
                                   "head (CFG) before the show, or THEME answers 'unknown theme' and the "
                                   "face keeps the theme it has")
        if music and music.get("song"):
            idx = self._library()
            if idx is None:
                self._unchecked_song = music["song"]      # no library here: a notice, not a problem
            else:
                e = idx.get(music["song"])
                if e is None or e.get("kind") != "song":
                    P(Problem(f"music: song {music['song']!r} is not a song in the library at {library.root()}"))
                else:
                    self._skin_assets[music["song"]] = e.get("sha256")
        if music and music.get("file") and self.path and os.path.isfile(self.path):
            audio = os.path.join(os.path.dirname(os.path.abspath(self.path)), music["file"])
            if not os.path.isfile(audio):
                notices.append(f"music file {music['file']!r} not found next to the show — motion "
                               "still validates; generate or fetch the audio to hear it")

        if (music or {}).get("output") == "thor" or self.audio_override == "thor" or any(
                isinstance(c, dict) and c.get("output") == "thor" for c in (self.cues or [])
                if isinstance(self.cues, list)):
            notices.append("audio routed to the thor: no Thor speaker is fitted yet (DEC-5)")

        used = set()
        # per-slot arm state, so speed is measured between what actually happens
        arm_state: dict[str, dict[int, float]] = {}
        arm_free_at: dict[str, float] = {}
        owner_until: dict[str, float] = {}
        # DANCE: the floor space a show declares, and its total open-loop base time
        dance_doc = self.doc.get("dance")
        floor_r = None
        if dance_doc is not None:
            r = dance_doc.get("floor_radius_m") if isinstance(dance_doc, dict) else None
            if not (_num(r) and FLOOR_RADIUS_SANITY_M[0] <= float(r) <= FLOOR_RADIUS_SANITY_M[1]):
                P(Problem(f"dance: floor_radius_m must be a number of metres within "
                          f"{FLOOR_RADIUS_SANITY_M} — the clear radius a human confirms before it runs"))
            else:
                floor_r = float(r)
        dance_total = 0.0
        drive_cues = []
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
            if slot in GRIP_SLOTS:
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
            elif slot == "VIDEO":
                msgs = self._check_video(where, c)
                intrinsic = self._video_length(c) if not msgs else 0.0
            elif slot == "DANCE":
                msgs = self._check_dance(where, c)
                if not msgs:
                    secs = float(c.get("secs", DANCE_DEFAULT_SECS))
                    intrinsic = secs
                    dance_total += secs
                    far = dance_excursion_m(c["move"], secs, float(c.get("amp", 1.0)))
                    if floor_r is not None and far + ROBOT_FOOTPRINT_RADIUS_M > floor_r:
                        warnings.append(Problem(
                            f"{where}: {c['move']} may stray ~{far:.2f} m; with the base's own "
                            f"~{ROBOT_FOOTPRINT_RADIUS_M:g} m that needs more than the declared floor "
                            f"radius {floor_r:g} m (placeholder speeds, arms not counted — an estimate)",
                            *ref))
            elif slot == "DRIVE":
                msgs = self._check_drive(where, c)
                if not msgs:
                    intrinsic = float(c["secs"])
                    dance_total += intrinsic
                    drive_cues.append((t, c))
            elif slot == "FACE":
                msgs = self._check_face(where, c)
            elif slot == "DEVICE":
                msgs = self._check_device(where, c)
                if not msgs:
                    intrinsic = float(c.get("fade_s") or 0.0)
                    if c["do"] == "brightness" and c["level"] < DEVICE_BRIGHT_LOW:
                        warnings.append(Problem(_bright_low(where, c["level"]), *ref))
            else:
                msgs = self._check_light(where, c)
            for q in msgs:
                p(q)
            e["kind"] = "command"
            e["start"], e["end"] = t, t + max(intrinsic, hold)
            owner_until[slot] = e["end"]

        if ("DANCE" in used or "DRIVE" in used) and floor_r is None and dance_doc is None:
            P(Problem("the show moves the base but declares no floor space — add dance: { floor_radius_m: "
                      "<metres> }; the runner asks a human to confirm it before the base moves"))
        if dance_total > DANCE_TOTAL_MAX_S + 1e-9:
            P(Problem(f"the show moves the base for {dance_total:.1f}s in total (DANCE and DRIVE), over the "
                      f"{DANCE_TOTAL_MAX_S:g}s cap on open-loop base motion"))
        if drive_cues and floor_r is not None:
            far = max(math.hypot(px, py) for _, px, py, _ in drive_path(drive_cues))
            if far + ROBOT_FOOTPRINT_RADIUS_M > floor_r:
                warnings.append(Problem(
                    f"the DRIVE cues take the base ~{far:.2f} m from where the show starts; with its own "
                    f"~{ROBOT_FOOTPRINT_RADIUS_M:g} m that needs more than the declared floor radius "
                    f"{floor_r:g} m (placeholder speeds, open loop — an estimate)"))
        for msg in self._check_requires(used):
            P(Problem(msg))
        for msg in self._check_palette(used, arm_state, head_tgt, events, limits):
            P(Problem(msg))
        if "start_countdown_s" in self.doc and countdown_s(self.doc["start_countdown_s"]) is None:
            P(Problem(f"start_countdown_s: {self.doc['start_countdown_s']!r} must be a number of seconds, "
                      f"0 to {START_COUNTDOWN_MAX_S:g}"))
        dev = self.doc.get("device")
        if dev is not None:
            if not isinstance(dev, dict) or not dev:
                P(Problem("device: must be a mapping of volume / brightness, each 0..1"))
            else:
                for k, v in dev.items():
                    if k not in ("volume", "brightness"):
                        P(Problem(f"device: unknown key {k!r} (known: volume, brightness)"))
                    elif not (_num(v) and 0 <= v <= 1):
                        P(Problem(f"device: {k} {v!r} must be 0..1"))
                    elif k == "brightness" and v < DEVICE_BRIGHT_LOW:
                        warnings.append(Problem(_bright_low("device:", v)))
                if (dev.keys() & {"volume", "brightness"}) and self.requires.get("tab5") is not True:
                    P(Problem("requires: this show sets the head's volume or brightness (device:) — "
                              "add tab5: true, so the preflight checks the head is up"))
        undeclared = used - declared
        if declared and undeclared:
            P(Problem("cues use slots the show did not declare: " + ", ".join(sorted(undeclared))
                      + " — the runner's preflight is built from the declaration"))
        if self._grip_effort:
            notices.append("gripper `effort` has no physical effect on this robot: its grippers take a "
                           "position only, and how hard they squeeze is set by the calibrated closed "
                           "position (Arms lane, 2026-10-10)")
        if self._lib_error and (self._unchecked_song or self._unchecked_skins or self._unchecked_videos):
            # A library that is THERE but cannot be read: the runner would fail to find what the show
            # plays — say so here, rather than let it pass as "no library on this machine"
            P(Problem(f"the show library at {library.root()} cannot be read ({self._lib_error}) — this "
                      "show plays from it; on the robot the runner reads it as its own user"))
            self._unchecked_song, self._unchecked_skins, self._unchecked_videos = None, set(), set()
        if self._unchecked_song:
            notices.append(f"no show library at {library.root()}: song {self._unchecked_song!r} is unchecked "
                           "here — the Thor checks it against its library before every run")
        if self._unchecked_videos:
            notices.append(f"no show library at {library.root()}: video(s) "
                           f"{', '.join(sorted(self._unchecked_videos))} are unchecked here — the Thor "
                           "checks them against its library before every run")
        if self._unchecked_skins:
            notices.append(f"no show library at {library.root()}: skin(s) "
                           f"{', '.join(sorted(self._unchecked_skins))} are unchecked here — the Thor "
                           "checks them against its library before every run")
        notices += self._move_notices(events)
        if used & {"ARM_L", "ARM_R"}:
            for msg in self._check_hand_box(events, music):
                (P(Problem(msg)) if not msg.startswith("NOTICE ") else notices.append(msg[7:]))
        if getattr(self, "require_sweep", False) and used & {"ARM_L", "ARM_R"}:
            for msg, fatal in self._check_sweep(events):
                # "warn" (the composer while editing): a missing or stale stamp is a warning — saving
                # sweeps it — but a measured collision is a problem everywhere
                if fatal or self.require_sweep != "warn":
                    P(Problem(msg))
                else:
                    warnings.append(Problem(msg + " (the composer sweeps it when you save)"))
        tnotes = self._timeline_notes(music, warnings.append)
        return {"problems": problems, "warnings": warnings, "notices": notices, "events": events, "music": music,
                "markers": markers, "declared": declared, "used": used, "assets": dict(self._skin_assets),
                "timeline_notes": tnotes}

    def _check_sweep(self, events) -> list:
        st = self.doc.get("clearance")
        how = "save it in the composer, or run `python3 sweep.py stamp <show>` (needs MuJoCo)"
        """[(sentence, fatal)]: fatal is a MEASURED collision; the others say the stamp is missing or stale."""
        if not isinstance(st, dict) or not st.get("checked_for"):
            return [(f"this show moves the arms but has not been swept for collisions — {how}", False)]
        if str(st["checked_for"]).split("@")[0] != motion_hash(events):
            return [(f"the show's motion changed since it was swept for collisions — sweep it again: {how}", False)]
        mm = st.get("min_mm")
        if not _num(mm):
            return [(f"clearance: min_mm must be a number — sweep it again: {how}", False)]
        if float(mm) < SWEEP_MIN_MM:
            return [(f"the robot comes within {float(mm):.0f} mm of itself at {st.get('at_s')} s "
                     f"({st.get('between', 'two parts')}) — at least {SWEEP_MIN_MM:g} mm is required; change the "
                     "motion there", True)]
        return []

    def _timeline_notes(self, music, W) -> list:
        """The show's timeline notes, each resolved to seconds, or [] — their shape checked, as
        warnings. `at`/`to`: a string is "bar.beat" (like a cue's bar:), a number is seconds."""
        raw = self.doc.get("timeline_notes")
        if raw is None:
            return []
        if not isinstance(raw, list):
            W(Problem("timeline_notes: must be a list of notes"))
            return []
        out, seen = [], set()
        for i, n in enumerate(raw):
            where = f"timeline note {i + 1}"
            if not isinstance(n, dict):
                W(Problem(f"{where}: must be a mapping {{id, at, text, status, ...}}"))
                continue
            bad = sorted(set(map(str, n)) - NOTE_KEYS)
            if bad:
                W(Problem(f"{where}: unknown key(s) {', '.join(bad)} (known: {', '.join(sorted(NOTE_KEYS))})"))
            nid = n.get("id")
            if not isinstance(nid, str) or not nid or nid in seen:
                W(Problem(f"{where}: needs a unique id"))
            seen.add(nid)
            if not isinstance(n.get("text"), str) or not n["text"].strip():
                W(Problem(f"{where} ({nid}): has no text"))
            status = n.get("status", "open")
            if status not in NOTE_STATUS:
                W(Problem(f"{where} ({nid}): status {status!r} is not one of {', '.join(NOTE_STATUS)}"))
            slots = n.get("slots") or []
            if not isinstance(slots, list) or any(x not in SLOTS for x in slots):
                W(Problem(f"{where} ({nid}): slots must be a list of {', '.join(SLOTS)}"))
                slots = [x for x in slots if x in SLOTS] if isinstance(slots, list) else []

            def when(v, what):
                if v is None:
                    return None
                form = {"t": v} if _num(v) else {"bar": v}
                t, _ = self._time(form, music, False, f"{where} ({nid}) {what}", W, ())
                return t
            t0 = when(n.get("at"), "at")
            if n.get("at") is None:
                W(Problem(f"{where} ({nid}): needs at (a bar \"9.1\" or seconds)"))
            t1 = when(n.get("to"), "to")
            if t0 is not None and t1 is not None and t1 < t0:
                W(Problem(f"{where} ({nid}): to is before at"))
                t1 = None
            out.append({"index": i, "id": nid, "at": n.get("at"), "to": n.get("to"), "t0": t0,
                        "t1": t1 if t1 is not None else t0, "slots": slots, "text": n.get("text", ""),
                        "status": status if status in NOTE_STATUS else "open", "reply": n.get("reply"),
                        "by": n.get("by")})
        return out

    def _library(self):
        """The show library's index on this machine (library.py), read once per analysis."""
        if self._lib is False:
            try:
                self._lib = library.load_index()
            except Exception as e:  # noqa: BLE001  unreadable is NOT absent: said below, as a problem
                self._lib, self._lib_error = None, f"{type(e).__name__}: {e}"
        return self._lib

    def earliest_time(self, limits: dict, slot: str, joints: dict, t_want: float,
                      exclude_index: int | None = None) -> dict:
        """The soonest an arm can arrive at `joints` (outward sense) at or after `t_want`.

        Workplan DEC-3 / item 1.3: a taught pose has no timing of its own, so this places it. The
        arm is free once its last keyframe at or before `t_want` ends (its t plus hold_s); from
        there each joint needs |change| / cap seconds on AVERAGE (the hard gate) and PEAK_FACTOR
        times that to avoid the eased-peak warning. `exclude_index` leaves one authored cue out —
        the one being re-timed. Nothing is changed; this only answers."""
        if slot not in ARM_SLOTS:
            raise ValueError(f"earliest_time is for arm slots, not {slot!r}")
        doc = dict(self.doc)
        cues = list(self.cues if isinstance(self.cues, list) else [])
        if exclude_index is not None and 0 <= exclude_index < len(cues):
            cues.pop(exclude_index)
        doc["cues"] = cues
        a = Show(doc, self.path).analyse(limits)
        kfs = sorted((e for e in a["events"] if e.get("slot") == slot and e.get("kind") == "keyframe"),
                     key=lambda e: e["t"])
        m = -1 if slot == "ARM_L" else 1

        def solve(at):
            """Predecessor = the last keyframe at or before `at`; returns its index and the timing."""
            idx = max((i for i, e in enumerate(kfs) if e["t"] <= at + 1e-9), default=None)
            prev = kfs[idx] if idx is not None else None
            free_at = prev["end"] if prev else 0.0
            before = dict(prev["pose"]) if prev else {j: 0.0 for j in range(1, 8)}
            target = dict(before)
            for jn, v in joints.items():
                j = int(jn)
                target[j] = _deg(v) * (m if j in MIRRORED_JOINTS else 1)
            need = {j: abs(target[j] - before[j]) / SPEED_CAP_DEG_S[j] for j in range(1, 8)}
            return idx, free_at, before, target, need

        # Moving later can pass another keyframe, which then becomes the real predecessor: repeat
        # until the predecessor stops changing (at most once per keyframe).
        at = t_want
        for _ in range(len(kfs) + 1):
            idx, free_at, before, target, need_avg = solve(at)
            bind = max(need_avg, key=need_avg.get)
            legal = free_at + need_avg[bind]
            t = max(t_want, legal)
            if solve(t)[0] == idx:
                break
            at = t
        quiet = free_at + need_avg[bind] * PEAK_FACTOR
        if t > t_want + 1e-9:
            reason = (f"J{bind} changes {abs(target[bind] - before[bind]):.0f} deg and may average at "
                      f"most {SPEED_CAP_DEG_S[bind]} deg/s (measured cap), so it needs "
                      f"{need_avg[bind]:.1f}s from t={free_at:.2f}s")
        else:
            reason = "reachable at the time asked for"
        nxt = next((e for e in kfs if e["t"] > t + 1e-9), None)
        next_problem = None
        if nxt is not None:
            dt = nxt["t"] - t
            need_next = max(abs(nxt["pose"][j] - target[j]) / SPEED_CAP_DEG_S[j] for j in range(1, 8))
            if need_next > dt + 1e-9:
                next_problem = (f"the next {slot} keyframe at t={nxt['t']:.2f}s would then be too fast: "
                                f"it needs {need_next:.1f}s from here and would get {dt:.1f}s")
        return {"t": round(t, 3), "moved": t > t_want + 1e-9, "asked": round(t_want, 3),
                "legal_t": round(legal, 3), "quiet_t": round(quiet, 3), "free_at": round(free_at, 3),
                "binding_joint": bind, "reason": reason, "next_problem": next_problem}

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
        at_rest = False
        key = ik_target_key(c)
        if "planner" in c and c["planner"] not in ("spline", "cbirrt", "curobo"):
            return [f"{where}: {slot} planner {c['planner']!r} is not known (known: spline, cbirrt, curobo)"]
        if "elbow" in c and not ("reach" in c or "point_at" in c):
            return [f"{where}: {slot} elbow: goes with a reach: or point_at: target"]
        if key or "solved" in c:
            # An IK target (ik.py): its angles are compiled at authoring time and checked here like
            # any others, so the robot plays exactly what the twin previewed.
            kinds = [k for k in ("reach", "point_at", "joints", "pose") if k in c]
            if len(kinds) != 1 or not key:
                return [f"{where}: {slot} needs exactly one of joints, pose, reach or point_at "
                        f"(has {', '.join(kinds) or 'none'})"]
            if not isinstance(c.get("solved"), dict):
                return [f"{where}: {slot} {key} is not compiled — run `python3 ik.py compile <show>` "
                        "(the composer does it on save); a target inside a clip cannot be compiled yet"]
            if str(c.get("solved_for", "")).split("@")[0] != key:
                return [f"{where}: {slot} {key} was edited after its angles were solved (they are for "
                        f"{str(c.get('solved_for', '')).split('@')[0] or 'nothing'}) — compile again"]
            joints = c["solved"]
            off = c.get("elbow_off")
            if "elbow" in c and _num(off) and off > ELBOW_WARN_M and warn is not None:
                warn.append(f"{where}: {slot}'s elbow ends {off * 100:.0f} cm from its target — this arm "
                            "cannot take that shape here (the hand is on its target)")
        if "pose" in c:
            if "joints" in c:
                return [f"{where}: {slot} has both pose and joints — say one"]
            if c["pose"] != "rest":
                return [f"{where}: {slot} pose {c['pose']!r} is not known — the only named pose is rest"]
            joints, at_rest = rest_pose(limits, side), True
        if not isinstance(joints, dict) or not joints:
            return [f"{where}: {slot} cue needs joints: {{n: deg, ...}}, or pose: rest"]

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
            elif at_rest:
                pass        # rest is built inside this robot's own limits (rest_pose); no margin applies
            else:
                lo_d, hi_d = math.degrees(lo) + MARGIN_DEG, math.degrees(hi) - MARGIN_DEG
                if not (lo_d <= signed <= hi_d):
                    p.append(f"{where}: {slot} J{j} {signed:+.1f} deg outside usable "
                             f"[{lo_d:+.1f}, {hi_d:+.1f}]")
            prev = state.get(j, 0.0)
            avg = abs(signed - prev) / dt
            peak = avg * PEAK_FACTOR
            cap = SPEED_CAP_DEG_S[j]
            if peak >= TRIP_DEG_S[j]:
                p.append(f"{where}: {slot} J{j} may peak at {peak:.0f} deg/s, at or over the arm "
                         f"controller's {TRIP_DEG_S[j]} deg/s trip — it would freeze the arm and stop "
                         f"the show; give it at least {abs(signed - prev) * PEAK_FACTOR / TRIP_DEG_S[j]:.2f}s")
            elif avg > cap + 1e-9:
                p.append(f"{where}: {slot} J{j} moves {avg:.0f} deg/s over {dt:.1f}s "
                         f"(cap {cap}, measured)")
            elif peak > cap + 1e-9 and warn is not None:
                need = abs(signed - prev) * PEAK_FACTOR / cap
                warn.append(f"{where}: {slot} J{j} may peak at {peak:.0f} deg/s if the arm eases "
                            f"(average {avg:.0f} x {PEAK_FACTOR}; cap {cap}, measured) — "
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
        do, frac = c.get("do"), c.get("open")
        if "pos" in c:
            return [f"{where}: {slot} pos (metres) is gone — write open: 0..1 (0 closed, 1 open, "
                    "the same on both sides); each robot's mapping to its controller lives on "
                    "the robot"]
        if do is not None and frac is not None:
            return [f"{where}: {slot} has both do and open — say one"]
        if do is None and frac is None:
            return [f"{where}: {slot} needs do: open|close, or open: 0..1"]
        if do is not None and do not in ("open", "close"):
            return [f"{where}: {slot} do must be open|close"]
        if frac is not None and not (_num(frac) and 0.0 <= float(frac) <= 1.0):
            p.append(f"{where}: {slot} open {frac!r} must be within 0..1 (0 closed, 1 open)")
        eff = c.get("effort")
        if eff is not None:
            self._grip_effort = True
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

    def _declared_sounds(self) -> set:
        """Sound files the show says it expects in sounds/ — kept locally like its music, because a
        seasonal sound would not be committed any more than a seasonal song (workplan 1.11)."""
        d = self.doc.get("sounds")
        return {x for x in d if isinstance(x, str)} if isinstance(d, list) else set()

    def _is_file_sound(self, name) -> bool:
        return bool(self._sound_file(name)) or name in self._declared_sounds()

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

    def video_route(self, c) -> str:
        """Where a VIDEO cue plays: the operator's override, else the cue's `output`, else the show's
        `video: {output}`, else the robot's default (casting, DEC 2026-10-20)."""
        if getattr(self, "video_override", None):
            return self.video_override
        v = self.doc.get("video") if isinstance(self.doc.get("video"), dict) else {}
        return c.get("output") or v.get("output") or DEFAULT_VIDEO_OUTPUT

    def _video_entry(self, vid):
        idx = self._library()
        return None if idx is None else idx.get(vid)

    def _video_length(self, c):
        if _num(c.get("secs")):
            return float(c["secs"])
        e = self._video_entry(c.get("video")) if c.get("do") == "play" else None
        if e and _num(e.get("duration_s")):
            return max(0.0, float(e["duration_s"]) - float(c.get("from_s") or 0.0))
        return 0.0

    def _check_video(self, where, c):
        do = c.get("do")
        if do == "stop":
            return [f"{where}: stop takes no {k}" for k in ("video", "from_s", "secs", "end") if k in c]
        if do != "play":
            return [f"{where}: video do must be play or stop"]
        p = []
        vid = c.get("video")
        if not isinstance(vid, str) or not vid:
            p.append(f"{where}: play needs a video: the id of a library video (library.py add --kind video)")
        else:
            idx = self._library()
            if idx is None:
                self._unchecked_videos.add(vid)       # no library here: checked on the robot, said once
            else:
                e = idx.get(vid)
                if e is None or e.get("kind") != "video":
                    p.append(f"{where}: {vid!r} is not a video in the library at {library.root()}")
                else:
                    self._skin_assets[vid] = e.get("sha256")
                    if _num(c.get("from_s")) and _num(e.get("duration_s")) and float(c["from_s"]) >= float(e["duration_s"]):
                        p.append(f"{where}: from_s {c['from_s']} is past the end of {vid} ({e['duration_s']} s)")
        if "from_s" in c and not (_num(c["from_s"]) and float(c["from_s"]) >= 0):
            p.append(f"{where}: from_s must be seconds >= 0 into the video")
        if "secs" in c and not (_num(c["secs"]) and float(c["secs"]) > 0):
            p.append(f"{where}: secs must be > 0 (how long it plays)")
        if c.get("end", "black") not in VIDEO_ENDS:
            p.append(f"{where}: end must be {' or '.join(VIDEO_ENDS)} (what shows after it)")
        route = self.video_route(c)
        if route not in VIDEO_OUTPUTS:
            p.append(f"{where}: video output {route!r} is not one of {', '.join(VIDEO_OUTPUTS)}")
        return p

    def sound_route(self, c) -> str:
        """Where a SOUND cue plays: the operator's override, else the cue's own `output`, else the
        tablet (DEC-5) — except a Tab5 earcon with no file of the same name, which only the Tab5
        has, and so defaults there."""
        if self.audio_override:
            return self.audio_override
        if c.get("output"):
            return c["output"]
        name = c.get("sound")
        if name in TAB5_EARCONS and not self._is_file_sound(name):
            return "tab5"
        return DEFAULT_AUDIO_OUTPUT

    def _check_sound(self, where, c):
        if c.get("do") != "play":
            return [f"{where}: sound do must be play (SOUND plays a named sound effect)"]
        p = []
        if c.get("role", DEFAULT_AUDIO_ROLE) not in AUDIO_ROLES:
            p.append(f"{where}: role {c['role']!r} is not {' or '.join(AUDIO_ROLES)} (unmarked is dialogue)")
        name = c.get("sound")
        if not isinstance(name, str) or not name:
            p.append(f"{where}: play needs a sound name")
        elif name not in TAB5_EARCONS and not self._is_file_sound(name):
            p.append(f"{where}: unknown sound {name!r} — not a Tab5 earcon "
                     f"({', '.join(TAB5_EARCONS)}), no file {SOUND_DIR}/{name}.* beside the show, "
                     "and not listed in the show's sounds:")
        else:
            route = self.sound_route(c)
            if route not in AUDIO_OUTPUTS:
                p.append(f"{where}: sound output {route!r} is not one of {', '.join(AUDIO_OUTPUTS)}")
            else:
                has = (("earcon",) if name in TAB5_EARCONS else ()) + (("file",) if self._is_file_sound(name) else ())
                if not set(has) & set(CAN_PLAY[route]):
                    what = "a Tab5 earcon" if name in TAB5_EARCONS else "a sound file"
                    p.append(f"{where}: the {route} cannot play {name!r} — it is {what}, and the "
                             f"{route} plays {', '.join(CAN_PLAY[route])}s"
                             + (f"; add {SOUND_DIR}/{name}.wav for the {route}" if "file" in CAN_PLAY[route] else ""))
        secs = c.get("secs")
        if secs is not None and not (_num(secs) and SOUND_SECS_SANITY[0] <= float(secs) <= SOUND_SECS_SANITY[1]):
            p.append(f"{where}: sound secs {secs} outside {SOUND_SECS_SANITY}")
        return p

    def _check_palette(self, used, arm_state, head_tgt, events, limits) -> list:
        """A palette routine (workplan 1.13 / 4.4b) is picked by the operator in any order after any
        other, so it must END in the agreed rejoin state: every arm it moved at rest, the head
        centred, the state light released. (The lift has no measured height to rejoin to; not
        checked — recorded as open.)"""
        pal = self.doc.get("palette")
        if pal is None:
            return []
        if not isinstance(pal, dict) or not isinstance(pal.get("label"), str) or not pal["label"]:
            return ["palette: needs a label — the text on the operator's button"]
        p = [f"palette: unknown key {k!r} (known: label, order)" for k in pal if k not in ("label", "order")]
        if "order" in pal and not (isinstance(pal["order"], int) and not isinstance(pal["order"], bool)):
            p.append("palette: order must be an integer")
        for slot in ARM_SLOTS:
            if slot not in used:
                continue
            side = "left" if slot == "ARM_L" else "right"
            m = -1 if side == "left" else 1
            rest = {j: v * (m if j in MIRRORED_JOINTS else 1) for j, v in rest_pose(limits, side).items()}
            end = arm_state.get(slot, {})
            off = [j for j in range(1, 8) if abs(end.get(j, 0.0) - rest[j]) > 0.5]
            if off:
                p.append(f"palette: a routine must end with {slot} at rest (add a pose: rest keyframe) "
                         f"— it ends off rest on J{', J'.join(map(str, off))}")
        if "HEAD" in used and (abs(head_tgt["pan"] - HEAD_NEUTRAL_DEG) > 1.0
                               or abs(head_tgt["tilt"] - HEAD_NEUTRAL_DEG) > 1.0):
            p.append(f"palette: a routine must end with the head centred (pan 90, tilt 90) — it ends at "
                     f"pan {head_tgt['pan']:g}, tilt {head_tgt['tilt']:g}")
        lights = sorted((e for e in events if e.get("slot") == "LIGHT"), key=lambda e: e["t"])
        if lights and lights[-1]["fields"].get("do") != "release":
            p.append("palette: a routine must end with the state light released (do: release)")
        return p

    def _check_requires(self, used: set) -> list:
        """`requires:` is what the runner's preflight is built from, so a typo in it is not
        cosmetic: `rc_authority: AUOT` would otherwise pass here and refuse (or worse) later."""
        req = self.requires
        if not isinstance(req, dict):
            return ["requires: must be a mapping of arms / tab5 / rc_authority"]
        p = [f"requires: unknown key {k!r} (known: {', '.join(REQUIRES_KEYS)})"
             for k in req if k not in REQUIRES_KEYS]
        auth = req.get("rc_authority")
        if auth is not None and auth not in CH5_MODES:
            hint = (f" — it is called {CH5_OLD_NAMES[auth]} now (workplan DEC-9)"
                    if isinstance(auth, str) and auth.upper() in CH5_OLD_NAMES else "")
            p.append(f"requires: rc_authority {auth!r} is not a CH5 mode "
                     f"({', '.join(CH5_MODES)}){hint}")
        moving = sorted(used & set(MOTION_SLOTS))
        if moving and auth != "AUTO":
            p.append(f"requires: this show moves {', '.join(moving)}, so it runs in AUTO — "
                     "add rc_authority: AUTO (shows that move run in AUTO; teaching happens in MANUAL)")
        arms = req.get("arms")
        if arms is not None and arms != "active":
            p.append(f"requires: arms {arms!r} — the only requirement is active")
        if used & {"ARM_L", "ARM_R", "GRIP_L", "GRIP_R"} and arms != "active":
            p.append("requires: this show uses the arms — add arms: active, so the preflight checks "
                     "they are powered and their controllers running")
        tab5 = req.get("tab5")
        if tab5 is not None and not isinstance(tab5, bool):
            p.append(f"requires: tab5 {tab5!r} must be true or false")
        if used & set(TAB5_SLOTS) and tab5 is not True:
            p.append(f"requires: this show uses {', '.join(sorted(used & set(TAB5_SLOTS)))}, which "
                     "the Tab5 drives — add tab5: true")
        return p

    def _check_drive(self, where, c):
        p = []
        vals = {}
        for k in ("forward_m", "left_m", "turn_deg"):
            v = c.get(k, 0)
            if not _num(v):
                p.append(f"{where}: drive {k} must be a number")
            vals[k] = float(v) if _num(v) else 0.0
        if not any(abs(v) > 1e-9 for v in vals.values()):
            p.append(f"{where}: a drive needs forward_m, left_m or turn_deg — it moves nothing")
        secs = c.get("secs")
        if not (_num(secs) and DRIVE_SECS[0] <= float(secs) <= DRIVE_SECS[1]):
            p.append(f"{where}: drive secs {secs!r} must be {DRIVE_SECS[0]:g}–{DRIVE_SECS[1]:g} "
                     "(one move; a longer journey is several)")
            return p
        # the wheels share one command budget between moving and turning: the time each needs adds up
        need = math.hypot(vals["forward_m"], vals["left_m"]) / DRIVE_V_MAX + abs(vals["turn_deg"]) / DRIVE_W_MAX_DEG
        if need > float(secs) + 1e-9:
            p.append(f"{where}: this drive needs at least {need:.1f}s at the base's autonomy ceiling "
                     f"({DRIVE_V_MAX:.2f} m/s, {DRIVE_W_MAX_DEG:.0f}°/s — placeholder speeds), not {float(secs):g}s")
        return p

    def _check_dance(self, where, c):
        move = c.get("move")
        if move not in DANCE_MOVES:
            return [f"{where}: dance move {move!r} is not in the library "
                    f"({', '.join(DANCE_MOVES)}) — a dance names a move, never a velocity"]
        p = []
        if any(abs(n) > 1e-9 for n in dance_net(move)):
            p.append(f"{where}: dance move {move!r} does not return to where it started "
                     f"(net {dance_net(move)}) — a library error, refused")
        secs = c.get("secs", DANCE_DEFAULT_SECS)
        if not (_num(secs) and DANCE_SECS[0] <= float(secs) <= DANCE_SECS[1]):
            p.append(f"{where}: dance secs {secs!r} outside {DANCE_SECS}")
        amp = c.get("amp", 1.0)
        if not (_num(amp) and 0.0 < float(amp) <= 1.0):
            p.append(f"{where}: dance amp {amp!r} must be within (0, 1] — 1 is the autonomy ceiling")
        return p

    @staticmethod
    def _check_hand_box(events, music) -> list[str]:
        """The hands at the arm CONTROLLERS' own point (hand_tcp), along every waypoint the runner will
        send, against their workspace box (tcp.py) — a waypoint outside it gets the WHOLE arm goal
        rejected at the robot. A notice, not a problem, where the arm chain is not on this machine."""
        try:
            import arm_output
            import tcp
        except ImportError as e:             # numpy absent: say so, never pass silently
            return [f"NOTICE the hands' workspace box is not checked here ({e})"]
        chain = tcp.load_chain()
        if chain is None:
            return ["NOTICE the hands' workspace box is not checked here: no arm chain (description/openarm/"
                    "chain.json) on this machine"]
        tracks = {}
        for slot in ARM_SLOTS:
            kfs = [{"start": e["start"], "t": e["t"], "pose_signed": {str(j): v for j, v in e["pose"].items()}}
                   for e in events if e.get("slot") == slot and "start" in e and not e.get("bad") and e.get("pose")]
            if kfs:
                tracks[slot] = arm_output.track_points(sorted(kfs, key=lambda k: k["t"]))
        beat = 60.0 / music["bpm"] if music and _num(music.get("bpm")) else None
        bpb = (music or {}).get("beats_per_bar") or 4

        def label(t):
            if not beat:
                return f"t={t:.2f}s"
            k = (t - float((music or {}).get("offset_s") or 0.0)) / beat
            return f"bar {int(k // bpb) + 1}.{int(k % bpb) + 1} ({t:.1f}s)"
        return tcp.box_problems(chain, tracks, label)

    @staticmethod
    def _move_notices(events) -> list[str]:
        """What a move cannot do where it is: one too early to be sent its lead ahead, and a sparkle
        on a library (picture) skin, which draws nothing — said, never refused (the face answers OK)."""
        out, skin = [], "default"
        face = sorted((e for e in events if e.get("slot") == "FACE" and "start" in e and not e.get("bad")),
                      key=lambda e: e["t"])
        for e in face:
            c = e["fields"]
            if c.get("do") == "skin" and isinstance(c.get("name"), str):
                skin = c["name"]
            elif c.get("do") == "move" and c.get("name") in FACE_MOVES:
                lead = FACE_MOVES[c["name"]][0]
                if e["t"] < lead - 1e-9:
                    out.append(f"face move {c['name']} at t={e['t']:.2f}s cannot be sent {lead:.3f}s early: "
                               "it is sent at the start and peaks late")
                if c["name"] == "sparkle" and skin not in CODE_SKINS:
                    out.append(f"face move sparkle at t={e['t']:.2f}s falls on the picture skin {skin!r}, "
                               "which cannot draw it: nothing shows")
        return out

    def _check_face(self, where, c):
        do, name = c.get("do"), c.get("name")
        if do == "skin":
            if not isinstance(name, str) or not name:
                return [f"{where}: face skin needs a name (`default` for the standard eyes)"]
            if name.lower() == IDLE_NAME:
                return [f"{where}: `idle` is reserved — every show end sends SKIN IDLE itself, the robot's "
                        "idle skin back; name the skin the show wants"]
            if name in CODE_SKINS:
                return []
            idx = self._library()
            if idx is None:
                self._unchecked_skins.add(name)       # no library here: a notice, not a problem
                return []
            e = idx.get(name)
            if e is None or e.get("kind") != "skin":
                return [f"{where}: face skin {name!r} is neither drawn by the face "
                        f"({', '.join(CODE_SKINS)}) nor a skin in the library at {library.root()}"]
            self._skin_assets[name] = e.get("sha256")
            return []
        if do == "theme":
            declared = {x.lower() for x in (self.doc.get("themes") or []) if isinstance(x, str)}
            if not isinstance(name, str) or not name:
                return [f"{where}: face theme needs a name"]
            if name.lower() == IDLE_NAME:
                return [f"{where}: `idle` is reserved — every show end sends THEME IDLE itself, the robot's "
                        "idle theme back; name the theme the show wants"]
            if name.lower() not in FACE_THEMES and name.lower() not in declared:
                return [f"{where}: face theme {name!r} is neither built into the face "
                        f"({', '.join(FACE_THEMES)}) nor listed under the show's themes: "
                        "(authored themes, installed on the head by CFG)"]
            return []
        if do == "move":
            if name not in FACE_MOVES:
                return [f"{where}: face move {name!r} is not one the face has ({', '.join(FACE_MOVES)})"]
            return []
        if do != "emote":
            return [f"{where}: face do must be emote|theme|skin|move"]
        if name not in FACE_EMOTES:
            return [f"{where}: face emote {name!r} is not one the face has "
                    f"({', '.join(FACE_EMOTES)} — from kEmotes[] in the ESP-IDF face)"]
        return []

    def _check_device(self, where, c):
        do = c.get("do")
        if do not in DEVICE_DOS:
            return [f"{where}: device do must be {'|'.join(DEVICE_DOS)}"]
        p = []
        lv = c.get("level")
        if not (_num(lv) and 0 <= lv <= 1):
            p.append(f"{where}: device {do} needs level 0..1")
        f = c.get("fade_s")
        if f is not None and not (_num(f) and 0 <= f <= DEVICE_FADE_MAX_S):
            p.append(f"{where}: device fade_s {f!r} outside 0..{DEVICE_FADE_MAX_S:g}")
        return p

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
        elif (clash := light_reserved_clash(rgb)):
            p.append(f"{where}: light rgb {rgb} reads as {clash} — the state light's reserved "
                     "colour, which tells a bystander whether the robot is safe; choose another hue "
                     "or a paler colour")
        br = c.get("br")
        if br is not None and not (_num(br) and 0 <= br <= LIGHT_BR_MAX):
            p.append(f"{where}: light br {br} outside 0..{LIGHT_BR_MAX}")
        return p

    # ---- the resolved timeline --------------------------------------------------------------
    def export(self, limits: dict, limits_path: str | None = None, with_notes: bool = False) -> dict:
        """The resolved timeline: what the show MEANS, with every inference made explicit.
        Schema in notes/design/choreo-format-v1.md 10. Deterministic for a given show+limits."""
        a = self.analyse(limits)
        declared = [s for s in SLOTS if s in a["declared"]] or [s for s in SLOTS if s in a["used"]]
        # a track for every slot a cue USES too, declared or not: an undeclared cue is a problem, and
        # it must be seen on its lane with that problem — never dropped from the timeline
        tracks = {s: [] for s in SLOTS if s in declared or s in a["used"]}
        for e in a["events"]:
            if e.get("bad") or "start" not in e or e["slot"] not in tracks:
                continue
            ev = {"t": _r(e["t"]), "start": _r(e["start"]), "end": _r(e["end"]),
                  "slot": e["slot"], "kind": e["kind"], "cue": _jsonable(e["fields"]),
                  "src": e["src"]}
            if e["slot"] == "SOUND":
                ev["output"] = self.sound_route(e["fields"])     # resolved, so players need not guess
                ev["role"] = e["fields"].get("role", DEFAULT_AUDIO_ROLE)
            if e["slot"] == "VIDEO":
                ev["output"] = self.video_route(e["fields"])
            if e["slot"] == "FACE" and e["fields"].get("do") == "move" and e["fields"].get("name") in FACE_MOVES:
                peak, dur = FACE_MOVES[e["fields"]["name"]]
                ev["send_t"] = _r(max(0.0, e["t"] - peak))      # when the line goes, so the PEAK is at t
                ev["move_s"] = _r(dur)
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
            music = {k: a["music"].get(k) for k in ("file", "song", "bpm", "offset_s", "beats_per_bar", "role")}
            music["output"] = a["music"].get("output", DEFAULT_AUDIO_OUTPUT)
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
            # the content hash of every library asset the show was validated against (plan 4.6), so a
            # run can tell when one has changed since
            "assets": a.get("assets", {}),
            # timeline notes only for the composer to draw — never in the runner's cue sheet
            **({"timeline_notes": a.get("timeline_notes", [])} if with_notes else {}),
            "duration_s": _r(max(ends, default=0.0)),
            "markers": [{"t": _r(m["t"]), "name": m["name"], "src": m["src"]}
                        for m in a["markers"]],
            "tracks": tracks,
            "dance": ({"floor_radius_m": self.doc["dance"].get("floor_radius_m")}
                      if isinstance(self.doc.get("dance"), dict) else None),
            # the head's volume and brightness at show time 0 (format §1); DEVICE cues change them
            "device": ({k: float(v) for k, v in self.doc["device"].items() if k in ("volume", "brightness")}
                       if isinstance(self.doc.get("device"), dict) else None),
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


def rest_pose(limits: dict, side: str) -> dict:
    """`pose: rest` — where the arm hangs (every joint 0), CLAMPED into this robot's own joint
    limits, in the show's outward sense (workplan 1.9).

    The hanging pose is not quite legal: the elbow's rest value (0) sits a few degrees below the arm
    software's own lower limit for J4. So rest means the nearest pose to hanging that the arm
    software allows, with NO extra margin — the validator accepts exactly these values because they
    are the documented rest, and resolves them from each robot's limits file, so no robot's numbers
    are committed."""
    out = {}
    for j in range(1, 8):
        lo, hi = limits.get(f"openarm_{side}_joint{j}", (None, None))
        signed = 0.0 if lo is None else min(max(0.0, math.degrees(lo)), math.degrees(hi))
        m = -1 if (side == "left" and j in MIRRORED_JOINTS) else 1
        out[j] = round(signed * m, 3) + 0.0       # outward sense; + 0.0 turns -0.0 into 0.0
    return out


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
        doc = yaml.load(fh, Loader=_SHOW_LOADER)
    if not isinstance(doc, dict):
        raise ShowError(f"{path}: not a mapping")
    return Show(doc, path)


def show_from_text(text: str, path: str = "") -> Show:
    """For editors: a show from unsaved text. `path` locates relative files (the audio)."""
    doc = yaml.load(text, Loader=_SHOW_LOADER)
    if not isinstance(doc, dict):
        raise ShowError("not a mapping")
    return Show(doc, path)


DEFAULT_LIMITS = "/openarm_ws/src/openarm/hw_validation/joint_limits.yaml"


def countdown_s(v):
    """A start countdown in seconds, or None if `v` is not one (a bool is not a number here)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= START_COUNTDOWN_MAX_S:
        return None
    return float(v)


def _bright_low(where, level) -> str:
    return (f"{where}: brightness {level:g} is below {DEVICE_BRIGHT_LOW:g} — the head will not dim past its "
            "own floor (bright_min), so it will be brighter than written")


def describe(limits: dict) -> str:
    """Every number and vocabulary the validator holds a show to — so an author (or an agent)
    reading the format spec can find them without reading this file or tripping an error."""
    L = []
    w = L.append
    w("ARM joints (each arm's own limits, minus a margin; speed checked per segment):")
    for side, m in (("left", -1), ("right", 1)):
        for j in range(1, 8):
            lo, hi = limits.get(f"openarm_{side}_joint{j}", (None, None))
            if lo is None:
                continue
            lo_d, hi_d = math.degrees(lo) + MARGIN_DEG, math.degrees(hi) - MARGIN_DEG
            # shows write J1-J3 in each arm's OWN outward sense: show the usable range that way
            if j in MIRRORED_JOINTS and m < 0:
                lo_d, hi_d = -hi_d, -lo_d
            w(f"  {side:5s} J{j}: usable {lo_d:+7.1f} .. {hi_d:+7.1f} deg (as written in a show)   "
              f"cap {SPEED_CAP_DEG_S[j]} deg/s average (measured); warns above it x {PEAK_FACTOR}")
    w(f"  margin {MARGIN_DEG:g} deg inside each limit. pose: rest = every joint 0, clamped into the"
      " limits with no margin:")
    for side in ("left", "right"):
        w(f"    {side:5s} rest: " + ", ".join(f"J{j} {v:g}" for j, v in rest_pose(limits, side).items()))
    w(f"  shoulder/head: J2 must stay {SHOULDER_MARGIN_DEG:g} deg below {SHOULDER_HEAD_CONTACT_DEG:g}"
      f" (contact, measured); with J4 < {ELBOW_FOLD_MIN_DEG:g} (extended) J2 <= {SHOULDER_EXTENDED_MAX_DEG:g}")
    w(f"GRIP: do open|close, or open: 0..1 (0 closed, 1 open, both sides); effort (0, {GRIP_EFFORT_MAX:g}] "
      "in the gripper controller's own units")
    w(f"HEAD: pan, tilt in {HEAD_RANGE_DEG} deg (90 = centre); an axis left out keeps its target; "
      f"turns at {HEAD_SLEW_DPS:g} deg/s per axis (warns if a move cannot finish)")
    w(f"LIFT: do up|down|stop, secs <= {LIFT_MAX_S:g}; never ack_pinch")
    w(f"SOUND: do play, sound: a Tab5 earcon {TAB5_EARCONS}, or {SOUND_DIR}/<name>.* beside the show "
      "(listed under sounds: if it is kept locally and may be absent); "
      f"output {AUDIO_OUTPUTS} (default tablet; an earcon with no file -> tab5); can play: {CAN_PLAY}")
    w(f"FACE: do skin, name {CODE_SKINS} or a library skin; do emote, name {FACE_EMOTES}; or do theme, name {FACE_THEMES} or an authored theme listed "
      "under themes: (installed on the head by CFG). A skin and a theme last until the show ends: every end sends SKIN IDLE, THEME IDLE and EMOTE AUTO; `idle` is reserved")
    w(f"FACE: do move, name {tuple(FACE_MOVES)} — a one-shot gesture over the held emote; the cue's time is "
      "its PEAK (sent early by the face's measured offset); sparkle may not show on a picture skin")
    w(f"LIGHT: do color, rgb [0..{LIGHT_RGB_MAX}] x3, br 0..{LIGHT_BR_MAX}; or do release. Refused: "
      f"anything within {LIGHT_RESERVED_BAND_DEG:g} deg of hue of {dict(LIGHT_RESERVED_HUES)} and at least "
      f"{LIGHT_RESERVED_MIN_SAT:g} saturated (reads as a reserved state colour)")
    w(f"DANCE: move {tuple(DANCE_MOVES)}, secs {DANCE_SECS} (default {DANCE_DEFAULT_SECS:g}), amp (0, 1] "
      f"(default 1 = the autonomy ceiling); <= {DANCE_TOTAL_MAX_S:g} s per show; needs "
      f"dance: {{ floor_radius_m: {FLOOR_RADIUS_SANITY_M} }}")
    w(f"requires: arms: active | tab5: true|false | rc_authority: {CH5_MODES}; a show that moves "
      f"{MOTION_SLOTS} needs rc_authority: AUTO; arms/grippers need arms: active; HEAD/FACE need tab5: true")
    w(f"music: file, bpm {BPM_SANITY}, offset_s >= 0, beats_per_bar {BEATS_PER_BAR_SANITY}, output")
    w(f"start_countdown_s: 0..{START_COUNTDOWN_MAX_S:g} (default {START_COUNTDOWN_DEFAULT_S:g}) — the "
      "countdown between a run's GO and its first move")
    w(f"DEVICE: do {'|'.join(DEVICE_DOS)}, level 0..1, fade_s 0..{DEVICE_FADE_MAX_S:g}; show-level "
      f"device: {{ volume, brightness }}; brightness below {DEVICE_BRIGHT_LOW:g} warns (the head's floor); "
      "every show end hands both back to the head")
    w("palette: { label, order } — a routine must end with its arms at pose: rest, the head at "
      "90/90 and the light released, so any routine can follow any other")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "export", "describe"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--limits", default=DEFAULT_LIMITS,
                    help="the arm software's joint_limits.yaml (default: the path in the container)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-o", "--out", help="export: write here instead of stdout")
    ap.add_argument("--allow-invalid", action="store_true",
                    help="export: write the timeline even if the show fails (valid: false)")
    ap.add_argument("--no-sweep", action="store_true",
                    help="do not require the self-collision stamp (sweep.py) the robot requires — desk work only")
    a = ap.parse_args()

    limits = load_limits(a.limits)
    if a.cmd == "describe":
        print(describe(limits))
        sys.exit(0)
    if not a.paths:
        sys.exit(f"{a.cmd} needs at least one show")
    paths = [q for p in a.paths for q in sorted(glob.glob(p))] or a.paths

    if a.cmd == "export":
        if len(paths) != 1:
            sys.exit("export takes exactly one show")
        show = load_show(paths[0])
        show.require_sweep = not a.no_sweep
        tl = show.export(limits, a.limits)
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
        show.require_sweep = not a.no_sweep
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
