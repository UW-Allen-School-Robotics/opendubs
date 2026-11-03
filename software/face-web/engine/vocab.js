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
  // PURPLE, the maintainers' pick. THIS VALUE IS THE REFERENCE for charging across the robot:
  // the PRISM's charging colour is set to it, not the other way round, because the face is
  // where the constraint lives -- it has to stay clear of the ARMED violet and of six other
  // state colours, and an LED strip has no such neighbours. It was teal 0x00c4a8,
  // which sat close enough to SAFE_IDLE's green (0x2ecc58) that charging never read as its own
  // thing -- and the dashboard sent SAFE_IDLE while charging anyway, so the state was never
  // shown at all. Deliberately DEEPER than the ARMED overlay's violet (198,92,255): see
  // skins/charge.js, which also carries the gauge and the word that make the two unmistakable.
  //
  // DEEPER, and by a measured amount, because HUE IS NOT AVAILABLE. With seven state colours the
  // circle is full: green, cyan, amber, red and two near-whites are taken, and the only gaps left
  // are the violet ARMED already holds and a magenta that would sit next to E_STOP's red. So this
  // is the first signal on the face that CANNOT be separated from another by hue, and the rule
  // armed.js set out ("different hue AND different form") is retired in favour of what is actually
  // true: different LIGHTNESS and different form, both asserted. 0x6d30e0 is 15 points of
  // lightness below the ARMED violet -- a deep royal purple beside a light orchid one -- and only
  // charging carries the perimeter gauge and the word.
  ["CHARGING",   "CHARGING",   0x6d30e0, null,      false],
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

// How each emote shapes the LIDS. Expression comes from the lids' shape, not from scaling the eye:
// the maintainers chose this by looking at it beside the old behaviour (#136, decision 1).
//
//   lu  how far the UPPER lid closes, 0..1 of the eye's height
//   ll  the LOWER lid, the same
//   lc  how the lids' edges BOW. The sign is the whole meaning: POSITIVE droops the upper lid's
//       middle downward and bulges the LOWER lid's middle upward -- the smile. NEGATIVE lifts the
//       upper lid's middle into a wide, alert aperture.
//
// There is no pupil channel. Per-emote pupil sizes were looked at and rejected ("the pupil is too
// big for several of them"), so the pupil stays at the baseline for every emote.
export const SHAPES = {
  NEUTRAL: { lu: 0.00, ll: 0.00, lc:  0.0, wm: 1.00, hm: 1.00 },
  ALERT:   { lu: 0.00, ll: 0.00, lc: -0.5, wm: 1.00, hm: 1.00 },
  SQUINT:  { lu: 0.30, ll: 0.26, lc: +0.3, wm: 1.02, hm: 0.80 },
  SOFT:    { lu: 0.10, ll: 0.22, lc: -0.2, wm: 1.00, hm: 1.00 },
  SLEEPY:  { lu: 0.52, ll: 0.06, lc: +0.8, wm: 1.00, hm: 1.00 },
};

export const CUES = ["heard", "attend", "refuse", "fault", "mode", "boot"];

// Built-in themes: per-state colours for the states a theme may touch. `plain` is the absence of
// a theme, and is the only one built in -- HALLOWEEN NOW LIVES IN THE CONTENT FOLDER, as
// `face/themes/halloween.json`, and is discovered through /api/head/looks like any other.
//
// WHAT A THEME MAY NOT TOUCH, and where that is enforced now. Reserved states (DEGRADED, E_STOP)
// are refused by `stateColour` below, whatever a theme says. CHARGING is refused by the theme
// loader (engine/themes.js), and has to be, because it is deliberately NOT reserved: a reserved
// state replaces the skin with the default eyes and suppresses the charge gauge, which would take
// the charging face apart. The rule being protected is the one the colour note above sets out --
// charging is purple in every theme, a person learns it once, and a theme that made it green
// again would unteach it.
export const THEMES = {
  plain: {},
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
