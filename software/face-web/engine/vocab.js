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
