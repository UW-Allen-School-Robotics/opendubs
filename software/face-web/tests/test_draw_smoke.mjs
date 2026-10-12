// ACTUALLY CALL EVERY SKIN'S draw(), for every state the face can hand it.
//
// This exists because the suite had 100 passing checks and still shipped a face that threw on its
// first frame: #204 rebuilt eyes.js and dropped five constants it still used, and nothing in CI
// ever called a code skin's draw(). `node --check` cannot catch it either -- an undefined name is
// valid syntax, and only becomes a ReferenceError when the line runs. The model was tested to
// death and the drawing was not tested at all.
//
// So this is deliberately dumb: no geometry is judged here, nothing is measured. It runs the real
// drawing code over every emote, every lid position, both eyes and a spread of gazes, and fails if
// anything throws. Every other test in this directory checks what the drawing MEANS; this one
// checks that it runs.
//
// Run: node software/face-web/tests/test_draw_smoke.mjs   (conformance.sh runs it first)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Enough of a canvas for a skin to draw into, and a Proxy so a 2D call no skin makes today does not
// fail tomorrow for the wrong reason. Unknown members are recorded and reported, not silently
// swallowed, so a REAL typo in a context call still shows up as something to look at.
const unknown = new Set();
function stubCtx() {
  const base = {
    save() {}, restore() {}, beginPath() {}, closePath() {}, fill() {}, stroke() {}, clip() {},
    moveTo() {}, lineTo() {}, quadraticCurveTo() {}, bezierCurveTo() {}, arc() {}, ellipse() {},
    rect() {}, roundRect() {}, fillRect() {}, clearRect() {}, drawImage() {}, translate() {},
    scale() {}, rotate() {}, setTransform() {}, transform() {}, measureText: () => ({ width: 10 }),
    fillText() {}, strokeText() {}, createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }), getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    canvas: { width: 1280, height: 720 },
  };
  return new Proxy(base, {
    get(t, k) {
      if (k in t) return t[k];
      if (typeof k === "symbol") return undefined;
      unknown.add(String(k));
      return () => {};                       // a method we have not stubbed: let it run, and report
    },
    set() { return true; },                  // fillStyle, lineWidth, globalCompositeOperation, ...
  });
}

globalThis.Image = class { constructor() { this.naturalWidth = 640; this.naturalHeight = 480; }
                           set src(_) { this.onload && this.onload(); } };
globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {},
                                                pause() {}, play: () => Promise.resolve() }) };

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { CODE_SKINS } = await import(join(here, "../engine/skins/index.js"));
const { makeAssetSkin } = await import(join(here, "../engine/skins/asset.js"));
const { EMOTES } = await import(join(here, "../engine/vocab.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A face driven through everything it can be in, yielding the per-eye inputs a skin is handed.
function* everyState() {
  for (const emote of Object.keys(EMOTES)) {
    const f = new Face();
    f.setState("SAFE_IDLE", 0);
    f.setEmote(emote, 0);
    for (let t = 0; t < 6000; t += 16) {
      f.tick(t, 0.016); f.stateAt = t;
      // a person to look at for part of it, so the gaze break and the tracking path both run
      if (t > 2000 && t < 4000) f.look({ x: 0.6, y: -0.3, conf: 1, at: t });
      if (t % 96 === 0) for (const e of f.eyes()) yield { e, t, emote };
    }
  }
}

// ---- 1. every code skin, every state ------------------------------------------------------------
// Iterated from CODE_SKINS, so a skin added later is covered without anyone remembering to add it.
for (const [name, skin] of Object.entries(CODE_SKINS)) {
  let frames = 0, err = null;
  try {
    for (const { e, t } of everyState()) { skin.draw(stubCtx(), e, t / 1000); frames++; }
  } catch (ex) { err = ex; }
  ok(!err, `code skin '${name}' draws every state without throwing`,
     err ? `${err.constructor.name}: ${err.message}` : `${frames} draws`);
}

// ---- 2. asset skins, including the full-face layout ----------------------------------------------
for (const [what, params] of [
  ["image, in the eyes", { spin_dps: 90 }],
  ["image, counter-spinning", { spin_dps: 90, counter: { every_s: [0.1, 0.1], hold_s: [0.2, 0.2], ease_s: 0.3 } }],
  ["image, full face", { layout: "full" }],
]) {
  const sk = makeAssetSkin({ id: "x", type: "image", file: "x.png", params }, "http://x/", () => {});
  let frames = 0, err = null;
  try {
    for (const { e, t } of everyState()) { sk.draw(stubCtx(), e, t / 1000); frames++; }
  } catch (ex) { err = ex; }
  ok(!err, `asset skin (${what}) draws every state without throwing`,
     err ? `${err.constructor.name}: ${err.message}` : `${frames} draws`);
}

// ---- 3. the states a skin sees only rarely -------------------------------------------------------
// A lid fully shut, a lid fully open and the gaze hard into each corner: the branches that only run
// at the extremes, which an averaged run of frames can miss entirely.
{
  const f = new Face(); f.setState("SAFE_IDLE", 0);
  for (let t = 0; t < 2000; t += 16) { f.tick(t, 0.016); f.stateAt = t; }
  const [base] = f.eyes();
  let err = null, cases = 0;
  try {
    for (const lid of [0, 0.5, 1]) for (const gx of [-1, 0, 1]) for (const gy of [-1, 0, 1])
      for (const [name, skin] of Object.entries(CODE_SKINS)) {
        skin.draw(stubCtx(), { ...base, lid, gazeX: gx, gazeY: gy }, 1.5); cases++;
      }
  } catch (ex) { err = ex; }
  ok(!err, "every code skin survives a shut lid and the gaze in each corner",
     err ? `${err.constructor.name}: ${err.message}` : `${cases} cases`);
}

if (unknown.size) console.log(`  note  2D calls not in the stub, worth a look if unexpected: ${[...unknown].join(", ")}`);
console.log(failed ? `\n${failed} FAILED` : "\nall draw smoke checks passed");
process.exit(failed ? 1 : 0);
