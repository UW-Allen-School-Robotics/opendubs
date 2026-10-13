// ARMED: the sign that a show will start when the operator holds the deadman.
//
// The people this is for are standing next to a robot that is about to move, so the checks here are
// about the failures that would matter to them: a sign that stays up after the runner dies, a sign
// that could be mistaken for E_STOP, a countdown that freezes, and a sign that a full-face photo
// could hide.
//
// Run: node software/face-web/tests/test_armed.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { drawArmed } = await import(join(here, "../engine/skins/armed.js"));
const { STATES } = await import(join(here, "../engine/vocab.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// Records what was drawn, including the colours and the text.
function rec() {
  const log = [], fills = [], strokes = [], texts = [];
  const r = (n) => (...a) => log.push(n + "(" + a.join(",") + ")");
  const ctx = {
    log, fills, strokes, texts,
    save: r("save"), restore: r("restore"), strokeRect: r("strokeRect"), fillRect: r("fillRect"),
    beginPath: r("beginPath"), fill: r("fill"), stroke: r("stroke"), rect: r("rect"),
    fillText: (t, x, y) => { texts.push({ t: String(t), x, y, px: ctx._px }); log.push(`fillText(${t},${x},${y})`); },
    measureText: (t) => ({ width: String(t).length * 72 }),     // ~ a bold 132px digit
  };
  Object.defineProperty(ctx, "fillStyle", { set(v) { fills.push(String(v)); } });
  Object.defineProperty(ctx, "strokeStyle", { set(v) { strokes.push(String(v)); } });
  ctx._px = 0;
  Object.defineProperty(ctx, "font", { set(v) { const m = String(v).match(/(\d+)px/); ctx._px = m ? +m[1] : 0; } });
  for (const k of ["lineWidth", "textAlign", "textBaseline"]) Object.defineProperty(ctx, k, { set() {} });
  return ctx;
}

// ---- 1. nothing is drawn unless armed -----------------------------------------------------------
{
  const c = rec(); drawArmed(c, null, 1.0);
  ok(c.log.length === 0, "null draws absolutely nothing", `${c.log.length} calls`);
  const u = rec(); drawArmed(u, undefined, 1.0);
  ok(u.log.length === 0, "and so does undefined");
}

// ---- 2. it is unmistakable, and says what it is -------------------------------------------------
{
  const c = rec(); drawArmed(c, "wait", 1.0);
  ok(c.log.some((l) => l.startsWith("strokeRect")), "a frame is drawn around the whole face");
  ok(c.texts.some((t) => t.t === "ARMED"), "and the word ARMED", c.texts.map((t) => t.t).join(","));
  // the frame spans the design area, not a corner badge
  const sr = c.log.find((l) => l.startsWith("strokeRect"));
  const [x, y, w, h] = sr.slice(11, -1).split(",").map(Number);
  ok(w > 1200 && h > 650, "the frame is full-width, not a badge", `${w.toFixed(0)}x${h.toFixed(0)} at ${x},${y}`);
}

// ---- 3. distinct from E_STOP and DEGRADED -------------------------------------------------------
// Those recolour the EYES red and amber; this is a violet FRAME. Both differences matter, because
// either alone could still be half-read as the other across a room.
//
// MEASURED BY HUE, not by RGB distance. An earlier version of this check used the sum of channel
// differences and called violet "too close" to INIT's pale blue-white -- by one unit, on a metric
// where most of the gap sat in a channel it under-weights. Two colours are confusable when they sit
// at the same place on the colour wheel, so that is what is measured. The right response to a
// failing check is not always to change the thing it measures.
{
  const c = rec(); drawArmed(c, "wait", 1.0);
  const rgb = [...c.strokes, ...c.fills]
    .map((s2) => s2.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/))
    .filter(Boolean).map((m) => m.slice(1).map(Number));
  ok(rgb.length > 0, "it draws in a stated colour", `${rgb.length} colours`);

  const hue = ([r, g, b]) => {
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (!d) return 0;
    const h = mx === r ? ((g - b) / d + (g < b ? 6 : 0)) : mx === g ? ((b - r) / d + 2) : ((r - g) / d + 4);
    return h * 60;
  };
  const apart = (a, b) => { const d = Math.abs(hue(a) - hue(b)); return Math.min(d, 360 - d); };

  for (const [name, col] of [["E_STOP", [0xff, 0x4d, 0x4d]], ["DEGRADED", [0xf3, 0xb6, 0x4a]]]) {
    const worst = Math.min(...rgb.map((v) => apart(v, col)));
    ok(worst >= 60, `a different colour from ${name}, by a wide margin`, `${worst.toFixed(0)} degrees of hue apart`);
  }
  // and not near ANY robot state, so it cannot read as "the robot is in some state"
  let worst = 360, who = "";
  for (const [name, , col] of STATES) {
    const d = Math.min(...rgb.map((v) => apart(v, [(col >> 16) & 255, (col >> 8) & 255, col & 255])));
    if (d < worst) { worst = d; who = name; }
  }
  ok(worst >= 40, "nor near any robot state's colour", `closest is ${who}, ${worst.toFixed(0)} degrees away`);
}

// ---- 4. nothing travels ---------------------------------------------------------------------------
// A sweep, chase or spinner reads as the machine running. The frame must breathe IN PLACE: its
// geometry identical at every instant, only its alpha changing.
{
  const geo = new Set(), alpha = new Set();
  for (let t = 0; t < 4; t += 0.05) {
    const c = rec(); drawArmed(c, "wait", t);
    geo.add(c.log.filter((l) => !l.startsWith("fillText")).join("|"));
    c.strokes.forEach((s) => alpha.add(s));
  }
  ok(geo.size === 1, "the frame's geometry never changes -- nothing moves along it", `${geo.size} distinct geometries`);
  ok(alpha.size > 10, "only its brightness breathes", `${alpha.size} distinct alphas`);
}

// ---- 5. the countdown counts, and fits between the eyes -------------------------------------------
{
  for (const [secs, want] of [[3, "3"], [2.4, "3"], [10, "10"], [1, "1"]]) {
    const c = rec(); drawArmed(c, secs, 1.0);
    ok(c.texts.some((t) => t.t === want), `${secs}s shows "${want}"`, c.texts.map((t) => t.t).join(","));
  }
  // the numeral sits in the 163 px channel between the eyes' inner edges (558..722)
  const c = rec(); drawArmed(c, 10, 1.0);
  const num = c.texts.find((t) => t.t === "10");
  ok(num && num.x === 640, "the countdown is centred between the eyes", `x ${num && num.x}`);
  ok(num && num.y > 200 && num.y < 520, "  and vertically in the eye channel", `y ${num && num.y}`);
}

// ---- 5b. NOTHING TOUCHES AN EDGE ------------------------------------------------------------------
// The check that was missing, and the bug it would have caught: the word sat at y 702, exactly on
// the frame's bottom stroke (694..710), with its glyphs reaching y 722 -- past the canvas's own 720
// edge, and visibly clipped on a real page in a window where the design area fills the height. The
// old test only asked whether the word cleared the quote and the mouth, which it did. Clearing what
// is BELOW you is not the same as being INSIDE the frame.
{
  const INSET = 10, THICK = 16, W = 1280, H = 720;
  const inner = { t: INSET + THICK, b: H - INSET - THICK, l: INSET + THICK, r: W - INSET - THICK };
  for (const armed of ["wait", 3, 10, 99]) {
    const c = rec(); drawArmed(c, armed, 1.0);
    for (const tx of c.texts) {
      // generous bounds for a middle baseline: real glyphs are shorter than the em
      const top = tx.y - tx.px * 0.6, bot = tx.y + tx.px * 0.6;
      const half = (tx.t.length * tx.px * 0.62) / 2;         // the recorder's own width model
      ok(top > inner.t && bot < inner.b && tx.x - half > inner.l && tx.x + half < inner.r,
         `"${tx.t}" (armed ${armed}) is wholly inside the frame`,
         `y ${top.toFixed(0)}..${bot.toFixed(0)} in ${inner.t}..${inner.b}`);
      ok(bot < H && top > 0, `  and inside the canvas`, `${top.toFixed(0)}..${bot.toFixed(0)} in 0..${H}`);
    }
  }
}

// ---- 6. THE LEASE: a dead runner must not leave it up for ever -------------------------------------
// The one failure that matters for a sign people stand next to.
{
  const f = new Face(); f.setState("SAFE_IDLE", 0);
  for (let t = 0; t < 500; t += 16) f.tick(t, 0.016);
  f.setArmed("wait", 500);
  ok(f.armedState(500) === "wait", "armed shows while it is renewed");
  ok(f.armedState(2900) === "wait", "  still, just inside the lease");
  ok(f.armedState(3600) === null, "and CLEARS ITSELF once the runner stops renewing", `${f.armedState(3600)}`);

  f.setArmed(null, 4000);
  ok(f.armedState(4000) === null, "OFF clears it immediately");
}

// ---- 7. a countdown counts down BETWEEN renewals ---------------------------------------------------
// Otherwise it freezes on the last number it was told and reads as stuck.
{
  const f = new Face(); f.setState("SAFE_IDLE", 0);
  f.setArmed(3, 1000);
  const a = f.armedState(1000), b = f.armedState(2000), c = f.armedState(2900);
  ok(Math.abs(a - 3) < 1e-9 && Math.abs(b - 2) < 1e-9 && Math.abs(c - 1.1) < 1e-9,
     "the countdown keeps counting between renewals", `${a} -> ${b} -> ${c}`);
  ok(f.armedState(4100) === null, "and it too expires rather than sitting at 0 for ever");
}

console.log(failed ? `\n${failed} FAILED` : "\nall armed checks passed");
process.exit(failed ? 1 : 0);
