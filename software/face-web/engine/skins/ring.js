// A second skin, to prove the skin interface rather than to be a look of record: each eye is a
// segmented iris ring that turns slowly, with a bright core for the pupil. It takes exactly the
// inputs the default eyes take — the emote's shape squashes and rounds it, the lid closes it from
// the top, the brow becomes an arc above it, the gaze moves the core and leans the ring — so a
// show's EMOTE, a blink and a tracked person all read on it with no change anywhere else.

export const name = "ring";
const SEGMENTS = 18;

export function draw(ctx, e, t) {
  const { cx, cy, w, h } = e;
  const r = Math.min(w, h) / 2;
  const open = 1 - e.lid;
  const squash = (h / w) * open;                     // emote height and the lid both close it
  if (squash < 0.02) {                               // shut: a line, as a closed eye
    ctx.strokeStyle = e.colour; ctx.lineWidth = 10; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(cx - w * 0.42, cy); ctx.lineTo(cx + w * 0.42, cy); ctx.stroke();
    return;
  }
  const lean = e.gazeX * 0.22;                       // the ring leans toward where it looks
  ctx.save();
  ctx.translate(cx + e.gazeX * r * 0.18, cy + e.gazeY * r * 0.18);
  ctx.scale(w / Math.max(w, h), squash * (w / Math.max(w, h)));

  // The glow behind, in the state colour.
  const halo = ctx.createRadialGradient(0, 0, r * 0.2, 0, 0, r * 1.15);
  halo.addColorStop(0, `rgba(${e.rgb.join(",")},0.35)`); halo.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(0, 0, r * 1.15, 0, Math.PI * 2); ctx.fill();

  // The iris: segments that turn, thicker as the emote rounds the corners.
  const thick = r * (0.10 + e.radius / Math.min(w, h) * 0.25);
  const spin = t * 0.25 * (e.side === "left" ? 1 : -1) + lean;
  ctx.lineWidth = thick; ctx.lineCap = "butt";
  for (let k = 0; k < SEGMENTS; k++) {
    const a0 = spin + (k / SEGMENTS) * Math.PI * 2, a1 = a0 + (Math.PI * 2 / SEGMENTS) * 0.72;
    const lit = 0.55 + 0.45 * Math.sin(t * 2 + k * 0.7);
    ctx.strokeStyle = `rgba(${e.rgb.join(",")},${lit.toFixed(3)})`;
    ctx.beginPath(); ctx.arc(0, 0, r * 0.78, a0, a1); ctx.stroke();
  }
  // The core: the pupil, moving further than the ring does.
  const core = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.34);
  core.addColorStop(0, "#fff"); core.addColorStop(0.35, e.colour); core.addColorStop(1, e.dim);
  ctx.fillStyle = core;
  ctx.beginPath(); ctx.arc(e.gazeX * r * 0.28, e.gazeY * r * 0.28, r * 0.34, 0, Math.PI * 2); ctx.fill();
  ctx.restore();

  // The brow, as an arc above the eye: raised lifts it, the angle tips it.
  const tilt = (e.side === "left" ? 1 : -1) * e.browAngle * Math.PI / 180;
  const by = cy - r * squash - r * 0.22 - e.browRaise * r * 0.25;
  ctx.save(); ctx.translate(cx, by); ctx.rotate(tilt);
  ctx.strokeStyle = e.colour; ctx.lineWidth = Math.max(6, r * 0.09); ctx.lineCap = "round";
  ctx.beginPath(); ctx.arc(0, r * 0.9, r * 0.95, -Math.PI * 0.5 - 0.5, -Math.PI * 0.5 + 0.5); ctx.stroke();
  ctx.restore();
}
