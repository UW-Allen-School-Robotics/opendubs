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
//  2. THE CATCHLIGHT IS ANCHORED TO THE EYE, NOT THE PUPIL. eyes.js draws it at
//     (px - pw/6, py - pw/6), so it rides the pupil and reads as a decal painted on the iris. A
//     reflection stays where the light is while the pupil slides underneath it. Two lines, and the
//     strongest "this is a wet sphere" cue available.
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

  // THE CATCHLIGHT, FIXED TO THE EYE. Its position is a property of where the light is, not of
  // where the pupil happens to be, so it does not move when the gaze does. Upper-left, which is
  // where a face lit from above and in front has its highlight.
  //
  // IT NEEDS A RIM, AND THAT IS A FINDING FROM LOOKING, NOT A FLOURISH. The body's gradient is
  // BRIGHTEST AT THE TOP, so a white highlight placed where a highlight physically belongs lands
  // on the lightest part of the eye and nearly vanishes -- measured at INIT, whose colour is
  // 0xdfe7f0: the highlight read 250,251,252 against a 223,231,240 surround. The old highlight
  // only read well because it rode the dark PUPIL, which is exactly the behaviour being removed.
  //
  // So the highlight is drawn pure white over a soft dark rim, which is what a specular reflection
  // on a wet surface actually looks like and makes it legible on any state colour. If the eye's
  // gradient is ever reversed (dark at top), the rim can go.
  const hl = Math.max(3, w * 0.055);
  const hx = cx - w * 0.17, hy = cy - h * 0.19;
  ctx.fillStyle = "rgba(0,0,0,0.22)";
  ctx.beginPath(); ctx.arc(hx, hy, hl * 1.45, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.beginPath(); ctx.arc(hx, hy, hl, 0, Math.PI * 2); ctx.fill();

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
