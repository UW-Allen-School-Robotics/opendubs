// THE HOOKS AN OFFLINE RENDER NEEDS, and the promise that the live face is unchanged by them.
//
// Choreography is rendering a show to MP4 with the face in it (Phase 2, layout b). A render is only
// worth having if re-running it after a one-line fix differs by that line and nothing else, which
// asks three things of this engine:
//
//   * NO HIDDEN CLOCK. Every time the engine uses must come from the `now` it is passed, or a frame
//     written in 3 ms behaves like a frame that took 16. Checked here by BOOBY-TRAPPING every clock
//     a browser offers and then driving the whole engine, every code skin included: if anything
//     reaches for performance.now, Date.now, setTimeout, setInterval or requestAnimationFrame, it
//     throws and this test says which. That is worth more than reading the source once, because it
//     keeps being true -- a clock added next month fails here rather than in a video.
//   * ONE SEEDED RANDOM SOURCE. The face is full of chance on purpose; a render seeds it with
//     `setRandom(fn)` and gets the same video twice.
//   * SOMETHING TO AWAIT FOR AN ASSET SKIN, so frame one is not a picture of the default eyes.
//
// And the claim that matters most to the robot: WITH NO HOOKS USED, NOTHING CHANGES. The default
// random path calls through to Math.random rather than capturing it, which is checked here too.
//
// Run: node software/face-web/tests/test_render_hooks.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A controllable Image, so an asset skin's load can be made to succeed, fail, or never answer.
const pending = [];
globalThis.Image = class {
  constructor() { this.naturalWidth = 640; this.naturalHeight = 480; pending.push(this); }
  set src(v) { this._src = v; }
  get src() { return this._src; }
};
globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {},
                                                pause() {}, play: () => Promise.resolve() }) };

const here = dirname(fileURLToPath(import.meta.url));
// Imported BEFORE any clock is trapped: a module that reads `location` at load time (face.js does,
// behind a try/catch) must not be mistaken for one that reads a clock while drawing.
const { Face } = await import(join(here, "../engine/face.js"));
const { setRandom, random } = await import(join(here, "../engine/rng.js"));
const face_mod = await import(join(here, "../engine/face.js"));
const { CODE_SKINS, Skins } = await import(join(here, "../engine/skins/index.js"));
const { makeAssetSkin } = await import(join(here, "../engine/skins/asset.js"));
const { EMOTES } = await import(join(here, "../engine/vocab.js"));

// A context that records what was drawn, so two runs can be compared exactly.
function recorder() {
  const log = [];
  const rec = (n) => (...a) => { log.push(n + "(" + a.map((v) => (typeof v === "object" ? "{}" : String(v))).join(",") + ")"); };
  const grad = { addColorStop: rec("grad.addColorStop") };
  const ctx = {
    log,
    save: rec("save"), restore: rec("restore"), beginPath: rec("beginPath"), closePath: rec("closePath"),
    fill: rec("fill"), stroke: rec("stroke"), clip: rec("clip"), moveTo: rec("moveTo"), lineTo: rec("lineTo"),
    quadraticCurveTo: rec("quadraticCurveTo"), arc: rec("arc"), rect: rec("rect"), roundRect: rec("roundRect"),
    fillRect: rec("fillRect"), drawImage: rec("drawImage"), translate: rec("translate"), scale: rec("scale"),
    rotate: rec("rotate"), ellipse: rec("ellipse"), bezierCurveTo: rec("bezierCurveTo"),
    setTransform: rec("setTransform"), clearRect: rec("clearRect"), fillText: rec("fillText"),
    transform: rec("transform"), measureText: () => ({ width: 10 }),
    createLinearGradient: (...a) => { log.push("lg(" + a.join(",") + ")"); return grad; },
    createRadialGradient: (...a) => { log.push("rg(" + a.join(",") + ")"); return grad; },
  };
  for (const k of ["fillStyle", "strokeStyle", "lineWidth", "lineCap", "globalCompositeOperation", "font", "textAlign"])
    Object.defineProperty(ctx, k, { set(v) { log.push(k + "=" + (typeof v === "object" ? "{}" : v)); } });
  return ctx;
}

// A show, as a render drives it: the time comes from the caller, never from a clock.
function runShow(ms = 4000, step = 1000 / 30) {
  const f = new Face();
  const ctx = recorder();
  f.setState("SAFE_IDLE", 0);
  f.setMouth(true);
  for (let i = 0, now = 0; now < ms; i++, now += step) {
    if (i === 20) f.setEmote("HAPPY", now);
    if (i === 40) f.move("wink_l", now);
    if (i === 60) f.look({ x: 0.5, y: -0.2, conf: 1, at: now });
    if (i === 80) f.setArmed(5, now);
    f.tick(now, step / 1000); f.stateAt = now;
    for (const [name, skin] of Object.entries(CODE_SKINS)) {
      for (const e of f.eyes()) skin.draw(ctx, e, now / 1000);
      void name;
    }
  }
  return ctx.log;
}

// ---- 1. no hidden clock -------------------------------------------------------------------------
// The traps go on AFTER the imports and come off in a finally, because the rest of this file (and
// Node itself, between awaits) legitimately needs timers.
{
  const saved = {
    performance: globalThis.performance, setTimeout: globalThis.setTimeout,
    setInterval: globalThis.setInterval, requestAnimationFrame: globalThis.requestAnimationFrame,
    dateNow: Date.now,
  };
  const reached = [];
  const trap = (what) => (...a) => { reached.push(what); throw new Error(`the engine read ${what}`); };
  let err = null;
  try {
    globalThis.performance = { now: trap("performance.now") };
    globalThis.setTimeout = trap("setTimeout");
    globalThis.setInterval = trap("setInterval");
    globalThis.requestAnimationFrame = trap("requestAnimationFrame");
    Date.now = trap("Date.now");
    runShow(2000);                       // synchronous throughout: it needs no timer of its own
  } catch (e) {
    err = e;
  } finally {
    globalThis.performance = saved.performance; globalThis.setTimeout = saved.setTimeout;
    globalThis.setInterval = saved.setInterval; globalThis.requestAnimationFrame = saved.requestAnimationFrame;
    Date.now = saved.dateNow;
  }
  ok(!err, "the engine and every code skin run with every clock booby-trapped",
     err ? `${reached.join(", ")}: ${err.message}` : "performance.now, Date.now, setTimeout, setInterval, rAF");
}

// ---- 2. a seeded source makes a render repeatable -----------------------------------------------
// mulberry32, which is what the renderer passes: small, fast, and the same sequence everywhere.
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
setRandom(mulberry32(7));
const seeded1 = runShow();
setRandom(mulberry32(7));
const seeded2 = runShow();
setRandom(mulberry32(8));
const seeded3 = runShow();
const prev = setRandom(null);
ok(JSON.stringify(seeded1) === JSON.stringify(seeded2), "the same seed renders the same frames",
   `${seeded1.length} calls`);
ok(JSON.stringify(seeded1) !== JSON.stringify(seeded3), "a different seed renders a different show");
ok(typeof prev === "function", "setRandom returns the previous source, so a caller can put it back");

// Unseeded, the face must still be unpredictable -- the whole reason it has chance in it.
const free1 = runShow(1500), free2 = runShow(1500);
ok(JSON.stringify(free1) !== JSON.stringify(free2), "with no source installed the live face is NOT repeatable");

// ---- 3. the live face is untouched --------------------------------------------------------------
// The default path CALLS THROUGH to Math.random instead of holding a reference, which is what lets
// a test replace Math.random (several do) and what stops this module pinning a stale function.
{
  const realRandom = Math.random;
  let calls = 0;
  try {
    Math.random = () => { calls++; return realRandom(); };
    random(); random(); random();
    ok(calls === 3, "with no source installed, random() goes straight to Math.random", `${calls} calls`);
    // And a seeded source does NOT touch Math.random, or a render would still be at its mercy.
    calls = 0;
    setRandom(mulberry32(1));
    for (let i = 0; i < 50; i++) random();
    setRandom(null);
    ok(calls === 0, "a seeded source never falls back to Math.random", `${calls} calls`);
  } finally {
    Math.random = realRandom;
  }
}

// Every emote still runs, with a seed in place: a hook that broke the vocabulary would be worse
// than no hook.
{
  setRandom(mulberry32(3));
  let threw = null;
  try {
    for (const emote of Object.keys(EMOTES)) {
      const f = new Face(); f.setState("SAFE_IDLE", 0); f.setEmote(emote, 0);
      const ctx = recorder();
      for (let now = 0; now < 1200; now += 16) { f.tick(now, 0.016); f.stateAt = now; }
      for (const e of f.eyes()) CODE_SKINS.default.draw(ctx, e, 1.2);
    }
  } catch (e) { threw = e; }
  setRandom(null);
  ok(!threw, "every emote still runs under a seeded source", threw && threw.message);
}

// ---- 4. something to await for an asset skin ----------------------------------------------------
const skins = new Skins();
ok(await skins.skinsReady() === true, "skinsReady() is true at once for a code skin (nothing to load)");
ok(typeof skins.ready === "function" && await skins.ready() === true,
   "ready() is the same thing under the name the page calls");

// An image skin that decodes.
function installAsset(entry) {
  skins.library.set(entry.id, entry);
  skins.baseUrl = "http://localhost/library/";
  return skins.select(entry.id);
}
pending.length = 0;
installAsset({ kind: "skin", id: "a-still", type: "image", file: "still.png", params: {} });
const waitingOk = skins.skinsReady();
ok(pending.length === 1, "selecting an image skin started one load", pending.length);
pending[0].onload();
ok(await waitingOk === true, "skinsReady() resolves true once the image has decoded");
ok(await skins.skinsReady() === true, "and true again afterwards, without a second load");

// One that cannot be decoded: false, not a hang, and the existing fallback still reports it.
pending.length = 0;
installAsset({ kind: "skin", id: "a-broken", type: "image", file: "broken.png", params: {} });
// The cap is generous on purpose: the false must come from the ERROR, not from waiting it out. A
// promise that is never settled on failure would still read as false here, five seconds late, and a
// render would pay that for every broken skin. So the time is asserted too.
const badFrom = Date.now();
const waitingBad = skins.skinsReady(5000);
pending[0].onerror();
const bad = await waitingBad, badMs = Date.now() - badFrom;
ok(bad === false && badMs < 500, "skinsReady() resolves FALSE for an asset that cannot be decoded, at once",
   `${bad} after ${badMs} ms`);
ok(skins.error && skins.error.includes("a-broken"), "and the skin still falls back and says why", skins.error);

// One that never answers at all: capped, so a render reports a problem instead of producing nothing.
pending.length = 0;
installAsset({ kind: "skin", id: "a-silent", type: "image", file: "silent.png", params: {} });
const t0 = Date.now();
const silent = await skins.skinsReady(60);
ok(silent === false, "an asset that never answers resolves FALSE at the cap, rather than hanging",
   `${Date.now() - t0} ms`);

// The alias and the method are one implementation, not two that can drift.
ok(Skins.prototype.ready !== Skins.prototype.skinsReady
   && String(Skins.prototype.ready).includes("skinsReady"), "ready() delegates to skinsReady()");

void makeAssetSkin; void face_mod;
console.log(failed ? `\n${failed} FAILED` : "\nall render-hook checks passed");
process.exit(failed ? 1 : 0);
