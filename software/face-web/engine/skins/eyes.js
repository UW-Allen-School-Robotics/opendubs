// The default skin: the robot's eyes. A rounded body with a vertical gradient from the state
// colour to 35% of it, a dark pupil that travels with the gaze, one catchlight inside the pupil,
// and TWO lids with curved edges -- an upper and a lower -- plus an angled brow.
//
// EXPRESSION COMES FROM THE LIDS' SHAPE, not from scaling the eye. The maintainers chose this by
// looking at it beside the old behaviour (#136, decision 1): an emote curves and closes the lids
// while the eye stays the same size, where the old vocabulary could only squash the whole eye.
// notes/design/face-eyes-and-gaze.md has the reasoning; vocab.js's SHAPES table has the numbers.
//
// THE PUPIL IS FIXED at the baseline fraction and no emote changes it. The maintainers looked at
// per-emote pupil sizes and rejected them -- "the pupil is too big for several of them, in general
// we're walking into the uncanny valley" -- so the channel is not here to be tuned back in.
//
// Every skin draws from the same per-eye inputs (Face.eyes()), so it blinks, emotes and looks the
// same way as any other; only the drawing differs.

import { drawMouth } from "./mouth.js";

const PUPIL_FRAC = 0.34;        // today's baseline, and it stays there
const RIM_PAD = 14;             // how close to the rim the pupil may travel
// The brow, unchanged by the lid-shape change: the lids carry the expression now, but the brow is
// still half of what reads as ALERT or SQUINT, so it draws exactly as it always did.
const BROW_W = 0.86, BROW_H = 0.085, BROW_GAP = 0.10, BROW_RANGE = 0.14;
const CURVE_FRAC = 0.22;

function roundRectPath(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
}

// A lid as a filled path with a QUADRATIC edge. `fromTop` picks which way it closes.
//
// The curve is the whole point, so it is worth being exact about the sign: a POSITIVE lidCurve
// bows the middle of the upper lid's edge DOWNWARD (a droop -- tired, sad), and a NEGATIVE one
// lifts the middle (a wide, alert aperture). On the lower lid the sense is mirrored, so that a
// positive curve on the lower lid bulges UPWARD, which is the smile.
function lid(ctx, x, y, w, h, amount, curve, fromTop) {
  if (amount <= 0.0005) return;
  const edge = fromTop ? y + amount * h : y + h - amount * h;
  const bow = curve * h * CURVE_FRAC * (fromTop ? 1 : -1);
  ctx.beginPath();
  if (fromTop) {
    ctx.moveTo(x - 2, y - 2);
    ctx.lineTo(x + w + 2, y - 2);
    ctx.lineTo(x + w + 2, edge);
    ctx.quadraticCurveTo(x + w / 2, edge + bow * 2, x - 2, edge);
  } else {
    ctx.moveTo(x - 2, y + h + 2);
    ctx.lineTo(x + w + 2, y + h + 2);
    ctx.lineTo(x + w + 2, edge);
    ctx.quadraticCurveTo(x + w / 2, edge + bow * 2, x - 2, edge);
  }
  ctx.closePath();
  ctx.fill();
}

export const name = "eyes";

export function draw(ctx, e) {
  // The mouth, once for the face rather than once per eye, and only when it is on. FIRST, before
  // any of the eye drawing: ring and fireworks RETURN EARLY when the lid is shut, so a mouth drawn
  // at the end would vanish on every blink. It is independent of the eye and does not overlap it,
  // so drawing it first costs nothing and cannot be skipped. `e.mouth` is null on every face that
  // has no mouth, so those faces pay one comparison and no draw.
  if (e.side === "left" && e.mouth) drawMouth(ctx, e.mouth.cx, e.mouth.level, e.colour, e.dim);

  const { cx, cy, w, h } = e;
  const x = cx - w / 2, y = cy - h / 2;
  const pupilFrac = PUPIL_FRAC;   // fixed: no emote changes the pupil (see above)
  const lidUpper = Math.max(0, Math.min(1, e.lidUpper ?? e.lid ?? 0));
  const lidLower = Math.max(0, Math.min(1, e.lidLower ?? 0));
  const lidCurve = Math.max(-1, Math.min(1, e.lidCurve ?? 0));

  ctx.save();
  roundRectPath(ctx, x, y, w, h, e.radius);
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(40 / 255, e.colour);
  g.addColorStop(1, e.dim);
  ctx.fillStyle = g;
  ctx.fill();
  ctx.clip();                                    // pupil, catchlight and lids live inside the body

  // The pupil, where the gaze points.
  const pw = w * pupilFrac;
  const px = cx + e.gazeX * Math.max(0, (w - pw) / 2 - RIM_PAD);
  const py = cy + e.gazeY * Math.max(0, (h - pw) / 2 - RIM_PAD);
  ctx.fillStyle = "#04201e";
  ctx.beginPath(); ctx.arc(px, py, pw / 2, 0, Math.PI * 2); ctx.fill();

  // THE CATCHLIGHT RIDES THE PUPIL, as the shipping head does and as the maintainers chose on
  // looking at this prototype. I had anchored it to the eye; that is reverted.
  //
  // THE RECORD WAS GENUINELY SPLIT, which is why this is spelled out rather than just changed:
  //
  //   app_face.cpp:893, the SHIPPING head -- "CHILD OF THE PUPIL. A catchlight is a corneal
  //   reflection sitting over the iris and pupil, not a spot floating on the sclera", and
  //   parenting it there means it travels with the pupil and can never escape the eye.
  //
  //   face.cpp:215, the RETIRED Arduino head -- "ONE highlight, and it stays put ... a highlight
  //   that chases the gaze is the wrong primitive for this kind of eye at all."
  //
  // The shipping one governs, and the maintainers' eye agrees with it. My argument for anchoring
  // was that a corneal reflection stays fixed while the pupil moves under it, which is true of a
  // real eye -- it is the basis of pupil/corneal-reflection eye tracking -- but THIS eye has no
  // cornea and no sclera. It is a flat slab with a dark disc on it, and on that primitive the
  // reflection has nothing to be fixed relative to, which is what "the wrong primitive" meant.
  //
  // The dark rim I added is gone with it: it existed only because an anchored highlight sat on the
  // brightest part of the gradient. On the pupil it reads without help.
  const hlr = Math.max(2, pw / 6);
  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.beginPath(); ctx.arc(px - pw / 6, py - pw / 6, hlr, 0, Math.PI * 2); ctx.fill();

  // The lids, over everything inside the body.
  ctx.fillStyle = "#000";
  lid(ctx, x, y, w, h, lidUpper, lidCurve, true);
  lid(ctx, x, y, w, h, lidLower, lidCurve, false);
  ctx.restore();

  // The brow, unchanged from eyes.js: this prototype is about the lids, and changing two things at
  // once would make the comparison unreadable.
  const bw = w * BROW_W, bh = Math.max(6, h * BROW_H);
  const half = bw * Math.tan(e.browAngle * Math.PI / 180) / 2;
  const by = cy - h / 2 - h * BROW_GAP - bh / 2 - Math.abs(half) - e.browRaise * h * BROW_RANGE;
  const inner = e.side === "left" ? cx + bw / 2 : cx - bw / 2;
  const outer = e.side === "left" ? cx - bw / 2 : cx + bw / 2;
  ctx.strokeStyle = e.colour; ctx.lineWidth = bh; ctx.lineCap = "round";
  ctx.beginPath(); ctx.moveTo(outer, by - half); ctx.lineTo(inner, by + half); ctx.stroke();
}
