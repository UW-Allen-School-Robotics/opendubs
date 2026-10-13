"""The head protocol, version 1, as data — notes/design/head-protocol.md is the specification.

One copy of the vocabulary and the message list, shared by the conformance suite
(conformance.py), the reference head (fake_head.py) and the test that checks both against the
Tab5's own source. Change the document and this together.
"""
from __future__ import annotations

import re

VERSION = 5                 # v2 SKIN, v3 SPIN, v4 MOVE, v5 VOLUME/BRIGHT/ARMED (all tablet face only)
PORT = 4242

# ---- vocabulary (head-protocol.md §3, §4) -------------------------------------------------------
STATES = ("INIT", "SAFE_IDLE", "MANUAL", "AUTONOMOUS", "DEGRADED", "E_STOP", "CHARGING")
EMOTES = ("NEUTRAL", "ALERT", "SQUINT", "SOFT", "SLEEPY")
CUES = ("heard", "attend", "refuse", "fault", "mode", "boot")
# One-shot gestures (v4, MOVE). The face owns each one's timing; §3 lists when each peaks.
# v5: show-timed device levels and the "armed" indication. Clamps are per-robot settings; these are
# the defaults a head uses without one.
BRIGHT_MIN_DEFAULT = 0.15      # a show can never black the face out by mistake
VOL_MAX_DEFAULT = 1.0
FADE_MAX_S = 30.0
ARMED_LEASE_MS = 3000          # ARMED WAIT clears itself unless re-sent
ARMED_COUNT_MAX_S = 60
MOVES = ("wink_l", "wink_r", "blink2", "look_l", "look_r", "roll", "bob", "sparkle", "side_eye_l", "side_eye_r", "to_camera", "double_take", "startle")
THEMES_BUILT_IN = ("plain", "halloween")          # plus the alias NONE for plain
# Skins drawn by the face engine's code (software/face-web/engine/skins/index.js CODE_SKINS); any
# other name is a library skin's id. `default` is the standard eyes.
CODE_SKINS = ("default", "ring", "fireworks")
OWNERS = ("touch", "rc", "gaze", "thor", "local", "park", "none")
TRACK_SOURCES = ("thor", "local")

# Precedence, highest first (§4). PARK is not an owner in this list: it sets aside everything
# below RC.
PRECEDENCE = ("touch", "rc", "gaze", "thor", "local")

# Leases, milliseconds: (default, maximum).
LEASE = {"gaze": (600, 1000), "track": (600, 1000), "touch": (600, 1000), "park": (2000, 5000),
         "rc": (600, 600)}
SLEW_DPS = 70.0
TOUCH_RATE_MAX_DPS = 45.0

# ---- which half answers what (§1) --------------------------------------------------------------
FACE, NECK, DEVICE, TWIN = "face", "neck", "device", "twin"

# verb -> (half, status). status: "shipping" (the Tab5 answers it today); "agreed" (agreed between
# the lanes, not built yet: a head may start parsing it); "new" (proposed, NOT agreed: no head may
# parse it yet — CI fails if one does); "tab5-only"; "twin-only".
MESSAGES = {
    "PING":    (DEVICE, "shipping"),
    "STATE":   (FACE, "shipping"),
    "EMOTE":   (FACE, "shipping"),
    "THEME":   (FACE, "shipping"),
    "CUE":     (FACE, "shipping"),
    "CFG":     (FACE, "shipping"),
    "SKIN":    (FACE, "agreed"),        # v2, tablet face only
    "SPIN":    (FACE, "agreed"),        # v3, tablet face only
    "MOVE":    (FACE, "agreed"),        # v4, tablet face only
    "VOLUME":  (FACE, "agreed"),        # v5, tablet face only
    "BRIGHT":  (FACE, "agreed"),        # v5, tablet face only
    "ARMED":   (FACE, "agreed"),        # v5, tablet face only
    "VOICE":   (FACE, "tab5-only"),
    "SIM":     (TWIN, "twin-only"),
    "GAZE":    (NECK, "shipping"),
    "TRACK":   (NECK, "agreed"),
    "NECK":    (NECK, "agreed"),
    "TOUCH":   (NECK, "agreed"),
    "PARK":    (NECK, "agreed"),
    "TOF":     (NECK, "shipping"),
    "I2CSCAN": (NECK, "shipping"),
    "REBOOT":  (DEVICE, "shipping"),
    "OTA":     (DEVICE, "shipping"),
}

NOT_YET_SHIPPING = ("new", "agreed")    # a head that should answer these but says ERR unknown: PENDING

# What each kind of head implements. A head answers everything else `ERR unknown`.
KINDS = {
    "tab5": {"PING", "STATE", "EMOTE", "THEME", "CUE", "CFG", "VOICE", "GAZE", "TRACK", "NECK",
             "TOF", "I2CSCAN", "REBOOT", "OTA"},
    "tablet-face": {"PING", "STATE", "EMOTE", "THEME", "CUE", "CFG", "SKIN", "SPIN", "MOVE", "VOLUME",
                    "BRIGHT", "ARMED", "REBOOT", "OTA"},
    "neck": {"PING", "GAZE", "TRACK", "NECK", "TOUCH", "PARK", "TOF", "I2CSCAN", "REBOOT", "OTA"},
}
# The Tab5's own camera tracker IS its local tracker, so it refuses TRACK local from the network.
TRACK_LOCAL_REFUSED = {"tab5"}

# Retired 2026-10-08 (§8): every head answers these `ERR unknown`.
RETIRED_TEXT = ("TURN 0", "LIFT -1 0 0", "PMODE STRAFE", "PLCIP 10.42.2.20")
CAN_ID_RC_RATE = 0x107
RETIRED_FRAMES = {0x104: "absolute pan/tilt", 0x108: "tracking mode"}


def can_frame(can_id: int, data: bytes) -> bytes:
    """The packed {u32 id; u8 dlc; u8 data[8]} frame, little-endian, 13 bytes."""
    import struct
    return struct.pack("<IB8s", can_id, len(data), data.ljust(8, b"\0"))


# ---- replies -----------------------------------------------------------------------------------
_KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")


def fields(reply: str) -> dict:
    """The key=value tokens of a reply, in any order, ignoring `|` and anything else (§6)."""
    return dict(_KV.findall(reply or ""))


def kind_of(pong: str) -> str:
    """Which head answered a PING. A PONG without `kind=` is today's Tab5 (§6)."""
    return fields(pong).get("kind", "tab5")


NUM = r"-?\d+(?:\.\d+)?"
REPLY = {
    "gaze_ok":   re.compile(rf"^OK gaze ({NUM}) ({NUM})$"),
    "gaze_off":  re.compile(r"^OK gaze off$"),
    "gaze_err":  re.compile(r"^ERR gaze "),
    "track_ok":  re.compile(rf"^OK track (thor|local) ({NUM}) ({NUM}) owner=(\w+) "
                            rf"at=({NUM}),({NUM}) moving=([01])$"),
    "track_off": re.compile(r"^OK track (thor|local) off$"),
    "track_err": re.compile(r"^ERR track "),
    "track_local_refused": re.compile(r"^ERR track local is on this head$"),
    "neck_ok":   re.compile(rf"^OK neck pan=({NUM}) tilt=({NUM}) target=({NUM}),({NUM}) "
                            rf"moving=([01]) owner=(\w+) parked=([01]) tilt_band=({NUM}),({NUM})$"),
    "neck_none": re.compile(r"^ERR neck no pan/tilt fitted$"),
    "touch_hold": re.compile(r"^OK touch hold$"),
    "touch_rate": re.compile(rf"^OK touch rate ({NUM}) ({NUM})$"),
    "touch_off": re.compile(r"^OK touch off$"),
    "touch_err": re.compile(r"^ERR touch "),
    "park_ok":   re.compile(r"^OK park$"),
    "park_off":  re.compile(r"^OK park off$"),
    "park_err":  re.compile(r"^ERR park "),
    "skin_ok":   re.compile(r"^OK skin \S+$"),
    "skin_unknown": re.compile(r"^ERR unknown skin \S+ \(showing default\)$"),
    "spin_ok":   re.compile(r"^OK spin (counter|together|auto)$"),
    "spin_err":  re.compile(r"^ERR spin "),
    "move_err":  re.compile(r"^ERR move \S+$"),
    "volume_ok": re.compile(r"^OK volume (idle )?[01]\.\d\d$"),
    "bright_ok": re.compile(r"^OK bright (idle )?[01]\.\d\d$"),
    "volume_err": re.compile(r"^ERR volume "),
    "bright_err": re.compile(r"^ERR bright "),
    "armed_err": re.compile(r"^ERR armed "),
    "unknown":   re.compile(r"^ERR unknown$"),
}
