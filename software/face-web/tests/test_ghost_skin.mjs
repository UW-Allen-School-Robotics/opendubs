// THE HALLOWEEN GHOST'S CHOICE OF EXPRESSION, from the demo runner's state and the face tracker.
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

ok(ghost.personInView({ target: [0.5, 0.5, 0.9], target_age_s: 0.2 }), "a target just now: someone in view");
ok(!ghost.personInView({ target: [0.5, 0.5, 0.9], target_age_s: 3 }), "a stale target: nobody");
ok(!ghost.personInView({ target: null, target_age_s: null }), "no target: nobody");
ok(!ghost.personInView(null), "no tracker answer: nobody");

console.log(failed ? `\n${failed} FAILED` : "\nall ghost-skin checks passed");
process.exit(failed ? 1 : 0);
