// THE TRACKING RETICLE: a hollow box over WHERE THE FACE THINKS THE TARGET IS.
//
// Ported from the Tab5, which already has it, and the reason is worth keeping in full because it
// is not the obvious one (app_face.cpp):
//
//   "'the eyes follow me' is not actually falsifiable by looking at it -- the eyes are always
//    moving, so a wrong mapping reads as ordinary wandering. The reticle makes the mapping
//    visible: if it sits on you and moves with you, tracking is right; if it moves the wrong way,
//    the sign is wrong and that is a one-line fix rather than an investigation."
//
// That was proved again on 2026-10-15, before this existed. With tracking live the neck drove
// itself into the bottom corner of its envelope and lost the person; from the face alone it looked
// like "it tracked me, then stopped". It took reading the tracker's log afterwards to find a sign
// error. WITH THIS ON, it is two seconds of watching: a box sliding away from you instead of onto
// you IS the sign error, with nothing to read.
//
// SO IT MUST BE DRAWN FROM THE VALUE THAT DRIVES THE EYES, after whatever transform the gaze takes
// -- not from the detector's box. A reticle drawn from the detector is a camera overlay: it would
// have sat happily on the person all through that runaway, because the detector was right and the
// mapping was wrong. The caller owns that, and it is the one thing about this that cannot be
// checked from in here.
//
// TWO COLOURS, AND THEY ARE LOAD-BEARING. Amber for motion, cyan for a face or a person, the
// Tab5's exact pair. Without the distinction, "the detector finally works" and "motion happened to
// be on your face" look identical -- and this project has already drawn a confident conclusion
// from exactly that ambiguity.
//
// THE DARK OUTLINE IS NOT DECORATION. Measured against every colour the face can show, cyan sits
// NINE degrees of hue and 0.04 of lightness from AUTONOMOUS (0x00b0eb) -- for practical purposes
// the same colour -- and AUTONOMOUS is not a reserved state, so a cyan box gets drawn over cyan
// eyes exactly when the robot is driving itself and watching people, which is when you most want
// to see it. Amber is nearer still to DEGRADED (0 degrees apart), and that one is survivable ONLY
// because the caller hides the reticle in a reserved state. So: a dark stroke under the coloured
// one, which costs no hue and makes the box read over cyan eyes, a purple charging face, a
// full-face photo or anything a skin does. Without it the reticle is reliably visible against the
// black background and nothing else -- the one case that needed no help.
//
// IT IS THE LOWEST OVERLAY, drawn before the charge gauge, the ARMED sign and the camera sign.
// Precedence by consequence: ARMED says the robot is about to move, the camera sign is the
// maintainers' consent condition, the gauge is the robot's own condition. This is a diagnostic,
// off by default, that no bystander needs -- so where they overlap, this is the one that loses.
// It is also the only overlay that MOVES, so it is the only one that can collide at all.

const W = 1280, H = 720;
const SIZE = 120;                  // the Tab5's box, when the caller does not size it
const RADIUS = 8, BORDER = 4;
const COLOUR = { motion: "255, 176, 32", face: "0, 229, 255", person: "0, 229, 255" };
const OUTLINE = "rgba(0, 0, 0, 0.70)";
const OPACITY = 0.8;
// Kept this far inside the design area, so the box can never be clipped and never reaches the
// outermost 8 px -- which is the charge gauge's band, where a reticle would read as a notch in the
// level rather than as a reticle.
const PAD = 20;

function roundRectPath(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
}

/**
 * Draw the reticle, in design space (1280x720), under every other overlay.
 *
 * `target` is null (draw nothing), or { x, y, kind, w, h }: the centre in DESIGN COORDINATES,
 * derived from the gaze that drives the eyes; `kind` is "motion" | "face" | "person"; `w`/`h` are
 * optional and size the box to the detection, defaulting to the Tab5's 120.
 *
 * `t` is seconds. The box does not animate -- a tracking box that pulsed or chased would add
 * motion to the one overlay whose whole job is to report motion faithfully, and a reticle that
 * moves on its own is a reticle you cannot read. It is accepted so every overlay has one shape.
 */
export function drawReticle(ctx, target, t = 0) {
  if (!target) return;
  // A NON-FINITE POSITION IS NOT A POSITION, and it is not drawn. NaN survived Math.min/Math.max
  // and produced a NaN box, which the canvas skips -- so the reticle vanished silently, and for a
  // diagnostic a silent absence is the WORST failure: it reads as "nothing detected", which is a
  // different and wrong answer. Clamping NaN to the centre would be worse still -- that is
  // inventing a position, the same sin as the Tab5's placeholder SOC and PING's hard-coded zeros.
  // Nothing drawn is the honest answer; whether a target exists is the caller's to know.
  if (!Number.isFinite(target.x) || !Number.isFinite(target.y)) return;
  const kind = COLOUR[target.kind] ? target.kind : "motion";   // an unknown kind is still SHOWN
  const w = Math.max(24, Math.min(W - 2 * PAD, target.w || SIZE));
  const h = Math.max(24, Math.min(H - 2 * PAD, target.h || SIZE));
  // Clamped so the whole box stays inside the padded area, not just its centre: a box clamped by
  // its centre still hangs off the edge, which is the bug the ARMED label shipped with.
  const cx = Math.max(PAD + w / 2, Math.min(W - PAD - w / 2, target.x));
  const cy = Math.max(PAD + h / 2, Math.min(H - PAD - h / 2, target.y));
  const x = cx - w / 2, y = cy - h / 2;

  ctx.save();
  // The dark stroke first and WIDER, so it sits proud on both sides of the colour. See above: this
  // is what makes the box legible over an AUTONOMOUS face, whose eyes are all but the same cyan.
  ctx.lineWidth = BORDER + 4;
  ctx.strokeStyle = OUTLINE;
  roundRectPath(ctx, x, y, w, h, RADIUS);
  ctx.stroke();

  ctx.lineWidth = BORDER;
  ctx.strokeStyle = `rgba(${COLOUR[kind]},${OPACITY})`;
  roundRectPath(ctx, x, y, w, h, RADIUS);
  ctx.stroke();
  ctx.restore();
}

// For the caller and the tests: the colours, and how far in the box is kept.
export const RETICLE = Object.freeze({ colours: Object.freeze({ ...COLOUR }), pad: PAD, size: SIZE });
