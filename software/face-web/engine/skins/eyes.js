// The default skin: "eyes as usual" — the Tab5 face's eyes (app_face.cpp renderFace), in Canvas2D.
// A rounded body with a vertical gradient from the state colour to 35% of it, a dark pupil that
// travels with the gaze, one catchlight inside the pupil, a lid from the top, and an angled brow.
// Every skin draws from the same per-eye inputs (Face.eyes()), so it blinks, emotes and looks the
// same way; only the drawing differs.

const PUPIL_FRAC = 0.34, RIM_PAD = 14;
const BROW_W = 0.86, BROW_H = 0.085, BROW_GAP = 0.10, BROW_RANGE = 0.14;

function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
}

export const name = "eyes";

export function draw(ctx, e) {
  const { cx, cy, w, h } = e;
  const x = cx - w / 2, y = cy - h / 2;

  ctx.save();
  roundRect(ctx, x, y, w, h, e.radius);
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(40 / 255, e.colour);
  g.addColorStop(1, e.dim);
  ctx.fillStyle = g;
  ctx.fill();
  ctx.clip();                                         // pupil and lid live inside the body

  const pw = w * PUPIL_FRAC;
  const px = cx + e.gazeX * Math.max(0, (w - pw) / 2 - RIM_PAD);
  const py = cy + e.gazeY * Math.max(0, (h - pw) / 2 - RIM_PAD);
  ctx.fillStyle = "#04201e";
  ctx.beginPath(); ctx.arc(px, py, pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.92)";
  ctx.beginPath(); ctx.arc(px - pw / 6, py - pw / 6, pw / 6, 0, Math.PI * 2); ctx.fill();

  if (e.lid > 0.001) { ctx.fillStyle = "#000"; ctx.fillRect(x - 2, y - 2, w + 4, e.lid * h + 2); }
  ctx.restore();

  // The brow: the angle becomes a height difference between its ends; "inner" faces the centre.
  const bw = w * BROW_W, bh = Math.max(6, h * BROW_H);
  const half = bw * Math.tan(e.browAngle * Math.PI / 180) / 2;
  const by = cy - h / 2 - h * BROW_GAP - bh / 2 - Math.abs(half) - e.browRaise * h * BROW_RANGE;
  const inner = e.side === "left" ? cx + bw / 2 : cx - bw / 2;
  const outer = e.side === "left" ? cx - bw / 2 : cx + bw / 2;
  ctx.strokeStyle = e.colour; ctx.lineWidth = bh; ctx.lineCap = "round";
  ctx.beginPath(); ctx.moveTo(outer, by - half); ctx.lineTo(inner, by + half); ctx.stroke();
}
