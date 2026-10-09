// PROTOTYPE: expression carried by LID SHAPE instead of by scaling the eye.
//
// notes/design/face-eyes-and-gaze.md, decision 1 ("C"). This draws the same per-eye inputs as
// skins/eyes.js plus four optional ones -- lidUpper, lidLower, lidCurve, pupil -- and is selected
// with ?eyes=shape or ?eyes=full. With the parameters at their defaults it is meant to look like
// the original; the point of the prototype is what happens when they are NOT at their defaults.
//
// WHY A SEPARATE FILE rather than an edit to eyes.js: the baseline has to stay byte-identical or
// the side-by-side proves nothing. eyes.js dispatches here and is otherwise untouched.
//
// THREE CHANGES FROM eyes.js, in the order they matter:
//
//  1. THE LIDS ARE CURVES, AND THERE IS A LOWER ONE. eyes.js draws `fillRect` from the top and has
//     no lower lid at all, so "pleased" can only be expressed by squashing the whole eye. A smile
//     in the eyes is a LOWER-LID RAISE; that is the one thing the old vocabulary cannot say.
//
//  2. (WITHDRAWN) The catchlight was anchored to the eye instead of riding the pupil. The
//     maintainers looked at it and said it reads as wrong, and the shipping head had already
//     decided the opposite; it now rides the pupil exactly as eyes.js does. See the call site.
//
//  3. THE PUPIL CAN CHANGE SIZE. PUPIL_FRAC is a constant in eyes.js.

const RIM_PAD = 14;
const BROW_W = 0.86, BROW_H = 0.085, BROW_GAP = 0.10, BROW_RANGE = 0.14;

// How far the middle of a lid edge may bow, as a fraction of eye height, at |lidCurve| = 1.
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

export function drawShape(ctx, e) {
  const { cx, cy, w, h } = e;
  const x = cx - w / 2, y = cy - h / 2;
  const pupilFrac = e.pupil ?? 0.34;
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
