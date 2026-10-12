// The one-shot eye MOVES (head protocol v4) in engine/face.js, headless.
//
// The conformance suite checks that MOVE is ANSWERED correctly. This checks what the frames DO,
// which is where the promises to the other lanes live:
//
//   * each move's PUBLISHED PEAK is really when it reads -- the show runner sends the line early by
//     that number to land the gesture on the beat, so a shape retuned without its number following
//     it puts every show using that move off the beat;
//   * a move layers and then leaves: it never disturbs the emote, the pin or where the eyes look;
//   * firing on consecutive beats REPLACES rather than queues, or a show drifts further behind
//     with every beat;
//   * the gesture is the same whatever rate the face is drawn at.
//
// Run: node software/face-web/tests/test_moves.mjs   (conformance.sh runs it)
// With --table it prints the measured offsets, which is where the published numbers come from.

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { readFile } from "node:fs/promises";

const here = dirname(fileURLToPath(import.meta.url));
const { Face, MOVES } = await import(join(here, "../engine/face.js"));

const FRAME = 1000 / 60;
let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// What each move is FOR: the quantity that has to be extreme for the gesture to read, and which
// occurrence of it counts. A double blink reads on its SECOND shut, so "the first maximum" is the
// wrong answer for it and right for everything else -- which is exactly why this is written down.
const READS = {
  wink_l:  { of: (o) => o.lidL, nth: 1 },
  wink_r:  { of: (o) => o.lidR, nth: 1 },
  blink2:  { of: (o) => o.lidL, nth: 2 },
  look_l:  { of: (o) => -o.gx,  nth: 1 },
  look_r:  { of: (o) => o.gx,   nth: 1 },
  roll:    { of: (o) => -o.gy,  nth: 1 },      // the top of the circle
  bob:     { of: (o) => o.dy,   nth: 1 },
  sparkle: { of: (o) => o.sparkle, nth: 1 },
};

// Drive one move on a real Face and sample its contribution every millisecond.
function curve(name) {
  const f = new Face();
  const m = MOVES[name];
  const out = [];
  f.move(name, 0);
  for (let t = 0; t <= m.dur + 40; t++) { f.stepMove(t); out.push({ t, ...f.mvOut }); }
  return out;
}

// When the gesture reads: the START of the nth run at the maximum. The start, not the middle,
// because a lid that is fully shut for 40 ms reads the instant it gets there.
function readsAt(name) {
  const c = curve(name), probe = READS[name].of;
  const peak = Math.max(...c.map((o) => probe(o)));
  let runs = 0, inRun = false;
  for (const o of c) {
    const at = probe(o) >= peak - 1e-9;
    if (at && !inRun) { runs++; if (runs === READS[name].nth) return { t: o.t, peak }; }
    inRun = at;
  }
  return { t: null, peak };
}

// ---- 1. the published peak is the measured peak -------------------------------------------------
const table = [];
for (const name of Object.keys(MOVES)) {
  const { t, peak } = readsAt(name);
  table.push([name, MOVES[name].dur, MOVES[name].peak, t]);
  ok(t !== null && Math.abs(t - MOVES[name].peak) <= FRAME,
     `${name}: the published peak is when it reads`,
     `published ${MOVES[name].peak} ms, measured ${t} ms (max ${peak.toFixed(3)})`);
}

// ---- 2. a double blink reads on its SECOND shut ------------------------------------------------
// Guarding the thing READS encodes: if blink2's peak ever drifted onto the first shut it would
// still "measure correctly" against a naive first-maximum rule, and every show would be early.
{
  const c = curve("blink2");
  const first = c.find((o) => o.lidL >= 1 - 1e-9).t;
  ok(MOVES.blink2.peak > first + 100,
     "blink2's peak is the second shut, not the first",
     `first shut ${first} ms, published ${MOVES.blink2.peak} ms`);
}

// ---- 3. one-shot: it ends, and leaves nothing behind -------------------------------------------
for (const name of Object.keys(MOVES)) {
  const f = new Face();
  f.move(name, 0);
  f.stepMove(MOVES[name].dur + 1);
  const o = f.mvOut;
  const quiet = o.lidL === 0 && o.lidR === 0 && o.gx === 0 && o.gy === 0 && o.dy === 0 && o.sparkle === 0;
  ok(f.mv === null && quiet, `${name}: ends by itself and contributes nothing after`);
}

// ---- 4. replace, not queue ---------------------------------------------------------------------
{
  const f = new Face();
  f.move("wink_l", 0);
  f.stepMove(100);
  f.move("bob", 100);                       // a second beat, while the first is still playing
  f.stepMove(100 + MOVES.bob.peak);
  ok(f.mv.name === "bob" && f.mvOut.lidL === 0 && f.mvOut.dy > 0.1,
     "a move fired mid-gesture replaces it, and lands its own peak on time",
     `dy ${f.mvOut.dy.toFixed(3)}, lidL ${f.mvOut.lidL}`);
}

// ---- 5. it layers, and disturbs nothing --------------------------------------------------------
// The whole reason a show can fire a move on any beat: `EMOTE SOFT` + `MOVE wink_l` is a wink
// WHILE soft, and the face carries on afterwards exactly as it would have.
// A fresh Face starts with lid = 1 -- "the eyes open once, as the face's first act" -- so a wink
// is invisible until it has woken and settled. Run it until the lid is down and no blink is in
// flight, or this measures a shut eye and calls it a failed wink.
function settled(emote) {
  const f = new Face();
  f.setEmote(emote);
  for (let t = 0; t < 4000 && !(f.woke && !f.blinking && f.lid.value < 0.2); t += 16) f.tick(t, 0.016);
  return f;
}
{
  const f = settled("SOFT");
  // Every piece of the face's own timing, not just its current values. A move that nudged
  // `nextEmote` would change expression early for the rest of the show, and an earlier version of
  // this check watched only the values -- so a deliberately broken build passed it.
  const keys = ["emote", "pin", "nextBlink", "nextEmote", "nextSaccade", "nextQuote"];
  const before = Object.fromEntries(keys.map((k) => [k, f[k]]));
  before.gx = f.gazeX.target; before.gy = f.gazeY.target;
  const T = 5000;                     // not 0: several schedules ARE 0 here, and a move that set
  f.move("wink_l", T);                // one to `now` would be undetectable at now = 0
  f.stepMove(T + MOVES.wink_l.peak);
  const changed = keys.filter((k) => f[k] !== before[k]);
  ok(changed.length === 0 && f.gazeX.target === before.gx && f.gazeY.target === before.gy,
     "a move changes no emote, pin, gaze target or any of the face's own schedules",
     changed.length ? `changed: ${changed.join(", ")}` : "");

  // And it SHOWS: the left eye is shut while the right is not.
  const [l, r] = f.eyes();
  ok(l.lid > 0.9 && r.lid < 0.5, "wink_l shuts the left eye only",
     `left ${l.lid.toFixed(3)}, right ${r.lid.toFixed(3)}`);
  ok(f.emote === "SOFT", "and the emote underneath is still SOFT");
}

// ---- 6. the same gesture whatever the frame rate -----------------------------------------------
// A pure function of age, so a hidden tab or a slow frame cannot change how it looks.
{
  const at = MOVES.roll.peak;
  const sample = (stepMs) => { const f = new Face(); f.move("roll", 0);
    for (let t = 0; t <= at; t += stepMs) f.stepMove(Math.min(t, at)); f.stepMove(at); return f.mvOut; };
  const a = sample(1), b = sample(FRAME), c = sample(97);      // 1000 fps, 60 fps, and a stutter
  const same = (x, y) => Math.abs(x.gx - y.gx) < 1e-12 && Math.abs(x.gy - y.gy) < 1e-12;
  ok(same(a, b) && same(a, c), "the gesture is identical at 1000 fps, 60 fps and through a stutter");
}

// ---- 7. sparkle is 0..1, and absent unless asked for -------------------------------------------
{
  const f = new Face();
  ok(f.eyes()[0].sparkle === 0, "sparkle is 0 when no move is playing");
  f.move("sparkle", 0);
  let worst = 0, hi = 0;
  for (let t = 0; t <= MOVES.sparkle.dur; t++) { f.stepMove(t);
    const v = f.mvOut.sparkle; hi = Math.max(hi, v); if (v < 0 || v > 1) worst = v; }
  ok(worst === 0 && hi === 1, "sparkle stays within 0..1 and reaches 1", `max ${hi}`);
}

// ---- 8. the table stays in the shape two OTHER lanes parse -------------------------------------
// THIS GUARD EXISTS SO A COSMETIC EDIT FAILS HERE RATHER THAN IN SOMEONE ELSE'S CI.
//
// Three things read MOVES, and only one of them imports it. The head protocol's
// test_head_protocol.py and the show validator's test_face_moves.py both read face.js as TEXT, with
// regexes that need each entry on ONE LINE and `dur:` BEFORE `peak:`. So reformatting this table --
// wrapping a long entry, swapping two keys, nothing that changes behaviour at all -- turns both of
// their suites red, on a PR that looks like it only touched the face. Neither of their checks runs
// here first, and the error they give is about a missing name rather than about formatting.
//
// They cannot import a JS object from Python, so the text coupling is not their fault and not
// removable today. What IS removable is the surprise: this reproduces both patterns and fails with
// the reason, so whoever reformats learns it from their own test run.
{
  const src = await readFile(join(here, "../engine/face.js"), "utf8");
  // NOT `split(sep, 1)[1]`: JavaScript's limit truncates the RESULT ARRAY, so that is undefined --
  // the same expression in Python (which the other two suites use) means "split once" and works.
  // An earlier `?? ""` here turned that into an empty block, and every move then failed with
  // "not on one line" instead of the truth. Fail loudly rather than plausibly.
  const at = src.indexOf("export const MOVES");
  if (at < 0) throw new Error("face.js has no `export const MOVES`");
  const block = src.slice(at).split("\n};")[0];
  const bad = [];
  for (const [name, m] of Object.entries(MOVES)) {
    // head-protocol's pattern, and choreography's stricter `dur:` then `peak:` order, in one.
    const re = new RegExp(`^\\s*${name}:\\s*\\{\\s*dur:\\s*(\\d+),\\s*peak:\\s*(\\d+)`, "m");
    const hit = re.exec(block);
    if (!hit) bad.push(`${name}: not on one line as \`${name}: { dur: N, peak: M, ...\``);
    else if (+hit[1] !== m.dur || +hit[2] !== m.peak) bad.push(`${name}: text says ${hit[1]}/${hit[2]}, object says ${m.dur}/${m.peak}`);
  }
  ok(bad.length === 0,
     "the MOVES table is still in the shape the head-protocol and show-validator suites parse",
     bad.join("; "));
}

if (process.argv.includes("--table")) {
  console.log("\n  move      dur ms   published   measured");
  for (const [n, d, p, t] of table) console.log(`  ${n.padEnd(9)} ${String(d).padStart(5)} ${String(p).padStart(10)} ${String(t).padStart(10)}`);
}
console.log(failed ? `\n${failed} FAILED` : "\nall move checks passed");
process.exit(failed ? 1 : 0);
