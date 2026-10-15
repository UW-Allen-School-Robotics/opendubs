// THE ENGINE'S ONE SOURCE OF RANDOMNESS, so a renderer can make the face repeatable.
//
// The face is deliberately full of chance -- blink intervals, saccades, the gaze break, which emote
// comes next, microsaccades, an asset skin's counter-spin schedule. That is what stops it reading as
// a loop. It also means the same show rendered twice is two different videos, which is no good when
// the render is the deliverable: a re-render after a one-line fix should differ only by that line.
//
// So every one of those draws goes through `random()` here, and an offline renderer calls
// `setRandom(fn)` once with its own seeded generator. The engine does not own a seed or a PRNG: the
// caller brings one, because the caller is what knows whether this run is meant to be repeatable.
//
// THE LIVE FACE IS UNCHANGED, BYTE FOR BYTE. Nothing calls setRandom on the robot, and the default
// path CALLS THROUGH to Math.random rather than capturing a reference to it, which matters for two
// reasons: a test that assigns `Math.random = ...` (several do) still takes effect, and this module
// cannot pin a stale function if something replaces Math.random later.

let src = null;

/**
 * Install a random source: a function returning a number in [0, 1), like Math.random.
 * `setRandom()` or `setRandom(null)` restores Math.random. Returns the PREVIOUS source (or null),
 * so a caller can put back whatever was there instead of assuming it was the default.
 */
export function setRandom(fn) {
  const prev = src;
  src = typeof fn === "function" ? fn : null;
  return prev;
}

/** A number in [0, 1). The engine's only entry to chance. */
export function random() {
  return src ? src() : Math.random();
}
