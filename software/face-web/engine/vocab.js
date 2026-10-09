// The face's vocabulary — the head protocol's (notes/design/head-protocol.md §3), the same names and
// meanings as the Tab5 face (tablet plan DT-14). software/head-protocol/tests checks this file against
// head_protocol.py, and that against the Tab5's source, so the three cannot drift apart.

// name, label, colour, pinned emote (null = free to emote), reserved (no theme may recolour it)
export const STATES = [
  ["INIT",       "INIT",       0xdfe7f0, "NEUTRAL", false],
  ["SAFE_IDLE",  "SAFE IDLE",  0x2ecc58, null,      false],
  ["MANUAL",     "MANUAL",     0xf2f6fb, null,      false],
  ["AUTONOMOUS", "AUTONOMOUS", 0x00b0eb, null,      false],
  ["DEGRADED",   "DEGRADED",   0xf3b64a, "SQUINT",  true],
  ["E_STOP",     "E-STOP",     0xff4d4d, "ALERT",   true],
  ["CHARGING",   "CHARGING",   0x00c4a8, null,      false],
];

// name: width x, height x, corner radius (fraction of min(w,h)), lid (0 open .. 1 shut),
// brow raise (-1 .. +1), brow angle (degrees, + = inner end down)
export const EMOTES = {
  NEUTRAL: [1.00, 1.00, 0.205, 0.00,  0.00,  0.0],
  ALERT:   [1.06, 1.08, 0.173, 0.00, +1.00, -2.0],
  SQUINT:  [1.02, 0.56, 0.429, 0.00, -0.45, +9.0],
  SOFT:    [0.95, 0.88, 0.481, 0.12, +0.30, -7.0],
  SLEEPY:  [0.99, 0.40, 0.733, 0.35, -0.60, +2.0],
};

// PROTOTYPE, additive: the same emote names expressed through LID SHAPE instead of by scaling the
// eye. notes/design/face-eyes-and-gaze.md. Read only by the ?eyes= prototype; EMOTES above is
// untouched, so the baseline stays exactly as it ships.
//
//   lu  upper lid, 0 open .. 1 shut
//   ll  LOWER lid, 0 .. 1 -- the channel the old vocabulary does not have at all
//   lc  lid curve, -1 (middle lifts: wide, alert) .. +1 (middle droops: tired)
//   pu  pupil, as a fraction of eye width (0.34 is today's constant)
//   wm/hm  scale, KEPT ONLY where a size change is the meaning
//
// PLEASED is new. The five shipping emotes have no pleased expression: SOFT is the nearest and
// does it with a 12% lid and a brow tilt. A lower-lid raise is what a smile in the eyes actually
// is, so it is included here to show the channel doing something the old one cannot.
//
// THESE NUMBERS ARE FIRST GUESSES AND ARE MEANT TO BE TUNED BY LOOKING. PLEASED started at
// ll: 0.40 and read as a hard squint rather than a smile, so it came down to 0.26 -- which is the
// whole reason this is a prototype in the twin rather than a table in a document.
export const SHAPES = {
  NEUTRAL: { lu: 0.00, ll: 0.00, lc:  0.0, pu: 0.34, wm: 1.00, hm: 1.00 },
  ALERT:   { lu: 0.00, ll: 0.00, lc: -0.5, pu: 0.40, wm: 1.00, hm: 1.00 },
  SQUINT:  { lu: 0.30, ll: 0.26, lc: +0.3, pu: 0.30, wm: 1.02, hm: 0.80 },
  SOFT:    { lu: 0.10, ll: 0.22, lc: -0.2, pu: 0.37, wm: 1.00, hm: 1.00 },
  SLEEPY:  { lu: 0.52, ll: 0.06, lc: +0.8, pu: 0.30, wm: 1.00, hm: 1.00 },
  PLEASED: { lu: 0.12, ll: 0.26, lc: -0.1, pu: 0.38, wm: 1.00, hm: 1.00 },
};

export const CUES = ["heard", "attend", "refuse", "fault", "mode", "boot"];

// Built-in themes: per-state colours for the states a theme may touch. Reserved states are absent
// by construction, and the engine refuses them anyway.
export const THEMES = {
  plain: {},
  halloween: { INIT: 0xe8e2d0, SAFE_IDLE: 0x8ac926, MANUAL: 0xd9c2e9, AUTONOMOUS: 0x7b2d8e, CHARGING: 0x2f9e44 },
};

export const NO_LINK_COLOUR = 0x606c7c;    // slate: matches no state, and is not FAULT
export const QUOTES = [
  "Idling at maximum efficiency.",
  "This IS the droid you're looking for.",
  "Standing by. Emphasis on standing.",
  "All systems nominal. Ambitions: also nominal.",
  "I could lift things. I'm choosing not to.",
  "Fully charged. Mildly bored.",
  "Running on 52 volts and good intentions.",
  "Waiting for humans. As usual.",
  "My other mode is autonomous.",
  "Powered on. Opinions loading...",
];
