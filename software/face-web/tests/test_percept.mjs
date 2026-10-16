// The tablet face's attention (engine/percept/), held to the Tab5's own self-test (percept/selftest.cpp),
// case by case, plus the tablet's third cue, PERSON. Headless and pure: synthetic frames, given times.
//
//   node software/face-web/tests/test_percept.mjs

import { Motion, W, H } from "../engine/percept/motion.js";
import { Attention, KIND } from "../engine/percept/attention.js";

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };

// A mid-grey scene with a little sensor noise (seeded), and a bright square "person".
let seed = 7;
const noise = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return (seed % 7) - 3; };
function frame(blob) {
  const f = new Uint8Array(W * H);
  for (let i = 0; i < f.length; i++) f[i] = 100 + noise();
  if (blob) for (let y = blob.y; y < blob.y + blob.s; y++) for (let x = blob.x; x < blob.x + blob.s; x++)
    if (x >= 0 && y >= 0 && x < W && y < H) f[y * W + x] = 200;
  return f;
}

// ---- motion -----------------------------------------------------------------------------------
{
  const m = new Motion();
  m.update(frame());
  let any = false;
  for (let i = 0; i < 30; i++) any = m.update(frame()).valid || any;
  check("a static scene produces no target", !any, "a target");
}
{
  const m = new Motion();                               // a blob sweeping left to right
  m.update(frame({ x: 10, y: 70, s: 40 }));
  let hits = 0, checked = 0, worst = 0;
  for (let x = 14; x < W - 50; x += 4) {
    const r = m.update(frame({ x, y: 70, s: 40 }));
    if (x < 40) continue;                               // the heat needs a few frames to build
    checked++;
    if (r.valid) {
      hits++;
      const want = ((x + 20) / W) * 2 - 1;
      worst = Math.max(worst, Math.abs(r.nx - want));
    }
  }
  const pct = Math.round(100 * hits / checked);
  check("a moving blob is tracked in >= 85 % of frames", pct >= 85, `${pct} %`);
  check("  and where it is (within a cell and a half)", worst < 0.2, worst.toFixed(2));
}
{
  const m = new Motion();
  let r;
  m.update(frame({ x: 20, y: 10, s: 30 }));
  for (let i = 0; i < 10; i++) r = m.update(frame(i % 2 ? { x: 20, y: 10, s: 30 } : { x: 30, y: 16, s: 30 }));
  check("a top-left blob reads as left and up (both negative)", r.valid && r.nx < 0 && r.ny < 0, JSON.stringify(r));
}
{
  const m = new Motion();
  m.update(frame());
  const all = new Uint8Array(W * H).fill(230);          // the light changes, or the head turns
  const r = m.update(all);
  check("a whole-frame change is gated", r.gated && !r.valid, JSON.stringify(r));
  check("  and the frame after it produces no target", !m.update(all).valid, "a target");
}
{
  const m = new Motion();
  m.update(frame());
  let bad = false, last;
  for (let x = 20; x < 120; x += 10) { last = frame({ x, y: 60, s: 40 }); const r = m.update(last, true); if (r.valid || !r.gated) bad = true; }
  check("frames while the robot is moving publish nothing", !bad, "a target");
  // The Tab5's point: a suppressed frame is still history, so when the robot settles the (unchanged)
  // scene is not seen as having all moved.
  check("  nor does the first frame after it settles", !m.update(last).valid, "a target");
}

// ---- attention ------------------------------------------------------------------------------------
{
  const a = new Attention();
  check("nothing noted: no target", a.tick(0).kind === KIND.NONE, a.tick(0).kind);
  a.noteMotion(10, 0.5, 0.0, 0.5);
  const m = a.tick(10);
  check("motion alone wins", m.kind === KIND.MOTION && m.confidence <= 0.7, JSON.stringify(m));
  a.notePerson(20, -0.3, -0.2, 0.4);
  const p = a.tick(20);
  check("a person beats motion", p.kind === KIND.PERSON, p.kind);
  a.noteFace(30, -0.3, -0.4, 0.35);                     // weak face
  const f = a.tick(30);
  check("a weak face beats a person and strong motion", f.kind === KIND.FACE, f.kind);
  check("  switching cue is a new target (seq)", f.seq !== p.seq && p.seq !== m.seq, `${m.seq},${p.seq},${f.seq}`);
}
{
  const weak = new Attention(), strong = new Attention();
  weak.noteFace(0, 0, 0, 0.35);
  for (let i = 0; i < 4; i++) strong.noteFace(i * 100, 0, 0, 0.95);
  const w = weak.tick(0), s = strong.tick(300);
  check("a repeated strong face is believed more than a lone weak one", s.confidence > w.confidence, `${s.confidence} vs ${w.confidence}`);
  check("  and held longer", s.goodUntil - 300 > w.goodUntil - 0, `${s.goodUntil - 300} vs ${w.goodUntil}`);
}
{
  const a = new Attention();
  a.noteFace(0, 0.10, 0.10, 0.9);
  const t0 = a.tick(0);
  a.noteFace(50, 0.12, 0.11, 0.9);
  check("a small drift keeps the same target", a.tick(50).seq === t0.seq, "a new seq");
  a.noteFace(100, 0.58, 0.10, 0.9);
  check("a big move is a new target", a.tick(100).seq !== t0.seq, "the same seq");
  check("a target 60 s old is gone", a.tick(60100).kind === KIND.NONE, a.tick(60100).kind);
}

{
  // A cue outlives its sighting by its dwell, at the position it was seen at. The neck must tell a NEW
  // sighting from that held one (2026-10-16: it re-aimed at a held face for 2 s and ran to its limit).
  const a = new Attention();
  a.noteFace(1000, -0.46, -0.04, 0.9, 950);              // the frame was captured at 950
  const held = [1000, 1200, 1400, 1600].map((t) => a.tick(t));
  check("a held face keeps its sighting time", held.every((g) => g.kind === KIND.FACE && g.seen === 950),
        held.map((g) => g.seen).join(","));
  a.noteFace(1700, -0.20, -0.04, 0.9, 1650);
  check("a new sighting has a new time", a.tick(1700).seen === 1650, a.tick(1700).seen);
  a.noteMotion(1800, 0.5, 0, 0.6);
  check("  a cue noted without one is seen when noted", a.motion.seen === 1800, a.motion.seen);
  check("nobody seen has no sighting", new Attention().tick(0).seen === 0, "a sighting");
}

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
