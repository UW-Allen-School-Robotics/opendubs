// THE HALLOWEEN GHOST'S CHOICE OF EXPRESSION, from the demo day's coordinator, the demo runner's state
// and the face tracker, and the dance it puts on for each show.
//
// Why it earns its place: the ghost is a content skin, and which of its expressions shows is decided by
// a small table (content/face/skins/ghost.js, ghostStage). A wrong row is a face that LOOKS FINE and is
// the wrong one -- FOCUS after the candy is handed over, or HAPPY when the run failed -- which nobody at
// the robot can tell from a bug in the runner. The drawing itself is checked by eye on the tablet.
//
// Run: node software/face-web/tests/test_ghost_skin.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const ghost = await import(join(here, "../content/face/skins/ghost.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// It is a skin the engine will take: the same checks skins/index.js makes of a content module.
ok(typeof ghost.draw === "function" && ghost.name === "ghost" && typeof ghost.stop === "function",
   "exports draw, name and stop, as a content code skin must");

const R = (state, since_s, extra = {}) => ({ available: true, state, since_s, ...extra });
const rows = [
  [null, "idle", "no runner: idles"],
  [{ available: false }, "idle", "runner down: idles"],
  [R("IDLE", 40), "idle", "IDLE"],
  [R("AWAITING_YES", 2), "idle", "waiting for the supervisor's yes"],
  [R("ARMED", 30, { last: "approved" }), "idle", "ARMED and waiting for a trick-or-treater"],
  [R("RUNNING", 0.2, { last: "run_started" }), "accepted", "a run just started: EEK!"],
  [R("RUNNING", 3.0), "accepted", "... still EEK! at 3 s"],
  [R("RUNNING", 5.5), "accepted", "... then SMILE at the person who asked"],
  [R("RUNNING", 6.2), "picking", "then FOCUS while the arm works"],
  [R("ARMED", 1, { last: "run_ended" }), "complete", "the run ended: HAPPY"],
  [R("ARMED", 6, { last: "run_ended" }), "idle", "... and back to waiting after 5 s"],
  [R("ARMED", 1, { last: "run_failed" }), "puzzled", "the run failed: PUZZLED"],
  [R("CHECK_FAILED", 20), "puzzled", "CHECK_FAILED: PUZZLED"],
  [R("GENERATING", 4), "puzzled", "writing a program: PUZZLED"],
  [R("ARMED", 30, { refused_s: 1 }), "puzzled", "a refused trigger just now (\"Hold on, I'm thinking!\")"],
  [R("ARMED", 30, { refused_s: 9 }), "idle", "... not long after"],
];
for (const [runner, want, what] of rows) {
  const got = ghost.ghostStage(runner);
  ok(got === want, what, `${got}${got === want ? "" : " (want " + want + ")"}`);
}

// The demo day's coordinator comes first: its candy show is a palette show, which the runner never sees.
const D = (state, since_s, extra = {}) => ({ available: true, state, since_s, ...extra });
const dayRows = [
  [D("IDLE_NAV", 30, { nav: "patrol" }), null, "idle", "patrolling: idles"],
  [D("APPROACH", 2), null, "idle", "on the way to someone: idles (and smiles, below)"],
  [D("CANDY", 0.3, { why: "trick or treat" }), null, "accepted", "trick or treat granted: EEK!"],
  [D("CANDY", 5.5), null, "accepted", "... then SMILE at the person who asked"],
  [D("CANDY", 6.5), null, "picking", "then FOCUS while the candy show runs"],
  [D("ENGAGED", 1, { why: "architect-bb75cd239645 ended" }), null, "complete", "the candy show ended: HAPPY"],
  [D("ENGAGED", 6, { why: "architect-bb75cd239645 ended" }), null, "idle", "... and back after 5 s"],
  [D("ENGAGED", 1, { why: "architect-bb75cd239645 aborted: DEADMAN" }), null, "puzzled", "it stopped part-way: PUZZLED"],
  [D("ENGAGED", 1, { why: "architect-bb75cd239645 never reached the palette within 15 s" }), null, "puzzled", "it never started: PUZZLED"],
  [D("ENGAGED", 1, { why: "stopped 1.2 m from them" }), null, "idle", "stopped in front of someone: idles"],
  [D("CHOREO", 0, { show: "wave-and-nod" }), null, "dance", "a palette show: the dance"],
  [D("PAUSED", 100), R("RUNNING", 0.5), "accepted", "coordinator paused, the runner's own run: the runner's EEK!"],
  [{ available: false }, R("RUNNING", 7), "picking", "no coordinator: the runner, as before"],
];
for (const [day, runner, want, what] of dayRows) {
  const got = ghost.ghostStage(runner, day);
  ok(got === want, what, `${got}${got === want ? "" : " (want " + want + ")"}`);
}

// A show's id, whichever way it is written, finds its dance; its costume and looks exist.
ok(ghost.danceKey("wave_and_nod") === "wave-and-nod" && ghost.danceKey("Monster-Mash") === "monster_mash",
   "show ids from the palette or ENCORE find their dance");
const LOOKS = ["groove", "beatpop", "tick", "nod", "wavelook", "wave-r", "wave-l", "bow", "sway", "creep", "rise", "drowsy",
               "cheer", "smile", "focus", "puzzled", "lookout", "peek", "eek", "happy", "doze"];
for (const [id, d] of Object.entries(ghost.DANCES)) {
  const bad = Object.values(d.looks || {}).flat().filter((l) => !LOOKS.includes(l));
  ok(d.costume in ghost.COSTUMES && !bad.length, `${id}: costume ${d.costume}, every look drawn`, bad.join(" "));
  // a timeline's typo would hold the last look silently: every step a known look and costume, in order
  const steps = d.steps || [], wrong = steps.filter(([t, l, c], i) => !LOOKS.includes(l) || (c && !(c in ghost.COSTUMES)) || (i && t <= steps[i - 1][0]));
  if (steps.length) ok(!wrong.length && steps[0][0] === 0, `${id}: ${steps.length} steps from 0 s, each a look and costume, in order`, JSON.stringify(wrong));
}
ok(Object.values(ghost.EMOTE_LOOKS).every((l) => LOOKS.includes(l)), "every emote has a look");
ok(ghost.TIMELINE.looks.every((l) => LOOKS.includes(l)) && ghost.TIMELINE.costumes.every((c) => c in ghost.COSTUMES),
   "a studio is offered only looks and costumes the ghost draws");

// The emote read back from the eye: the lids' bow and the brow each emote gives (engine/vocab.js).
for (const [name, lidCurve, browRaise] of [["NEUTRAL", 0, 0], ["ALERT", -0.5, 1], ["SQUINT", 0.3, -0.45],
                                          ["SOFT", -0.2, 0.3], ["SLEEPY", 0.8, -0.6], ["SOFT", -0.18, 0.5]]) {
  const got = ghost.emoteOf({ lidCurve, browRaise });
  ok(got === name, `the eye's shape reads as ${name}`, got);
}

ok(ghost.personInView({ target: [0.5, 0.5, 0.9], target_age_s: 0.2 }), "a target just now: someone in view");
ok(!ghost.personInView({ target: [0.5, 0.5, 0.9], target_age_s: 3 }), "a stale target: nobody");
ok(!ghost.personInView({ target: null, target_age_s: null }), "no target: nobody");
ok(!ghost.personInView(null), "no tracker answer: nobody");

// Every look a timeline may hold draws, in every costume. A look that throws freezes the face on its last
// frame with no error anyone sees: FOCUS danced did, asking for a pose that doesn't exist. Drawn on a
// canvas that does nothing, so this checks only that a frame completes.
{
  const nothing = new Proxy(function () {}, { get: (_, k) => k === "getTransform" ? () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 })
    : k === "isPointInPath" ? () => true : nothing, apply: () => nothing, set: () => true });
  globalThis.Path2D ??= class { moveTo() {} lineTo() {} bezierCurveTo() {} quadraticCurveTo() {} closePath() {} ellipse() {} rect() {} };
  globalThis.OffscreenCanvas ??= class { constructor(w, h) { this.width = w; this.height = h; } getContext() { return nothing; } };
  globalThis.window ??= {};
  const out = new Proxy({ canvas: { width: 640, height: 360 } }, { get: (o, k) => (k in o ? o[k] : () => {}) });
  let clock = 0;
  for (const c of ghost.TIMELINE.costumes) {
    const steps = ghost.TIMELINE.looks.map((l, i) => [i * 2, l, c]);
    let broke = "";
    for (let s = 0; s < steps.length * 2 && !broke; s += 1 / 15) {
      window.ghostInputs = { day: { available: true, state: "CHOREO", show: "celebrate", since_s: s }, person: false, steps };
      try { ghost.draw(out, { side: "left" }, (clock += 1 / 15)); } catch (e) { broke = `${steps[Math.floor(s / 2)][1]}: ${e.message}`; }
    }
    ok(!broke, `every look draws in the ${c} costume`, broke);
  }
  ghost.stop();

  // And a show's steps hand over smoothly: played at 30 fps, the eyes never jump in one frame at a step's
  // start (each step's look used to replace the last at once). Meant to be sudden, and left out: into
  // EEK! and PEEK, waking from DOZE, and BEAT POP's own pop on the beat.
  const change = (a, b) => Math.max(...a.map((e, i) => Math.max(...Array.from(e, (v, j) => Math.abs(v - b[i][j])))));
  for (const show of ["wave-and-nod", "robotic-smile", "celebrate"]) {
    const steps = ghost.TIMELINE.steps(show);
    let before = null, worst = "", most = 0;
    for (let f = 0; f <= (steps[steps.length - 1][0] + 0.5) * 30; f++) {
      const s = f / 30;
      window.ghostInputs = { day: { available: true, state: "CHOREO", show, since_s: s }, person: false, steps };
      ghost.draw(out, { side: "left" }, (clock += 1 / 30));
      const eyes = ghost.snapshot().eyes.map((e) => Array.from(e));
      const k = steps.findIndex((st) => st[0] <= s + 1e-6 && s - st[0] < 0.1);   // a step that has just started
      if (before && k > 0) {
        const [from, to] = [steps[k - 1][1], steps[k][1]];
        const meant = to === "eek" || to === "peek" || from === "doze" || from === "beatpop" || to === "beatpop";
        const d = change(before, eyes);
        if (!meant && d > most) { most = d; worst = `${from} → ${to} at ${steps[k][0]} s`; }
      }
      before = eyes;
    }
    ok(most <= 12, `${show}: each step fades in from the last, no jump`, worst ? `${worst}: ${most.toFixed(0)}` : "");
    ghost.stop();
  }
  delete window.ghostInputs;
}

console.log(failed ? `\n${failed} FAILED` : "\nall ghost-skin checks passed");
process.exit(failed ? 1 : 0);
