// THE TRACKING RETICLE, held to the job it exists for.
//
// It is a diagnostic, so the bar is different from the other overlays: it is not enough that it
// looks right, it has to be TRUSTWORTHY WHEN EVERYTHING ELSE IS WRONG. That is the whole point --
// on 2026-10-15 the neck drove itself into the corner of its envelope and lost the person, and
// from the face it looked like "it tracked me, then stopped". The reticle is what turns that into
// two seconds of watching. A reticle that was itself unreliable would be worse than none, because
// it would be believed.
//
// So the checks are: it is drawn whenever there is a target (including for a kind nobody
// recognises -- a diagnostic must fail VISIBLY), it is never drawn when there is not, it stays on
// screen no matter where the target claims to be, it is legible over the colours it will sit on,
// and it never moves on its own.
//
// Run: node software/face-web/tests/test_reticle.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const { drawReticle, RETICLE } = await import(join(here, "../engine/skins/reticle.js"));
const { STATES } = await import(join(here, "../engine/vocab.js"));

const W = 1280, H = 720;
let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// Records the strokes in order, with their width, colour and the box they trace.
function recorder() {
  const strokes = [], log = [];
  let lw = 1, ss = "", box = null;
  const ctx = {
    log, strokes,
    save: () => log.push("save"), restore: () => log.push("restore"),
    beginPath: () => { log.push("beginPath"); box = null; },
    roundRect: (x, y, w, h, r) => { log.push("roundRect"); box = { x, y, w, h, r }; },
    rect: (x, y, w, h) => { log.push("rect"); box = { x, y, w, h, r: 0 } },
    stroke: () => { log.push("stroke"); strokes.push({ lw, ss, box }); },
    fill: () => { log.push("fill"); },
    fillRect: () => { log.push("fillRect"); },
    arc: () => log.push("arc"), moveTo: () => log.push("moveTo"), lineTo: () => log.push("lineTo"),
    closePath: () => log.push("closePath"), clip: () => log.push("clip"),
    quadraticCurveTo: () => log.push("q"), translate: () => log.push("translate"),
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
    fillText: () => log.push("fillText"),
  };
  Object.defineProperty(ctx, "lineWidth", { set(v) { lw = v; } });
  Object.defineProperty(ctx, "strokeStyle", { set(v) { ss = String(v); } });
  Object.defineProperty(ctx, "fillStyle", { set(v) { log.push("fillStyle=" + v); } });
  return ctx;
}
function draw(target, t = 0) { const c = recorder(); drawReticle(c, target, t); return c; }

// ---- 1. drawn when there is a target, never when there is not -----------------------------------
for (const kind of ["motion", "face", "person"]) {
  const c = draw({ x: 640, y: 360, kind });
  ok(c.strokes.length === 2, `'${kind}' draws the box`, `${c.strokes.length} strokes`);
}
for (const [what, tgt] of [["null", null], ["undefined", undefined]]) {
  ok(draw(tgt).log.length === 0, `draws NOTHING for ${what}`);
}
// A DIAGNOSTIC MUST FAIL VISIBLY. An unrecognised kind is still drawn -- a tracker reporting
// something new, or a typo in the caller, must not make the reticle silently vanish, because the
// absence would be read as "nothing detected", which is a different and wrong answer.
{
  const c = draw({ x: 640, y: 360, kind: "banana" });
  ok(c.strokes.length === 2, "an unrecognised kind is still SHOWN, not silently dropped");
}

// ---- 2. the two colours, and they are the Tab5's ------------------------------------------------
const colourOf = (kind) => draw({ x: 640, y: 360, kind }).strokes[1].ss;
ok(colourOf("motion").includes("255, 176, 32"), "motion is the Tab5's amber 0xffb020", colourOf("motion"));
ok(colourOf("face").includes("0, 229, 255"), "a face is the Tab5's cyan 0x00e5ff", colourOf("face"));
ok(colourOf("person") === colourOf("face"), "a person reads the same as a face: both are 'somebody'");
ok(colourOf("motion") !== colourOf("face"),
   "motion and a face are DIFFERENT -- without this, 'the detector works' and 'motion was on your face' look alike");

// ---- 3. legible over what it will actually sit on -----------------------------------------------
// The dark stroke is the whole mitigation, so it is checked structurally: drawn FIRST (so the
// colour lands on top of it) and WIDER (so it shows on both sides).
for (const kind of ["motion", "face"]) {
  const [under, over] = draw({ x: 640, y: 360, kind }).strokes;
  ok(/rgba\(0, 0, 0/.test(under.ss), `'${kind}': a dark stroke goes down first`, under.ss);
  ok(under.lw > over.lw, `'${kind}': and it is wider, so it shows on both sides`, `${under.lw} vs ${over.lw}`);
  ok(JSON.stringify(under.box) === JSON.stringify(over.box), `'${kind}': both trace the same box`);
}
// And the reason, stated as a measurement rather than an opinion: the cyan is on top of AUTONOMOUS.
{
  const hue = (c) => {
    const r = ((c >> 16) & 255) / 255, g = ((c >> 8) & 255) / 255, b = (c & 255) / 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    let h = 0;
    if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return ((h * 60) + 360) % 360;
  };
  const gap = (a, b) => { const d = Math.abs(hue(a) - hue(b)); return Math.min(d, 360 - d); };
  const autonomous = STATES.find((s) => s[0] === "AUTONOMOUS")[2];
  ok(gap(0x00e5ff, autonomous) < 20,
     "the cyan really is nearly AUTONOMOUS's colour, which is WHY the outline exists",
     `${gap(0x00e5ff, autonomous).toFixed(0)} degrees apart`);
}

// ---- 4. it stays on screen, wherever the target claims to be ------------------------------------
// Clamped by its EDGES, not its centre: a box clamped by the centre still hangs off the screen,
// which is the bug the ARMED label shipped with in #227.
const corners = [
  ["far off the left", { x: -900, y: 360 }], ["far off the right", { x: 3000, y: 360 }],
  ["far above", { x: 640, y: -500 }], ["far below", { x: 640, y: 2000 }],
  ["off the corner", { x: -50, y: -50 }],
  ["a huge detection", { x: 640, y: 360, w: 5000, h: 5000 }],
  ["a tiny detection", { x: 640, y: 360, w: 1, h: 1 }],
];
for (const [what, t] of corners) {
  const s = draw({ ...t, kind: "face" }).strokes;
  if (!s.length) { ok(false, `${what}: still draws something`); continue; }
  const b = s[1].box;
  const inside = b.x >= 0 && b.y >= 0 && b.x + b.w <= W && b.y + b.h <= H;
  const detail = `x ${b.x.toFixed(0)}..${(b.x + b.w).toFixed(0)}  y ${b.y.toFixed(0)}..${(b.y + b.h).toFixed(0)}`;
  ok(inside, `${what}: the whole box stays on screen`, detail);
  // AGAINST AN ABSOLUTE, not against RETICLE.pad. Measuring the box against the very constant
  // that positions it passes however small that constant becomes -- a mutation shrinking the pad
  // to 2 slipped through exactly that way. 12 is the real requirement: the charge gauge owns the
  // outermost 8 px, and a reticle sitting in it reads as a notch in the charge level.
  const CLEAR = 12;
  ok(b.x >= CLEAR && b.y >= CLEAR && b.x + b.w <= W - CLEAR && b.y + b.h <= H - CLEAR,
     `${what}: and clear of the charge gauge's band at the edge`, detail);
}

// A NON-FINITE POSITION IS NOT DRAWN, and that is deliberate rather than an oversight. NaN used to
// survive the clamp and produce a NaN box, which the canvas skips -- the reticle vanished with no
// sign, and for a diagnostic a silent absence is read as "nothing detected", a different and wrong
// answer. Clamping it to the centre would be worse: that invents a position. Nothing drawn is the
// honest answer, and whether a target exists at all is the caller's to know.
for (const [what, t] of [["NaN", { x: NaN, y: NaN }], ["a NaN in one axis", { x: 640, y: NaN }],
                         ["Infinity", { x: Infinity, y: 360 }], ["a string", { x: "640", y: 360 }]]) {
  ok(draw({ ...t, kind: "face" }).strokes.length === 0,
     `${what} draws nothing rather than inventing a position`);
}

// ---- 5. hollow, sized, and still ------------------------------------------------------------------
{
  const c = draw({ x: 640, y: 360, kind: "face" });
  ok(!c.log.includes("fill") && !c.log.includes("fillRect"), "it is HOLLOW: it never hides the face under it");
  const d = draw({ x: 640, y: 360, kind: "face" }).strokes[1].box;
  ok(d.w === RETICLE.size && d.h === RETICLE.size, "with no detection size it is the Tab5's 120", `${d.w}x${d.h}`);
  const e = draw({ x: 640, y: 360, kind: "face", w: 300, h: 200 }).strokes[1].box;
  ok(e.w === 300 && e.h === 200, "and it takes the detection's size when there is one", `${e.w}x${e.h}`);
  const at = (x) => draw({ x, y: 360, kind: "face" }).strokes[1].box.x;
  ok(at(400) < at(800), "it follows the target across the screen", `${at(400)} -> ${at(800)}`);
}
{
  // STILL ON PURPOSE. The one overlay whose job is to report motion faithfully must not add any.
  const a = JSON.stringify(draw({ x: 500, y: 300, kind: "motion" }, 0).strokes);
  const b = JSON.stringify(draw({ x: 500, y: 300, kind: "motion" }, 2.7).strokes);
  ok(a === b, "it does not animate: a reticle that moved on its own could not be read");
}

// ---- 6. where the box comes from ----------------------------------------------------------------
// Face.reticleTarget() exists so the centre is derived from THE GAZE THAT DRIVES THE EYES rather
// than from the detector's box. That is the property the whole diagnostic rests on: in a runaway
// the detector is RIGHT and the mapping is wrong, so a reticle fed from the detector sits happily
// on the person while the head drives into a wall.
{
  const { Face } = await import(join(here, "../engine/face.js"));
  // A tracker that keeps reporting, which is what a real one does at 5 Hz. The first version of
  // this helper set the target once and then ticked for a second and a half, and the staleness
  // guard correctly threw it away -- a good sign for the guard and a bad test.
  const settle = (f, at, tgt = null) => {
    for (let t = 0; t <= at; t += 16) {
      if (tgt && t % 160 === 0) f.look({ ...tgt, at: t });
      f.tick(t, 0.016);
    }
  };

  const f = new Face(); f.setState("SAFE_IDLE", 0);
  ok(f.reticleTarget(0) === null, "no target, no box: a reticle over an empty room would be a lie");

  settle(f, 1500, { x: 0.8, y: -0.4, conf: 1, kind: "face" });
  const r = f.reticleTarget(1500);
  ok(r !== null, "a target gives a box");
  ok(r.kind === "face", "and carries the cue's kind through", r && r.kind);
  ok(r.x > 640, "a target to the right puts the box right of centre", r && r.x.toFixed(0));
  ok(r.y < 360, "and one above centre puts it above", r && r.y.toFixed(0));

  // IT FOLLOWS THE TARGET, NOT THE EYES.
  const g = new Face(); g.setState("SAFE_IDLE", 0);
  settle(g, 1500, { x: -0.8, y: 0.4, conf: 1, kind: "person" });
  const l = g.reticleTarget(1500);
  ok(l.x < 640 && l.y > 360, "the opposite target puts the box on the opposite side",
     `${l.x.toFixed(0)},${l.y.toFixed(0)}`);
  ok(Math.abs(l.x - 640) > 40, "and it travels a usable distance, not a token nudge",
     Math.abs(l.x - 640).toFixed(0));

  // THE BOX IS THE DETECTION, NOT THE GAZE, and this is the check that holds the line. A gaze
  // break deliberately looks AWAY from a person the face can still see, so the two values
  // separate -- and the box must stay on the person. Derived from the gaze instead, the box could
  // only ever agree with the eyes, which makes it decoration rather than evidence: it could never
  // show you the eyes going somewhere the detection did not.
  const b = new Face(); b.setState("SAFE_IDLE", 0);
  let parted = false, where = "";
  for (let t = 0; t <= 12000; t += 16) {
    if (t % 160 === 0) b.look({ x: 0.75, y: -0.25, conf: 1, at: t, kind: "face" });
    b.tick(t, 0.016);
    if (t % 160 !== 0) continue;
    const box = b.reticleTarget(t);
    if (!box) continue;
    const gazeX = 640 + b.gazeX.value * (640 - 90);
    if (Math.abs(gazeX - box.x) > 60) {           // the eyes have broken away from the person
      parted = true;
      where = `eyes at ${gazeX.toFixed(0)}, box at ${box.x.toFixed(0)}`;
      ok(Math.abs(box.x - (640 + 0.75 * (640 - 90))) < 1,
         "during a gaze break the box STAYS on the person while the eyes look away", where);
      break;
    }
  }
  ok(parted, "a gaze break happened within 12 s, so that check actually ran", where);

  // Stale: the person has gone, the box goes with them.
  const h = new Face(); h.setState("SAFE_IDLE", 0);
  settle(h, 1000, { x: 0.5, y: 0, conf: 1, kind: "face" });
  ok(h.reticleTarget(1000) !== null, "a fresh target is drawn");
  ok(h.reticleTarget(9000) === null, "a stale one is not: no box left hanging after they leave");

  // THE BOX APPEARS EXACTLY WHEN THE EYES ARE FOLLOWING, which is the property that makes it a
  // diagnostic rather than a second opinion. These were two separate numbers for a while -- 700 in
  // the gaze path, 500 here -- so the box would have vanished while the eyes were still tracking,
  // and anyone watching would have concluded the tracker had dropped them. Swept across the
  // boundary rather than sampled either side of it, so a future divergence cannot hide between
  // two convenient probes.
  for (let age = 0; age <= 1200; age += 50) {
    const m = new Face(); m.setState("SAFE_IDLE", 0);
    settle(m, 1000, { x: 0.6, y: 0.2, conf: 1, kind: "face" });
    const at = 1000 - (1000 % 160);            // when the last report actually landed
    const now = at + age;
    const eyesFollowing = m.target && now - m.target.at < 700;
    const boxShown = m.reticleTarget(now) !== null;
    if (Boolean(eyesFollowing) !== boxShown) {
      ok(false, `the box and the eyes disagree at age ${age} ms`,
         `eyes ${eyesFollowing ? "following" : "free"}, box ${boxShown ? "shown" : "hidden"}`);
      break;
    }
    if (age === 1200) ok(true, "the box is shown exactly while the eyes are following, across the whole boundary");
  }

  // An unknown or missing kind still produces a box, as the drawing does: a diagnostic fails
  // visibly or not at all.
  const k = new Face(); k.setState("SAFE_IDLE", 0);
  settle(k, 1000, { x: 0.3, y: 0.1, conf: 0.9 });
  ok(k.reticleTarget(1000) && k.reticleTarget(1000).kind === "motion",
     "a target with no kind reads as motion rather than vanishing");

  // And the whole way through, it stays somewhere the box can be drawn.
  for (const [x, y] of [[1, 1], [-1, -1], [1, -1], [-1, 1]]) {
    const e = new Face(); e.setState("SAFE_IDLE", 0);
    settle(e, 2000, { x, y, conf: 1, kind: "face" });
    const t = e.reticleTarget(2000);
    ok(t.x > 0 && t.x < 1280 && t.y > 0 && t.y < 720,
       `a target at the extreme (${x},${y}) still yields an on-screen centre`,
       `${t.x.toFixed(0)},${t.y.toFixed(0)}`);
  }
}

console.log(failed ? `\n${failed} FAILED` : "\nall reticle checks passed");
process.exit(failed ? 1 : 0);
