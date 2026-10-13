// The optional mouth: that it draws when on, animates from one number, closes by itself when the
// feed stops, and -- the claim that matters most -- that turning it OFF changes NOTHING.
//
// Run: node software/face-web/tests/test_mouth.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { CODE_SKINS } = await import(join(here, "../engine/skins/index.js"));
const { EMOTES } = await import(join(here, "../engine/vocab.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A context that RECORDS every call and argument, so two runs can be compared exactly.
function recorder() {
  const log = [];
  const rec = (name) => (...a) => { log.push(name + "(" + a.map(v => typeof v === "object" ? "{}" : String(v)).join(",") + ")"); };
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

// Drive a face and record everything every code skin draws.
//
// SEEDED, because comparing two runs is the whole method here and the face is full of randomness --
// saccades, blink intervals, emote cycling, the gaze break, and the fireworks skin's own jitter.
// An earlier version pinned the timers after each tick and still failed: the first tick had already
// rolled a saccade, so the two runs diverged on frame one and the comparison was never comparing
// like with like. Replacing Math.random outright is the only version of this that means anything.
function trace(setup) {
  const real = Math.random;
  let seed = 12345;
  Math.random = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  try {
    const out = {};
    for (const [name, skin] of Object.entries(CODE_SKINS)) {
      seed = 12345;                                  // every skin starts from the same roll
      const f = new Face();
      f.setState("SAFE_IDLE", 0);
      f.setEmote("NEUTRAL", 0);
      setup(f);
      const ctx = recorder();
      for (let t = 0; t < 3000; t += 16) {
        f.tick(t, 0.016); f.stateAt = t;
        for (const e of f.eyes()) skin.draw(ctx, e, t / 1000);
      }
      out[name] = ctx.log;
    }
    return out;
  } finally { Math.random = real; }
}

// ---- 1. OFF IS IDENTICAL ------------------------------------------------------------------------
// The brief asked for byte-identical. Proven by comparing the whole recorded call stream of a face
// with the mouth never mentioned against one explicitly turned off -- same calls, same arguments,
// same order. If the mouth leaked a single operation into the default face, these would differ.
{
  const never = trace(() => {});
  const off = trace((f) => f.setMouth(false));
  for (const name of Object.keys(never)) {
    const a = never[name].join("\n"), b = off[name].join("\n");
    ok(a === b, `'${name}': mouth off draws exactly what a face with no mouth draws`,
       `${never[name].length} calls`);
  }
}

// ---- 2. ON ADDS THE MOUTH, AND ONLY THE MOUTH ---------------------------------------------------
{
  const off = trace(() => {});
  const on = trace((f) => { f.setMouth(true); });
  for (const name of Object.keys(off)) {
    ok(on[name].length > off[name].length, `'${name}': the mouth adds draw calls when on`,
       `${off[name].length} -> ${on[name].length}`);
    // and the eye drawing is untouched: every call the eyeless-mouth run made is still there, in order
    const a = off[name], b = on[name];
    let i = 0;
    for (const call of b) if (i < a.length && call === a[i]) i++;
    ok(i === a.length, `  and every call the mouthless face made is still made, in order`,
       `matched ${i}/${a.length}`);
  }
}

// ---- 3. it animates from one number, and closes by itself ---------------------------------------
{
  const f = new Face(); f.setState("SAFE_IDLE", 0); f.setMouth(true);
  for (let t = 0; t < 500; t += 16) f.tick(t, 0.016);
  ok(f.mouthLevel === 0, "a mouth with no audio is shut", `${f.mouthLevel}`);

  let peak = 0;
  for (let t = 500; t < 1500; t += 16) { f.setMouthLevel(0.9); f.tick(t, 0.016); peak = Math.max(peak, f.mouthLevel); }
  ok(peak > 0.7, "it opens when fed an envelope", `peak ${peak.toFixed(3)}`);

  // THE FEED STOPS -- a dead audio path must look like silence, not a held vowel.
  let t = 1500;
  for (; t < 3500; t += 16) f.tick(t, 0.016);
  ok(f.mouthLevel === 0, "and closes by itself when the feed stops", `${f.mouthLevel}`);
}

// ---- 4. the manual talk cue ---------------------------------------------------------------------
{
  const f = new Face(); f.setState("SAFE_IDLE", 0); f.setMouth(true);
  for (let t = 0; t < 300; t += 16) f.tick(t, 0.016);
  f.talk(1000, 300);
  let moved = new Set(), peak = 0;
  for (let t = 300; t < 1300; t += 16) { f.tick(t, 0.016); moved.add(f.mouthLevel.toFixed(2)); peak = Math.max(peak, f.mouthLevel); }
  ok(peak > 0.4, "talk() opens the mouth with no audio behind it", `peak ${peak.toFixed(3)}`);
  ok(moved.size > 8, "  and it moves rather than holding one shape", `${moved.size} distinct levels`);
  for (let t = 1300; t < 3300; t += 16) f.tick(t, 0.016);
  ok(f.mouthLevel === 0, "  and it ends", `${f.mouthLevel}`);
}

// ---- 5. on, it survives every emote and a shut lid ------------------------------------------------
// ring and fireworks return early when the lid is shut; the mouth is drawn before that, or it would
// disappear on every blink.
{
  const f = new Face(); f.setState("SAFE_IDLE", 0); f.setMouth(true);
  for (let t = 0; t < 1000; t += 16) f.tick(t, 0.016);
  f.setMouthLevel(0.8); f.tick(1000, 0.016);
  for (const [name, skin] of Object.entries(CODE_SKINS)) {
    for (const emote of Object.keys(EMOTES)) {
      f.setEmote(emote, 1000); f.tick(1016, 0.016);
      const ctx = recorder();
      const [l] = f.eyes();
      skin.draw(ctx, { ...l, lid: 1 }, 1.5);            // lid fully shut: the early-return path
      ok(ctx.log.some((c) => c.startsWith("roundRect") || c.startsWith("rect")),
         `'${name}' still draws the mouth with the lid shut (${emote})`);
    }
  }
}

console.log(failed ? `\n${failed} FAILED` : "\nall mouth checks passed");
process.exit(failed ? 1 : 0);
