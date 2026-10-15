// THE MONSTER SKINS, held to the things a show depends on while the face is a monster.
//
// test_draw_smoke.mjs already runs every one of them through every state and fails if anything
// throws. That is not enough on its own: a skin that quietly stopped blinking, or stopped following
// the gaze, or leaked a clip onto the eye beside it, would pass the smoke test and still be broken
// on stage. So this checks what the drawing MEANS, by recording the context calls and reading them.
//
// The structural check is the one worth having twice over. Every monster fills its eye body, CLIPS
// to it, draws its interior and restores -- and if a `ctx.restore()` went missing, the clip would
// survive into the second eye and the brow, and half the face would vanish. Counting saves against
// restores catches that; no amount of looking at a still frame reliably does.
//
// Run: node software/face-web/tests/test_monsters.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { CODE_SKINS } = await import(join(here, "../engine/skins/index.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

const NAMES = ["monster_stitched", "monster_vampire", "monster_wolf", "monster_mummy",
               "monster_zombie", "monster_ghoul", "monster_lab"];

// A context that RECORDS every call and argument, as test_mouth.mjs does, so a drawing can be read
// back instead of looked at.
function recorder() {
  const log = [];
  const rec = (name) => (...a) => { log.push(name + "(" + a.map((v) => (typeof v === "object" ? "{}" : String(v))).join(",") + ")"); };
  const grad = { addColorStop: rec("grad.addColorStop") };
  const ctx = {
    log,
    save: rec("save"), restore: rec("restore"), beginPath: rec("beginPath"), closePath: rec("closePath"),
    fill: rec("fill"), stroke: rec("stroke"), clip: rec("clip"), moveTo: rec("moveTo"), lineTo: rec("lineTo"),
    quadraticCurveTo: rec("quadraticCurveTo"), arc: rec("arc"), rect: rec("rect"), roundRect: rec("roundRect"),
    fillRect: rec("fillRect"), drawImage: rec("drawImage"), translate: rec("translate"), scale: rec("scale"),
    rotate: rec("rotate"),
    createLinearGradient: (...a) => { log.push("createLinearGradient(" + a.join(",") + ")"); return grad; },
    createRadialGradient: (...a) => { log.push("createRadialGradient(" + a.join(",") + ")"); return grad; },
    ellipse: rec("ellipse"), bezierCurveTo: rec("bezierCurveTo"), setTransform: rec("setTransform"),
    clearRect: rec("clearRect"), fillText: rec("fillText"), transform: rec("transform"),
  };
  for (const k of ["fillStyle", "strokeStyle", "lineWidth", "lineCap", "globalCompositeOperation"])
    Object.defineProperty(ctx, k, { set(v) { log.push(k + "=" + (typeof v === "object" ? "{}" : v)); } });
  return ctx;
}

// One eye's inputs, from a REAL face rather than a hand-written object, so a field the engine adds
// later arrives here too -- taken ONCE and then frozen.
//
// Once, because the engine is full of randomness (saccades, micro-movement, blink and emote
// timers) and two runs of it do not agree. Every expression channel is then pinned to a known
// value, so a difference between two logs below is the SKIN's doing and nothing else's.
const BASE = (() => {
  const f = new Face();
  f.setState("SAFE_IDLE", 0);
  f.setEmote("NEUTRAL", 0);
  for (let t = 0; t < 1200; t += 16) f.tick(t, 0.016);
  const [l] = f.eyes();
  return Object.freeze({ ...l, gazeX: 0, gazeY: 0, lid: 0, lidLower: 0, lidCurve: 0,
                         browRaise: 0, browAngle: 0, mouth: null });
})();

function eyeInput(over = {}) { return { ...BASE, ...over }; }

function render(skin, over = {}, t = 2) {
  const ctx = recorder();
  skin.draw(ctx, eyeInput(over), t);
  return ctx.log;
}

// Every circle and ellipse centre in a log, in order: the pupils, catchlights, voids and bubbles.
function circles(log) {
  const out = [];
  for (const line of log) {
    const m = /^(arc|ellipse)\(([-\d.e]+),([-\d.e]+),/.exec(line);
    if (m) out.push({ x: +m[2], y: +m[3] });
  }
  return out;
}

// ---- 1. they are registered, under their own names ----------------------------------------------
for (const n of NAMES) {
  const s = CODE_SKINS[n];
  ok(!!s && typeof s.draw === "function", `'${n}' is a code skin with a draw()`);
  ok(s && s.name === n, `'${n}' reports its own name`, s && s.name);
}
ok(new Set(NAMES.map((n) => CODE_SKINS[n])).size === NAMES.length, "the seven are seven different skins");

// ---- 2. nothing leaks out of an eye -------------------------------------------------------------
// The clip is what keeps a monster's interior inside its own eye; the matching restore is what keeps
// it off the eye beside it. Checked under a shut lid too, where an early return would be tempting.
for (const n of NAMES) {
  for (const [what, over] of [["open", {}], ["shut", { lid: 1 }], ["gazing", { gazeX: 1, gazeY: -1 }]]) {
    const log = render(CODE_SKINS[n], over);
    const saves = log.filter((l) => l === "save()").length;
    const restores = log.filter((l) => l === "restore()").length;
    ok(saves === restores && saves > 0, `'${n}' balances save/restore (${what})`, `${saves}/${restores}`);
    const clipAt = log.indexOf("clip()");
    const firstCircle = log.findIndex((l) => /^(arc|ellipse)\(/.test(l));
    ok(clipAt >= 0 && (firstCircle < 0 || clipAt < firstCircle),
       `'${n}' clips to the eye before drawing inside it (${what})`, `clip@${clipAt} first circle@${firstCircle}`);
  }
}

// ---- 3. the gaze still moves the eye ------------------------------------------------------------
// Both axes, and in the right direction: a sign slip here would have the face looking away from
// whoever it is tracking, which reads as a working face doing the wrong thing.
for (const n of NAMES) {
  const l = circles(render(CODE_SKINS[n], { gazeX: -1 }));
  const r = circles(render(CODE_SKINS[n], { gazeX: 1 }));
  const u = circles(render(CODE_SKINS[n], { gazeY: -1 }));
  const d = circles(render(CODE_SKINS[n], { gazeY: 1 }));
  ok(l.length === r.length && l.length > 0, `'${n}' draws the same shapes whichever way it looks`, l.length);
  // The widest-moving shape is the pupil; `monster_lab`'s bubbles do not move with the gaze, which
  // is why this takes the maximum rather than insisting every shape moves.
  const dx = Math.max(...r.map((c, i) => c.x - l[i].x));
  const dy = Math.max(...d.map((c, i) => c.y - u[i].y));
  ok(dx > 20, `'${n}' looks left and right`, `${dx.toFixed(1)} px`);
  ok(dy > 10, `'${n}' looks up and down`, `${dy.toFixed(1)} px`);
}

// ---- 4. the lids -------------------------------------------------------------------------------
// A monster that stopped blinking would look like a stare, which is a thing a face does on purpose,
// so nobody would report it as broken. Hence: the shut lid is drawn, it is BLACK, and its curved
// edge actually moves with `lidCurve`.
function lidEdges(log) {
  return log.filter((l) => l.startsWith("quadraticCurveTo(")).map((l) => l);
}
for (const n of NAMES) {
  const open = render(CODE_SKINS[n], { lid: 0 });
  const shut = render(CODE_SKINS[n], { lid: 1 });
  ok(lidEdges(open).length === 0, `'${n}' draws no lid when the eye is open`, lidEdges(open).length);
  ok(lidEdges(shut).length === 1, `'${n}' draws the upper lid when the eye is shut`, lidEdges(shut).length);
  ok(shut.includes("fillStyle=#000"), `'${n}' draws its lid in black`);
  const lower = render(CODE_SKINS[n], { lid: 0.3, lidLower: 0.3 });
  ok(lidEdges(lower).length === 2, `'${n}' draws a lower lid too`, lidEdges(lower).length);
  const flat = lidEdges(render(CODE_SKINS[n], { lid: 0.5, lidCurve: 0 }))[0];
  const bowed = lidEdges(render(CODE_SKINS[n], { lid: 0.5, lidCurve: 1 }))[0];
  ok(flat !== bowed, `'${n}' bows its lid edge with lidCurve`, flat);
}

// ---- 5. the brow still carries the emote --------------------------------------------------------
// The brow is drawn last, outside the clip, so its first moveTo is the one to read.
function browY(log) {
  const i = log.map((l) => l.startsWith("moveTo(")).lastIndexOf(true);
  return i < 0 ? NaN : +/^moveTo\(([-\d.e]+),([-\d.e]+)\)/.exec(log[i])[2];
}
for (const n of NAMES) {
  const down = browY(render(CODE_SKINS[n], { browRaise: 0 }));
  const up = browY(render(CODE_SKINS[n], { browRaise: 1 }));
  ok(Number.isFinite(down) && up < down - 10, `'${n}' raises its brow with browRaise`, `${down.toFixed(1)} -> ${up.toFixed(1)}`);
  // Read at the OUTER end, which is the end that moves: eyes.js pivots the brow about its inner
  // end (the `- Math.abs(half)` in the baseline cancels the `+ half` there), so an inner-end check
  // would pass for a skin that ignored the angle entirely. The monsters keep that pivot.
  const level = browY(render(CODE_SKINS[n], { browAngle: 0 }));
  const tipped = browY(render(CODE_SKINS[n], { browAngle: 20 }));
  ok(Math.abs(level - tipped) > 10, `'${n}' tips its brow with browAngle`, `${level.toFixed(1)} -> ${tipped.toFixed(1)}`);
}

// ---- 6. nothing accumulates ---------------------------------------------------------------------
// The reason fireworks is a pure function of time, applied to the one monster that moves: a skipped
// frame or a hidden tab must not change what is on screen, and re-selecting a skin must not reset
// anything, because there is nothing to reset.
for (const n of NAMES) {
  const a = render(CODE_SKINS[n], {}, 3.5);
  const b = render(CODE_SKINS[n], {}, 3.5);
  ok(JSON.stringify(a) === JSON.stringify(b), `'${n}' draws the same thing twice from the same inputs`);
}
const lab0 = render(CODE_SKINS.monster_lab, {}, 0.0);
const lab1 = render(CODE_SKINS.monster_lab, {}, 1.3);
const lab0again = render(CODE_SKINS.monster_lab, {}, 0.0);
ok(JSON.stringify(lab0) !== JSON.stringify(lab1), "'monster_lab' bubbles move with t");
ok(JSON.stringify(lab0) === JSON.stringify(lab0again), "'monster_lab' at the same t is the same frame, however it was reached");

// ---- 7. they are actually different faces -------------------------------------------------------
// Seven names pointing at one drawing would pass every check above.
const drawings = new Map();
for (const n of [...NAMES, "default"]) drawings.set(n, JSON.stringify(render(CODE_SKINS[n], {})));
ok(new Set(drawings.values()).size === drawings.size, "each monster draws something different, and none of them is the default eyes");

// The mismatched one, specifically: its two eyes must NOT be mirror images, which is the design.
const zl = JSON.stringify(circles(render(CODE_SKINS.monster_zombie, { side: "left" })).map((c) => c.y));
const zr = JSON.stringify(circles(render(CODE_SKINS.monster_zombie, { side: "right" })).map((c) => c.y));
ok(zl !== zr, "'monster_zombie' has two different eyes, not one mirrored twice", `${zl} vs ${zr}`);

console.log(failed ? `\n${failed} FAILED` : "\nall monster checks passed");
process.exit(failed ? 1 : 0);
