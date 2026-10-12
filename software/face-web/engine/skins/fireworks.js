// A code skin for a show's big moment: rainbow rockets that launch from inside each eye, bloom, and
// cascade down like a willow. Chosen with the ordinary `SKIN fireworks`, and it loops for as long as
// it is selected, so a show turns it on for a chorus and back to `default` afterwards.
//
// It draws from the same per-eye inputs as every skin (Face.eyes(), the skin contract), so it still
// blinks, emotes and follows the gaze: the display is CLIPPED TO THE EYE BODY, which is what makes
// it read as eyes doing something rather than as a video playing behind a face. An emote therefore
// shapes it for free -- SQUINT flattens the bursts into a letterbox, SLEEPY lowers the lid over them.
//
// WHY IT IS A PURE FUNCTION OF TIME. Every rocket's whole life is computed from `t` and its index,
// with no state carried between frames. Three reasons, in the order they matter:
//   * the face can be drawn at any rate, or skipped for seconds while a tab is hidden, and the
//     display is identical to one that ran every frame -- an accumulating particle system drifts or
//     catches up in a rush (the counter-spin in asset.js needed an integrator and had to seed and
//     latch it; not needing one at all is better);
//   * selecting the skin twice looks the same as selecting it once, because there is nothing to
//     reset;
//   * it is testable headlessly -- the state at a chosen moment can simply be asked for.
//
// The rainbow ignores the robot's state colour, which would normally be a problem: you must be able
// to tell a fault by the face. It is not a problem here because a reserved state (DEGRADED, E_STOP)
// draws `default` whatever is chosen -- main.js enforces it, and this skin cannot be shown then. For
// every other state the base glow below still carries the state colour.

export const name = "fireworks";

const PERIOD = 0.52;        // seconds between launches, per eye
const RISE = 0.30;          // how long the rocket climbs before it blooms
const LIFE = 1.70;          // a rocket's whole life, so about three overlap
const SPARKS = 16;          // particles per burst
const GRAVITY = 0.55;       // in eye-heights per second squared; what makes it a willow
const DRAG = 1.9;           // the slowing that makes the trails droop rather than fly straight

// A cheap deterministic hash: the same rocket must look the same every frame, since nothing is
// stored between them. Math.random() here would make every frame a different firework.
const rnd = (i, salt) => {
  const x = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return x - Math.floor(x);
};

export function draw(ctx, e, t) {
  const { cx, cy, w, h } = e;
  const open = 1 - e.lid;

  // Shut: a line, as a closed eye -- the same shape the ring skin draws, so a blink reads the same
  // whichever skin is on.
  if (open * (h / w) < 0.02) {
    ctx.strokeStyle = e.colour; ctx.lineWidth = 10; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(cx - w * 0.42, cy); ctx.lineTo(cx + w * 0.42, cy); ctx.stroke();
    return;
  }

  const x = cx - w / 2, y = cy - h / 2;
  ctx.save();
  // The eye body, as the window everything happens inside.
  const r = Math.max(0, Math.min(e.radius, w / 2, h / 2));
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
  ctx.clip();

  // A NIGHT SKY, not a wash. The state colour still has to be readable behind the show, but filling
  // the eye with `e.dim` (35% of it, which is bright) swamped the bursts: with `lighter` below,
  // saturated hues added onto a strong teal all came out the same pale blue, and the rainbow -- the
  // whole point of the skin -- was gone. So the tint is deep at the top and falls to near black,
  // which reads as sky, keeps the state legible, and leaves the hues somewhere to be bright against.
  const [cr, cg, cb] = e.rgb;
  const sky = ctx.createLinearGradient(0, y, 0, y + h);
  sky.addColorStop(0, `rgb(${(cr * 0.16) | 0},${(cg * 0.16) | 0},${(cb * 0.16) | 0})`);
  sky.addColorStop(1, `rgb(${(cr * 0.04) | 0},${(cg * 0.04) | 0},${(cb * 0.04) | 0})`);
  ctx.fillStyle = sky;
  ctx.fillRect(x, y, w, h);

  // The eyes look where the face looks; a small shift, as the asset skins do, so the bursts sit
  // toward the gaze instead of the display being nailed to the frame.
  const gx = e.gazeX * w * 0.10, gy = e.gazeY * h * 0.10;
  // The two eyes must not fire in lockstep -- that reads as one mechanism, not two eyes. Half a
  // period apart, which keeps them obviously related.
  const phase = e.side === "right" ? PERIOD / 2 : 0;
  const first = Math.floor((t - phase) / PERIOD) - Math.ceil(LIFE / PERIOD);

  ctx.globalCompositeOperation = "lighter";           // bursts add, so overlaps brighten
  for (let k = first; k <= Math.floor((t - phase) / PERIOD); k++) {
    const u = t - phase - k * PERIOD;                 // this rocket's own age
    if (u < 0 || u >= LIFE) continue;

    // Where it launches from and climbs to. Across the eye, never off its own edge.
    const lx = x + w * (0.25 + 0.5 * rnd(k, 1));
    const apexY = y + h * (0.22 + 0.26 * rnd(k, 2));
    const hue = Math.floor(360 * rnd(k, 3));

    if (u < RISE) {
      // Climbing: a bright head with a short tail, easing as it runs out of push.
      const p = u / RISE, ease = 1 - (1 - p) * (1 - p);
      const hy = y + h - (y + h - apexY) * ease;
      const tail = h * 0.18 * (1 - p);
      const g = ctx.createLinearGradient(lx + gx, hy + gy, lx + gx, hy + tail + gy);
      g.addColorStop(0, `hsla(${hue},100%,78%,${0.95 * open})`);
      g.addColorStop(1, `hsla(${hue},100%,60%,0)`);
      ctx.strokeStyle = g; ctx.lineWidth = Math.max(2, w * 0.018); ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(lx + gx, hy + gy); ctx.lineTo(lx + gx, hy + tail + gy); ctx.stroke();
      continue;
    }

    // Bloomed: each spark flies out, is slowed by drag and pulled down, leaving a tapering trail.
    const b = u - RISE, span = LIFE - RISE;
    const fade = Math.max(0, 1 - b / span);
    const slow = (1 - Math.exp(-DRAG * b)) / DRAG;    // the distance travelled under drag
    for (let s = 0; s < SPARKS; s++) {
      const a = (s / SPARKS) * Math.PI * 2 + rnd(k, 7) * Math.PI;
      const speed = h * (0.55 + 0.45 * rnd(k * 31 + s, 5));
      const px = lx + Math.cos(a) * speed * slow;
      const py = apexY + Math.sin(a) * speed * slow + GRAVITY * h * b * b;
      // A trail back toward where the spark was a moment ago: the willow's droop. A fixed lookback
      // in TIME, not a fixed length, so a fast spark streaks and a dying one shortens by itself.
      const bp = Math.max(0, b - 0.17);
      const slowP = (1 - Math.exp(-DRAG * bp)) / DRAG;
      const qx = lx + Math.cos(a) * speed * slowP;
      const qy = apexY + Math.sin(a) * speed * slowP + GRAVITY * h * bp * bp;

      // The tail-off is not linear, but squaring it dimmed the burst for most of its life and the
      // rainbow -- the brief -- read as muted olive. 1.4 keeps the late fade while the bloom stays
      // vivid, which is what a show's big moment is for.
      const alpha = Math.pow(fade, 1.4) * open;
      const g = ctx.createLinearGradient(px + gx, py + gy, qx + gx, qy + gy);
      g.addColorStop(0, `hsla(${(hue + s * 14) % 360},100%,72%,${alpha})`);
      g.addColorStop(1, `hsla(${(hue + s * 14) % 360},100%,46%,0)`);
      ctx.strokeStyle = g;
      ctx.lineWidth = Math.max(2.5, w * 0.026 * fade + 1.5);
      ctx.beginPath(); ctx.moveTo(px + gx, py + gy); ctx.lineTo(qx + gx, qy + gy); ctx.stroke();
    }
  }
  ctx.restore();
}
