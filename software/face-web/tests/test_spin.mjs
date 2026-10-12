// The counter-spin geometry in engine/skins/asset.js, headless — the part the head protocol's
// conformance suite cannot see. It checks message handling; this checks what the frames DO, which
// is where every promise this feature made to another lane actually lives:
//
//   * the zero crossing is ease_s/2 after receipt   -- head-protocol.md records it, and the show
//                                                      runner sends the SPIN line early by it
//   * BOTH eyes cross together                      -- so "the cue lands on the beat" is true of
//                                                      the pair, not just of one eye
//   * nothing ever jumps                            -- the whole reason the integrator exists
//   * an entry with no counter and no SPIN is       -- bit-for-bit the original expression
//
// Run: node software/face-web/tests/test_spin.mjs   (conformance.sh runs it first)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// The engine is browser code. Three stubs are all it needs to run a frame with nothing drawn.
globalThis.Image = class { set src(_) { this.onload && this.onload(); } };
globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { makeAssetSkin, ASSET_PARAM_DEFAULTS, ASSET_COUNTER_DEFAULTS } =
  await import(join(here, "../engine/skins/asset.js"));

const DPS = 90, FPS = 60, DT = 1 / FPS;
const FRAME_DEG = DPS * DT;                 // 1.5 deg: what a steady frame advances by
const EPS = 1e-9;
const deg = (r) => (r * 180) / Math.PI;

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A skin plus a draw() that returns the angle each eye was rotated to this frame.
function rig(params) {
  const sk = makeAssetSkin({ id: "t", type: "image", file: "x.png", params }, "http://x/", () => {});
  const rot = {};
  const ctx = { save() {}, restore() {}, translate() {}, scale() {}, drawImage() {},
                rotate(a) { rot[ctx._side] = a; } };
  const draw = (t) => {
    for (const side of ["left", "right"]) {
      ctx._side = side;
      sk.draw(ctx, { cx: 0, cy: 0, lid: 0, gazeX: 0, gazeY: 0, side }, t);
    }
    return { left: rot.left, right: rot.right };
  };
  return { sk, draw };
}
const step = (a, b) => Math.max(Math.abs(deg(b.left - a.left)), Math.abs(deg(b.right - a.right)));
const rate = (a, b) => ({ l: deg(b.left - a.left) * FPS, r: deg(b.right - a.right) * FPS });

// ---- 1. a SPINNING skin with NO `counter` block still honours SPIN -----------------------------
// Agreed with Choreography: a cue must not silently depend on whether an operator configured the
// autonomous feature. They are different features that merely share a mechanism.
{
  const EASE = 0.9;                                    // the default, since there is no block
  const { sk, draw } = rig({ spin_dps: DPS });
  let t = 0, prev = draw(t);
  for (let i = 0; i < 120; i++) { t += DT; prev = draw(t); }   // free-run, so the latch has a real angle

  sk.spin("counter", 3);
  t += DT; const first = draw(t);
  ok(step(prev, first) <= FRAME_DEG + EPS, "no jump when a show first takes the skin",
     `${step(prev, first).toFixed(3)} deg, a steady frame is ${FRAME_DEG.toFixed(3)}`);

  const t0 = t;
  let zeroL = null, zeroR = null, worstAsym = 0, p = first;
  for (let i = 0; i < 90; i++) {
    t += DT; const f = draw(t); const r = rate(p, f);
    worstAsym = Math.max(worstAsym, Math.abs(Math.abs(r.l) - Math.abs(r.r)));
    if (zeroL === null && r.l <= 0.5) zeroL = t - t0;
    if (zeroR === null && r.r <= 0.5) zeroR = t - t0;
    p = f;
  }
  const want = EASE / 2;
  ok(zeroL !== null && Math.abs(zeroL - want) <= DT, "the zero crossing is ease_s/2 after receipt",
     `${zeroL?.toFixed(3)}s vs ${want.toFixed(3)}s`);
  ok(zeroL !== null && zeroL === zeroR, "both eyes cross zero on the same frame",
     `L ${zeroL?.toFixed(3)}s  R ${zeroR?.toFixed(3)}s`);
  ok(worstAsym < 1e-6, "the two eyes keep equal and opposite rates throughout the ease",
     `worst difference ${worstAsym.toExponential(1)} deg/s`);
}

// ---- 2. SPIN AUTO hands the schedule back without a jump ---------------------------------------
{
  const { sk, draw } = rig({ spin_dps: DPS });
  let t = 0; draw(t);
  for (let i = 0; i < 60; i++) { t += DT; draw(t); }
  sk.spin("counter", null);
  for (let i = 0; i < 120; i++) { t += DT; draw(t); }          // settle fully into counter
  sk.spin("auto");
  let prev = draw(t), worst = 0;
  for (let i = 0; i < 180; i++) { t += DT; const f = draw(t); worst = Math.max(worst, step(prev, f)); prev = f; }
  ok(worst <= FRAME_DEG + EPS, "no jump when SPIN AUTO hands the skin back",
     `largest frame ${worst.toFixed(3)} deg`);
}

// ---- 3. a show owns the spin until it says AUTO -------------------------------------------------
// A hold that expires returns to together but STAYS held, or the autonomous schedule fires in the
// middle of a show and the face breaks apart on nobody's beat.
{
  const cfg = { every_s: [0.1, 0.1], hold_s: [0.3, 0.3], ease_s: 0.2 };   // a schedule desperate to fire
  const { sk, draw } = rig({ spin_dps: DPS, counter: cfg });
  let t = 0; draw(t);
  sk.spin("counter", 0.2);                              // a short hold, then back to together
  for (let i = 0; i < 60; i++) { t += DT; draw(t); }     // 1 s: the hold has long expired

  let p = draw(t), apart = false;
  for (let i = 0; i < 300; i++) {                        // 5 s with the schedule free to misbehave
    t += DT; const f = draw(t); const r = rate(p, f);
    if (Math.abs(r.l - r.r) > 1) apart = true;
    p = f;
  }
  ok(!apart, "the autonomous schedule stays suppressed while a show holds the spin");

  sk.spin("auto");
  let q = draw(t), returned = false;
  for (let i = 0; i < 300; i++) {
    t += DT; const f = draw(t); const r = rate(q, f);
    if (Math.abs(r.l - r.r) > 1) returned = true;
    q = f;
  }
  ok(returned, "and fires again once the show sends AUTO");
}

// ---- 4. a NON-spinning skin: SPIN is a no-op ----------------------------------------------------
// protocol.js answers OK either way; a skin that does not spin simply has nothing to do.
{
  const { sk, draw } = rig({ spin_dps: 0 });
  let t = 0; draw(t);
  sk.spin("counter", 2);
  for (let i = 0; i < 60; i++) { t += DT; draw(t); }
  const f = draw(t);
  ok(f.left === 0 && f.right === 0, "SPIN on a non-spinning skin changes nothing",
     `L ${deg(f.left)} R ${deg(f.right)}`);
}

// ---- 5. an ordinary entry is bit-for-bit what it was --------------------------------------------
// No counter block and no SPIN must take the original absolute-time expression, untouched --
// EXACTLY, not nearly: this is the whole claim that the integrator costs existing skins nothing.
for (const mirror of [false, true]) {
  const { draw } = rig({ spin_dps: DPS, mirror });
  let worst = 0;
  for (let i = 0; i < 600; i++) {
    const t = i * DT, f = draw(t), want = (DPS * Math.PI) / 180 * t;
    worst = Math.max(worst, Math.abs(f.left - want), Math.abs(f.right - (mirror ? -want : want)));
  }
  ok(worst === 0, `an ordinary entry is exactly rate*t (mirror ${mirror})`, `deviation ${worst}`);
}

// ---- 5b. the mode is normalised, and anything left over does NOTHING -------------------------
// A mode the skin did not recognise used to fall through to the `together` branch, so a caller
// passing "COUNTER" got a spin that drew TOGETHER: silently the wrong gesture, on the beat,
// reported nowhere. Now case is normalised -- so "COUNTER" is simply accepted -- and what is still
// unrecognised leaves the spin exactly as it was, which is the better failure.
//
// Did the eyes separate, over `frames` frames?
const separates = (draw, from, frames) => {
  let t = from, p = draw(t), apart = false;
  for (let i = 0; i < frames; i++) { t += DT; const f = draw(t); const r = rate(p, f);
    if (Math.abs(r.l - r.r) > 1) apart = true; p = f; }
  return apart;
};
{
  const { sk, draw } = rig({ spin_dps: DPS });
  let t = 0; draw(t);
  for (let i = 0; i < 60; i++) { t += DT; draw(t); }
  for (const bad of ["spin", "", "counter ", null, undefined, 7, {}]) sk.spin(bad, 2);
  ok(!separates(draw, t, 120), "an unrecognised mode leaves the spin exactly as it was");

  sk.spin("COUNTER", 2);                       // the same word, differently cased, IS the mode
  ok(separates(draw, t, 120), "a differently-cased mode is accepted, not silently misread");
}

// ---- 5c. ease_s: 0 is clamped, not snapped ----------------------------------------------------
// library.py accepts ease_s: 0 -- it refuses only negatives and non-numbers -- so a VALID entry can
// ask for an instant reversal, which protocol v3 says the wire may not have. The skin clamps to
// EASE_MIN = 0.05, so the crossing is 0.025 s rather than immediate. Choreography computes its
// lead as max(ease_s, 0.05)/2 to match; this pins the number so the two cannot drift apart.
{
  const { sk, draw } = rig({ spin_dps: DPS, counter: { every_s: [99, 99], hold_s: [9, 9], ease_s: 0 } });
  let t = 0; draw(t);
  sk.spin("counter", 5);
  const t0 = t; let zero = null, p = draw(t), maxStep = 0;
  for (let i = 0; i < 30; i++) {
    t += DT; const f = draw(t);
    maxStep = Math.max(maxStep, step(p, f));
    if (zero === null && rate(p, f).r <= 0.5) zero = t - t0;
    p = f;
  }
  ok(zero !== null && zero <= 3 * DT, "ease_s 0 is clamped, so it still eases through zero",
     `crossed at ${zero?.toFixed(3)}s, EASE_MIN/2 = 0.025s`);
  ok(maxStep <= FRAME_DEG + EPS, "and even then nothing snaps", `largest frame ${maxStep.toFixed(3)} deg`);
}

// ---- 6. THE WIRE REACHES THE SKIN ---------------------------------------------------------------
// The conformance suite checks that SPIN is answered correctly, but it runs against a CODE skin,
// whose `spin` is undefined -- so the guard in protocol.js skips and nothing there ever proves the
// dispatch reaches an asset skin, or that it translates the line the way the skin expects. This
// drives the REAL parser (engine/protocol.js, the Tablet lane's) into a REAL asset skin.
{
  const { handle } = await import(join(here, "../engine/protocol.js"));
  const { sk, draw } = rig({ spin_dps: DPS });
  const skins = { current: sk, shownName: () => "t", error: null, select: () => ({ ok: true }) };
  const say = (line) => handle({}, { enabled: false }, line, 0, skins);

  let t = 0; draw(t);
  for (let i = 0; i < 60; i++) { t += DT; draw(t); }

  ok(say("SPIN COUNTER 3") === "OK spin counter", "the wire answers OK spin counter");
  const t0 = t; let zero = null, p = draw(t);
  for (let i = 0; i < 90; i++) {
    t += DT; const f = draw(t);
    if (zero === null && rate(p, f).r <= 0.5) zero = t - t0;
    p = f;
  }
  ok(zero !== null && Math.abs(zero - 0.45) <= 2 * DT,
     "a SPIN COUNTER line off the wire eases the skin through zero",
     `crossed at ${zero?.toFixed(3)}s`);

  // The mode must arrive lowercased and the hold as a NUMBER, or the skin silently mishandles it.
  const seen = [];
  const spy = { spin: (mode, holdS) => seen.push([mode, holdS]), };
  const say2 = (line) => handle({}, { enabled: false }, line, 0, { current: spy, shownName: () => "t", error: null });
  say2("SPIN COUNTER 2.5"); say2("SPIN TOGETHER"); say2("SPIN AUTO");
  ok(JSON.stringify(seen) === JSON.stringify([["counter", 2.5], ["together", null], ["auto", null]]),
     "the wire hands the skin lowercase modes and a numeric hold", JSON.stringify(seen));
}

// ---- 7. the exported param defaults ------------------------------------------------------------
// library.py checks an entry's param keys against this export, so it is load-bearing beyond the
// engine: a key dropped from it becomes a key the validator starts rejecting in valid entries.
{
  const keys = Object.keys(ASSET_PARAM_DEFAULTS).sort().join(",");
  ok(keys === "blink,counter,follow_look,layout,look,mirror,size,spin_dps",
     "the exported param defaults are the full set library.py validates against", keys);
  ok(Object.isFrozen(ASSET_PARAM_DEFAULTS), "and are frozen, so one skin cannot change another's defaults");

  // The hoist must not have changed any default: an entry with no params draws as it always did.
  const { draw } = rig({});
  const f = draw(1.0);
  ok(f.left === 0 && f.right === 0, "an entry with no params still has no spin (spin_dps 0)");

  const ck = Object.keys(ASSET_COUNTER_DEFAULTS).sort().join(",");
  ok(ck === "ease_s,every_s,hold_s", "the counter block's keys are exported too", ck);
  ok(Object.isFrozen(ASSET_COUNTER_DEFAULTS) && Object.isFrozen(ASSET_COUNTER_DEFAULTS.every_s)
     && Object.isFrozen(ASSET_COUNTER_DEFAULTS.hold_s),
     "and frozen through the arrays, which a config can hold by reference");
  // EASE_MIN is a clamp, not a key an entry may set: in the export, library.py would accept it.
  ok(!("ease_min" in ASSET_COUNTER_DEFAULTS) && !("ease_min" in ASSET_PARAM_DEFAULTS),
     "the clamp is not exported as a settable key");

  // A counter block that sets nothing must land exactly on the exported defaults.
  const bare = rig({ spin_dps: DPS, counter: {} });
  bare.sk.spin("counter", 1);
  const t0 = 0; let zero = null, q = bare.draw(t0), tt = t0;
  for (let i = 0; i < 90; i++) { tt += DT; const g = bare.draw(tt);
    if (zero === null && rate(q, g).r <= 0.5) zero = tt - t0; q = g; }
  ok(zero !== null && Math.abs(zero - ASSET_COUNTER_DEFAULTS.ease_s / 2) <= DT,
     "an empty counter block uses the exported ease_s", `crossed at ${zero?.toFixed(3)}s`);
}

console.log(failed ? `\n${failed} FAILED` : "\nall spin checks passed");
process.exit(failed ? 1 : 0);
