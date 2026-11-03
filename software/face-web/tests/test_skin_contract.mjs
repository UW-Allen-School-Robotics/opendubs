// THE SKIN CONTRACT, held against the engine — what every code skin is handed, and what a
// well-made one does with it.
//
// This replaces test_monsters.mjs, which held the same checks against the seven monster skins
// until they moved to the private content folder. The checks were never really about monsters:
// they were about the contract in skin-api.js, and that contract now matters MORE, because the
// skins that depend on it live outside this repository. Nothing in CI can see them. If the engine
// stopped supplying `lidCurve`, or renamed a field, or moved the brow's pivot, every content skin
// on the robot would break and no test here would notice.
//
// So the two halves below do different work:
//
//   Part 1 reads the ENGINE. The eye inputs come from a real Face, so a channel the engine stops
//   producing disappears from them, and these assertions fail. This is the half with teeth.
//
//   Part 2 reads a FIXTURE skin, written here to follow skin-api.js's rules exactly. It cannot
//   catch an engine change by itself — it is code that passes because it was written to. What it
//   does do is keep the contract's shape executable: the fixture is the worked example of
//   "clip, draw inside, restore", and the recorder proves those properties are READABLE from a
//   drawing, which is what lets a content author check their own skin the same way.
//
// Run: node software/face-web/tests/test_skin_contract.mjs   (conformance.sh runs it)

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
const LIVE = (() => {
  const f = new Face();
  f.setState("SAFE_IDLE", 0);
  f.setEmote("NEUTRAL", 0);
  for (let t = 0; t < 1200; t += 16) f.tick(t, 0.016);
  return f.eyes();
})();
const BASE = Object.freeze({ ...LIVE[0], gazeX: 0, gazeY: 0, lid: 0, lidLower: 0, lidCurve: 0,
                             browRaise: 0, browAngle: 0, mouth: null });

function eyeInput(over = {}) { return { ...BASE, ...over }; }

// ---- PART 1: what the ENGINE hands a skin -------------------------------------------------------
// Every channel skin-api.js documents, present on a real eye and of the right kind. A content skin
// reads these by name; a rename or a removal here is invisible until the robot's face breaks.
const NUMBERS = ["cx", "cy", "w", "h", "radius", "lid", "lidLower", "lidCurve",
                 "browRaise", "browAngle", "gazeX", "gazeY", "sparkle"];
for (const k of NUMBERS) {
  const v = LIVE[0][k];
  ok(typeof v === "number" && Number.isFinite(v), `the engine supplies a finite '${k}'`, String(v));
}
ok(LIVE.length === 2, "the engine draws two eyes", String(LIVE.length));
ok(LIVE[0].side === "left" && LIVE[1].side === "right", "the eyes report their own side",
   `${LIVE[0].side}/${LIVE[1].side}`);
ok(LIVE[0].w > 1 && LIVE[0].h > 1, "the eye has a visible size", `${LIVE[0].w.toFixed(1)}x${LIVE[0].h.toFixed(1)}`);
// `colour` and `dim` are CSS strings a skin assigns straight to fillStyle; `rgb` is the triple.
ok(typeof LIVE[0].colour === "string" && LIVE[0].colour.length > 0,
   "the engine supplies the state 'colour' as a CSS string", String(LIVE[0].colour));
ok(typeof LIVE[0].dim === "string" && LIVE[0].dim.length > 0,
   "the engine supplies 'dim', the 35% colour", String(LIVE[0].dim));
ok(Array.isArray(LIVE[0].rgb) && LIVE[0].rgb.length === 3, "the engine supplies the raw 'rgb' triple",
   JSON.stringify(LIVE[0].rgb));
ok(LIVE[0].mouth === null || (LIVE[0].mouth && typeof LIVE[0].mouth.level === "number"),
   "'mouth' is null, or carries a level", JSON.stringify(LIVE[0].mouth));

// The channels a skin reads must be able to MOVE, or a skin that honours them has nothing to show.
// Read off the engine, not off our own overrides: the lid is driven to shut and back.
const lidRange = (() => {
  const f = new Face();
  f.setState("SAFE_IDLE", 0);
  let lo = Infinity, hi = -Infinity;
  for (let t = 0; t < 20000; t += 16) { f.tick(t, 0.016); const [e] = f.eyes(); lo = Math.min(lo, e.lid); hi = Math.max(hi, e.lid); }
  return { lo, hi };
})();
ok(lidRange.hi - lidRange.lo > 0.5, "the engine blinks: 'lid' sweeps open to shut on its own",
   `${lidRange.lo.toFixed(2)}..${lidRange.hi.toFixed(2)}`);

// ---- the step-6 guard --------------------------------------------------------------------------
// The seven monsters are content now. A content skin MAY NOT TAKE A BUILT-IN'S NAME (skins/index.js
// refuses one that does), so re-adding any of these to the core would silently shadow the content
// copy and the robot would draw the core's version of a skin the content author has since changed.
const MOVED = ["monster_stitched", "monster_vampire", "monster_wolf", "monster_mummy",
               "monster_zombie", "monster_ghoul", "monster_lab"];
for (const n of MOVED) ok(!(n in CODE_SKINS), `'${n}' is not a built-in: it comes from the content folder`);
ok(Object.keys(CODE_SKINS).join(",") === "default,ring,fireworks",
   "the built-ins are default, ring and fireworks", Object.keys(CODE_SKINS).join(","));

// ---- PART 2: the contract, made executable -----------------------------------------------------
// A fixture skin written the way skin-api.js says to write one: mouth first on the left pass, clip
// to the eye body before drawing inside it, restore after, brow last and outside the clip, and a
// pure function of `t`.
const fixture = {
  name: "contract_fixture",
  draw(ctx, e, t) {
    const half = e.w / 2;
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(e.cx - half, e.cy - e.h / 2, e.w, e.h, e.radius);
    ctx.fillStyle = e.dim;
    ctx.fill();
    ctx.clip();                                        // rule 2: inside the body from here
    // The pupil, following the gaze on both axes.
    const px = e.cx + e.gazeX * half * 0.5, py = e.cy + e.gazeY * (e.h / 2) * 0.5;
    ctx.beginPath();
    ctx.arc(px, py, e.h * 0.18, 0, Math.PI * 2);
    ctx.fillStyle = e.colour;
    ctx.fill();
    // A shape that moves with t alone, so purity is checkable.
    ctx.beginPath();
    ctx.arc(e.cx + Math.sin(t) * 4, e.cy - e.h * 0.25, 3, 0, Math.PI * 2);
    ctx.fill();
    // The lids, bowing with lidCurve, in black as the engine's own eyes draw them.
    ctx.fillStyle = "#000";
    if (e.lid > 0) {
      const y = e.cy - e.h / 2 + e.h * e.lid;
      ctx.beginPath();
      ctx.moveTo(e.cx - half, e.cy - e.h / 2);
      ctx.lineTo(e.cx + half, e.cy - e.h / 2);
      ctx.lineTo(e.cx + half, y);
      ctx.quadraticCurveTo(e.cx, y + e.lidCurve * e.h * 0.15, e.cx - half, y);
      ctx.fill();
    }
    if (e.lidLower > 0) {
      const y = e.cy + e.h / 2 - e.h * e.lidLower;
      ctx.beginPath();
      ctx.moveTo(e.cx - half, e.cy + e.h / 2);
      ctx.lineTo(e.cx + half, e.cy + e.h / 2);
      ctx.lineTo(e.cx + half, y);
      ctx.quadraticCurveTo(e.cx, y - e.lidCurve * e.h * 0.15, e.cx - half, y);
      ctx.fill();
    }
    ctx.restore();                                     // rule 2: and out again
    // The brow, last and OUTSIDE the clip, pivoting about its inner end as eyes.js does.
    const inner = e.side === "left" ? half : -half;
    const lift = e.browRaise * e.h * 0.2;
    const tip = Math.tan((e.browAngle * Math.PI) / 180) * e.w;
    ctx.beginPath();
    ctx.moveTo(e.cx + inner, e.cy - e.h * 0.7 - lift);
    ctx.lineTo(e.cx - inner, e.cy - e.h * 0.7 - lift + tip);
    ctx.strokeStyle = e.colour;
    ctx.lineWidth = 8;
    ctx.stroke();
    return true;
  },
};

function render(skin, over = {}, t = 2) {
  const ctx = recorder();
  skin.draw(ctx, eyeInput(over), t);
  return ctx.log;
}
function circles(log) {
  const out = [];
  for (const line of log) {
    const m = /^(arc|ellipse)\(([-\d.e]+),([-\d.e]+),/.exec(line);
    if (m) out.push({ x: +m[2], y: +m[3] });
  }
  return out;
}
const lidEdges = (log) => log.filter((l) => l.startsWith("quadraticCurveTo("));

// The clip is what keeps a skin's interior inside its own eye; the matching restore is what keeps
// it off the eye beside it. Checked under a shut lid too, where an early return would be tempting.
for (const [what, over] of [["open", {}], ["shut", { lid: 1 }], ["gazing", { gazeX: 1, gazeY: -1 }]]) {
  const log = render(fixture, over);
  const saves = log.filter((l) => l === "save()").length;
  const restores = log.filter((l) => l === "restore()").length;
  ok(saves === restores && saves > 0, `the fixture balances save/restore (${what})`, `${saves}/${restores}`);
  const clipAt = log.indexOf("clip()");
  const firstCircle = log.findIndex((l) => /^(arc|ellipse)\(/.test(l));
  ok(clipAt >= 0 && clipAt < firstCircle, `the fixture clips before drawing inside the eye (${what})`,
     `clip@${clipAt} first circle@${firstCircle}`);
}

// Readable from the drawing: the gaze moves the pupil, on both axes and the right way.
{
  const l = circles(render(fixture, { gazeX: -1 })), r = circles(render(fixture, { gazeX: 1 }));
  const u = circles(render(fixture, { gazeY: -1 })), d = circles(render(fixture, { gazeY: 1 }));
  const dx = Math.max(...r.map((c, i) => c.x - l[i].x));
  const dy = Math.max(...d.map((c, i) => c.y - u[i].y));
  ok(dx > 20, "the gaze moves the pupil left and right", `${dx.toFixed(1)} px`);
  ok(dy > 10, "the gaze moves the pupil up and down", `${dy.toFixed(1)} px`);
}

// The lids, and that lidCurve actually bows the edge.
ok(lidEdges(render(fixture, { lid: 0 })).length === 0, "no lid is drawn when the eye is open");
ok(lidEdges(render(fixture, { lid: 1 })).length === 1, "the upper lid is drawn when the eye is shut");
ok(render(fixture, { lid: 1 }).includes("fillStyle=#000"), "the lid is black");
ok(lidEdges(render(fixture, { lid: 0.3, lidLower: 0.3 })).length === 2, "a lower lid is drawn too");
{
  const flat = lidEdges(render(fixture, { lid: 0.5, lidCurve: 0 }))[0];
  const bowed = lidEdges(render(fixture, { lid: 0.5, lidCurve: 1 }))[0];
  ok(flat !== bowed, "lidCurve bows the lid edge", flat);
}

// The brow carries the emote, read at the OUTER end -- the end that moves, since the pivot is the
// inner one. An inner-end check would pass for a skin that ignored the angle entirely.
function browOuterY(log) {
  const i = log.map((l) => l.startsWith("lineTo(")).lastIndexOf(true);
  return i < 0 ? NaN : +/^lineTo\(([-\d.e]+),([-\d.e]+)\)/.exec(log[i])[2];
}
{
  const down = browOuterY(render(fixture, { browRaise: 0 }));
  const up = browOuterY(render(fixture, { browRaise: 1 }));
  ok(Number.isFinite(down) && up < down - 10, "browRaise lifts the brow", `${down.toFixed(1)} -> ${up.toFixed(1)}`);
  const level = browOuterY(render(fixture, { browAngle: 0 }));
  const tipped = browOuterY(render(fixture, { browAngle: 20 }));
  ok(Math.abs(level - tipped) > 10, "browAngle tips the brow", `${level.toFixed(1)} -> ${tipped.toFixed(1)}`);
}

// Nothing accumulates: a skipped frame or a hidden tab must not change what is on screen, and
// re-selecting a skin must not reset anything, because there is nothing to reset.
ok(JSON.stringify(render(fixture, {}, 3.5)) === JSON.stringify(render(fixture, {}, 3.5)),
   "the same inputs draw the same thing twice");
ok(JSON.stringify(render(fixture, {}, 0.0)) !== JSON.stringify(render(fixture, {}, 1.3)),
   "t alone changes the drawing");
ok(JSON.stringify(render(fixture, {}, 0.0)) === JSON.stringify(render(fixture, {}, 0.0)),
   "the same t is the same frame, however it was reached");

console.log(failed ? `\n${failed} FAILED` : "\nall skin-contract checks passed");
process.exit(failed ? 1 : 0);
