// SEVEN MONSTER SKINS, for a Monster Mash show. Each is an ordinary code skin -- `SKIN
// monster_wolf` -- so a show changes monster on a bar line and the face keeps blinking, gazing,
// emoting and taking MOVEs all the way through. That is the whole reason these are code and not
// library stills: three minutes of song spent looking at a frozen picture is a dead face.
//
// ORIGINAL DESIGNS, DELIBERATELY. The folklore is public domain -- Shelley's novel, Stoker's, and
// the wolf and the walking dead are older than either -- but the famous 1931 FILM MAKEUP is not,
// and neither is any studio's character art or any performer's likeness. So nothing here copies
// one: no flat-topped skull, no neck bolts, no particular hairline, no one's face. These are the
// archetypes, built from the folklore. It helps that a skin is only ever two eyes and two brows:
// there is no skull to shape and no costume to draw, so the parts of those designs that belong to
// someone are not even reachable from here.
//
// THE PALETTE IS THE SKIN'S, not the robot's state colour, and that is the same trade fireworks.js
// makes for the same reason: a reserved state (DEGRADED, E_STOP) draws `default` whatever is
// chosen -- main.js enforces it -- so a monster can never be what hides a fault. In every other
// state the face is being a monster on purpose.
//
// WHAT EVERY ONE OF THEM STILL OBEYS, because a show's vocabulary has to keep working while the
// face is a wolf: the lids (upper, lower and their curve, drawn with eyes.js's quadratic edge, so
// a blink and a SQUINT read as they do on the default eyes), the gaze (the pupil travels and stays
// off the rim), the brow (`browRaise` lifts it and `browAngle` tips it, with each monster's own
// shape added on top rather than replacing it), and the mouth when it is on. The one field they
// ignore is `sparkle`, which face-engine.md A allows.
//
// `monster_lab` is the only one that moves by itself, and like fireworks it is a PURE FUNCTION OF
// `t`: nothing accumulates between frames, so a hidden tab, a skipped frame and re-selecting the
// skin all look identical.

import { drawMouth } from "./mouth.js";

const RIM_PAD = 14;                        // how close to the rim a pupil may travel (as eyes.js)
const BROW_W = 0.86, BROW_GAP = 0.10, BROW_RANGE = 0.14;
const CURVE_FRAC = 0.22;

function shellPath(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
}

// The eye body in the monster's own colours, CLIPPED so everything drawn after it stays inside the
// eye. Returns the geometry the interior and the lids need. Every skin below pairs this with the
// `ctx.restore()` after its lids.
function shell(ctx, e, top, bottom) {
  const { cx, cy, w, h } = e;
  const x = cx - w / 2, y = cy - h / 2;
  ctx.save();
  shellPath(ctx, x, y, w, h, e.radius);
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fill();
  ctx.clip();
  return { x, y, w, h, cx, cy };
}

// Where the pupil sits for this gaze, kept off the rim exactly as the default eyes keep it.
function pupilAt(e, frac) {
  const pw = e.w * frac;
  return {
    pw,
    px: e.cx + e.gazeX * Math.max(0, (e.w - pw) / 2 - RIM_PAD),
    py: e.cy + e.gazeY * Math.max(0, (e.h - pw) / 2 - RIM_PAD),
  };
}

// eyes.js's lid, copied rather than shared so the two can diverge: a filled path with a QUADRATIC
// edge, a positive `lidCurve` drooping the upper lid's middle and bulging the lower one's.
function lidPath(ctx, g, amount, curve, fromTop) {
  if (amount <= 0.0005) return;
  const { x, y, w, h } = g;
  const edge = fromTop ? y + amount * h : y + h - amount * h;
  const bow = curve * h * CURVE_FRAC * (fromTop ? 1 : -1);
  ctx.beginPath();
  ctx.moveTo(x - 2, fromTop ? y - 2 : y + h + 2);
  ctx.lineTo(x + w + 2, fromTop ? y - 2 : y + h + 2);
  ctx.lineTo(x + w + 2, edge);
  ctx.quadraticCurveTo(x + w / 2, edge + bow * 2, x - 2, edge);
  ctx.closePath();
  ctx.fill();
}

// Both lids, in black, over whatever the monster drew inside. `extra` hangs one monster's upper lid
// lower without taking the blink away from underneath it.
function lids(ctx, e, g, extra = 0) {
  const curve = Math.max(-1, Math.min(1, e.lidCurve ?? 0));
  ctx.fillStyle = "#000";
  lidPath(ctx, g, Math.max(0, Math.min(1, (e.lidUpper ?? e.lid ?? 0) + extra)), curve, true);
  lidPath(ctx, g, Math.max(0, Math.min(1, e.lidLower ?? 0)), curve, false);
}

// A brow. `thick` and `bias` are the monster's character; `browRaise` and `browAngle` still come
// from the emote, so ALERT and SQUINT read on a monster as they do on the default eyes. `tuft`
// breaks the line into a shaggy zigzag -- from the index, not from Math.random, so it does not
// shimmer frame to frame.
function brow(ctx, e, colour, thick, bias, tuft = 0) {
  const { cx, cy, w, h } = e;
  const bw = w * BROW_W, bh = Math.max(6, thick * h);
  const angle = (e.browAngle ?? 0) + bias;
  const half = bw * Math.tan(angle * Math.PI / 180) / 2;
  const by = cy - h / 2 - h * BROW_GAP - bh / 2 - Math.abs(half) - (e.browRaise ?? 0) * h * BROW_RANGE;
  const inner = e.side === "left" ? cx + bw / 2 : cx - bw / 2;
  const outer = e.side === "left" ? cx - bw / 2 : cx + bw / 2;
  ctx.strokeStyle = colour; ctx.lineWidth = bh; ctx.lineCap = "round";
  ctx.beginPath();
  if (!tuft) {
    ctx.moveTo(outer, by - half); ctx.lineTo(inner, by + half);
  } else {
    const n = 8;
    for (let i = 0; i <= n; i++) {
      const u = i / n;
      const px = outer + (inner - outer) * u;
      const py = by - half + half * 2 * u + (i % 2 ? -tuft * bh : tuft * bh);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    }
  }
  ctx.stroke();
}

// Every skin has the same outer shape: the mouth once, on the left-eye pass and BEFORE anything
// else (as in eyes.js and ring.js, so no early return can skip it), then the eye.
function monster(name, eye) {
  return {
    name,
    draw(ctx, e, t) {
      if (e.side === "left" && e.mouth) drawMouth(ctx, e.mouth.cx, e.mouth.level, e.colour, e.dim);
      eye(ctx, e, t || 0);
    },
  };
}

// A deterministic hash, as fireworks.js uses: the same bubble must look the same every frame.
const rnd = (i, salt) => {
  const x = Math.sin(i * 127.1 + salt * 311.7) * 43758.5453;
  return x - Math.floor(x);
};

// ---- the seven ---------------------------------------------------------------------------------

// A STITCHED THING. A dull green eye with a sickly glow, a surgical seam across it, and a heavy
// brow that is FLAT rather than angled -- flat reads as brute where angled reads as angry, and it
// is the brow, not a skull shape, that carries this one.
export const monster_stitched = monster("monster_stitched", (ctx, e) => {
  const g = shell(ctx, e, "#4d7a3a", "#16250f");
  const p = pupilAt(e, 0.30);
  ctx.fillStyle = "#0b140a";
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(190,255,160,0.85)";
  ctx.beginPath(); ctx.arc(p.px - p.pw / 6, p.py - p.pw / 6, Math.max(2, p.pw / 7), 0, Math.PI * 2); ctx.fill();

  // The seam: a line across the eye with ticks through it, high enough that the pupil still reads.
  const sy = g.y + g.h * 0.30;
  ctx.strokeStyle = "#1d3010"; ctx.lineWidth = Math.max(3, g.h * 0.016); ctx.lineCap = "butt";
  ctx.beginPath(); ctx.moveTo(g.x, sy); ctx.lineTo(g.x + g.w, sy); ctx.stroke();
  for (let i = 1; i < 7; i++) {
    const sx = g.x + (g.w * i) / 7;
    ctx.beginPath(); ctx.moveTo(sx, sy - g.h * 0.05); ctx.lineTo(sx, sy + g.h * 0.05); ctx.stroke();
  }

  lids(ctx, e, g);
  ctx.restore();
  brow(ctx, e, "#2b4a1d", 0.14, 0);
});

// BLOOD-EYED. A red iris round a black pupil, a cold white glint, and a steeply arched brow. No
// cape and no hairline: at this size the eyes do all of it anyway.
export const monster_vampire = monster("monster_vampire", (ctx, e) => {
  const g = shell(ctx, e, "#2a1016", "#0a0406");
  const p = pupilAt(e, 0.42);
  ctx.fillStyle = "#b3121d";
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#1a0306";
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 4, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.95)";
  ctx.beginPath(); ctx.arc(p.px - p.pw / 5, p.py - p.pw / 5, Math.max(2, p.pw / 10), 0, Math.PI * 2); ctx.fill();
  lids(ctx, e, g);
  ctx.restore();
  // The bias is MIRRORED by side, because the inner/outer swap already mirrors the line: one signed
  // angle for both eyes would arch one brow and droop the other.
  brow(ctx, e, "#1c0a0e", 0.085, e.side === "left" ? 15 : -15);
});

// A WOLF. Amber, with a vertical slit for a pupil, and a brow broken into tufts.
export const monster_wolf = monster("monster_wolf", (ctx, e) => {
  const g = shell(ctx, e, "#c8871c", "#3a2206");
  const p = pupilAt(e, 0.46);
  ctx.fillStyle = "#120a02";
  ctx.beginPath();
  ctx.ellipse(p.px, p.py, Math.max(2, p.pw * 0.14), p.pw / 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(255,232,170,0.9)";
  ctx.beginPath(); ctx.arc(p.px - p.pw / 5, p.py - p.pw / 4, Math.max(2, p.pw / 11), 0, Math.PI * 2); ctx.fill();
  lids(ctx, e, g);
  ctx.restore();
  brow(ctx, e, "#4a3212", 0.10, e.side === "left" ? 6 : -6, 0.35);
});

// WRAPPED. Bandage bands cross the eye at an angle with one band's worth of GAP across the middle,
// so there is always something to peer out of and a blink still reads underneath.
export const monster_mummy = monster("monster_mummy", (ctx, e) => {
  const g = shell(ctx, e, "#6b5a3e", "#1d1710");
  const p = pupilAt(e, 0.26);
  ctx.fillStyle = "#07070a";
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(255,240,200,0.7)";
  ctx.beginPath(); ctx.arc(p.px - p.pw / 6, p.py - p.pw / 6, Math.max(2, p.pw / 6), 0, Math.PI * 2); ctx.fill();

  ctx.save();
  ctx.translate(g.cx, g.cy); ctx.rotate(-0.28); ctx.translate(-g.cx, -g.cy);
  const band = g.h * 0.17;
  for (let i = -4; i <= 4; i++) {
    if (i === 0) continue;                            // the gap it looks out of
    ctx.fillStyle = i % 2 ? "#d8ccb0" : "#c0b094";
    ctx.fillRect(g.x - g.w, g.cy + i * band * 1.25 - band / 2, g.w * 3, band);
  }
  ctx.restore();

  lids(ctx, e, g);
  ctx.restore();
  brow(ctx, e, "#4a3f2c", 0.07, 0);
});

// MISMATCHED, which is the whole idea: one eye has a small low pupil and a hanging lid, the other a
// wide one, and the brows disagree. This is the only skin that reads `e.side` to be DIFFERENT
// rather than merely mirrored.
export const monster_zombie = monster("monster_zombie", (ctx, e) => {
  const g = shell(ctx, e, "#7d9152", "#1b2411");
  const odd = e.side === "right";
  const p = pupilAt(e, odd ? 0.22 : 0.38);
  const px = p.px + (odd ? g.w * 0.06 : 0);
  const py = p.py + (odd ? g.h * 0.10 : -g.h * 0.03);
  ctx.fillStyle = "#0d1208";
  ctx.beginPath(); ctx.arc(px, py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(220,255,190,0.5)";
  ctx.beginPath(); ctx.arc(px - p.pw / 7, py - p.pw / 7, Math.max(2, p.pw / 9), 0, Math.PI * 2); ctx.fill();
  lids(ctx, e, g, odd ? 0.18 : 0);                    // one lid hangs, and still blinks from there
  ctx.restore();
  brow(ctx, e, "#3c4a22", 0.06, odd ? -7 : 5);        // deliberately NOT mirrored
});

// HOLLOW. Almost nothing inside: a pale rim round a void, with the faintest ember at the centre so
// the gaze is still readable. The emptiness is the design, so there is no pupil to speak of.
export const monster_ghoul = monster("monster_ghoul", (ctx, e) => {
  const g = shell(ctx, e, "#cfd6d2", "#6d7a74");
  const p = pupilAt(e, 0.62);
  const r = ctx.createRadialGradient(p.px, p.py, p.pw * 0.12, p.px, p.py, p.pw / 2);
  r.addColorStop(0, "#000");
  r.addColorStop(0.72, "#06080a");
  r.addColorStop(1, "rgba(10,14,16,0)");
  ctx.fillStyle = r;
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(150,200,210,0.32)";
  ctx.beginPath(); ctx.arc(p.px, p.py, Math.max(2, p.pw / 14), 0, Math.PI * 2); ctx.fill();
  lids(ctx, e, g);
  ctx.restore();
  brow(ctx, e, "#8d9a94", 0.05, e.side === "left" ? -4 : 4);
});

// THE LABORATORY. Not a flask -- there is nowhere to put one -- but what a flask does: green
// bubbles rising through the eye and fading as they reach the top. A pure function of `t`, like
// fireworks, so it never drifts and never needs resetting.
export const monster_lab = monster("monster_lab", (ctx, e, t) => {
  const g = shell(ctx, e, "#1d4a33", "#061710");
  const BUBBLES = 14;
  for (let i = 0; i < BUBBLES; i++) {
    const speed = 0.10 + 0.22 * rnd(i, 1);
    const u = (t * speed + rnd(i, 2)) % 1;            // 0 at the bottom, 1 at the top
    const bx = g.x + g.w * (0.10 + 0.80 * rnd(i, 3));
    const by = g.y + g.h * (1 - u);
    const rr = Math.max(2, g.w * (0.012 + 0.030 * rnd(i, 4)) * (0.5 + u));
    ctx.fillStyle = `rgba(120,255,190,${(0.15 + 0.45 * (1 - u)).toFixed(3)})`;
    ctx.beginPath(); ctx.arc(bx, by, rr, 0, Math.PI * 2); ctx.fill();
  }
  const p = pupilAt(e, 0.28);
  ctx.fillStyle = "#04120c";
  ctx.beginPath(); ctx.arc(p.px, p.py, p.pw / 2, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgba(160,255,210,0.8)";
  ctx.beginPath(); ctx.arc(p.px - p.pw / 6, p.py - p.pw / 6, Math.max(2, p.pw / 7), 0, Math.PI * 2); ctx.fill();
  lids(ctx, e, g);
  ctx.restore();
  brow(ctx, e, "#14382a", 0.06, 0);
});
