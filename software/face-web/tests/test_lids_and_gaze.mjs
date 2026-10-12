// What the maintainers chose by looking at #136, now that it ships: expression from the LIDS'
// shape rather than by scaling the eye, a gaze that BREAKS instead of staring, and a pupil that no
// emote touches. Each was a decision, so each gets a check that would notice it being undone.
//
// Run: node software/face-web/tests/test_lids_and_gaze.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { EMOTES, SHAPES } = await import(join(here, "../engine/vocab.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

function woken(emote = "NEUTRAL") {
  const f = new Face();
  f.setState("SAFE_IDLE", 0);
  f.setEmote(emote, 0);
  for (let t = 0; t < 2000; t += 16) { f.tick(t, 0.016); f.stateAt = t; }
  return f;
}

// ---- 1. the gaze BREAKS from a person who never moves -------------------------------------------
// The failure this fixes is the one a person actually notices: the robot used to hold their gaze
// for as long as they stood there, because a confident target rewrote the next saccade every frame.
{
  const f = woken();
  let away = 0, wasAway = false, backAfterAway = 0;
  for (let t = 2000; t < 62000; t += 16) {
    f.look({ x: 0.5, y: 0.0, conf: 1, at: t });     // one person, dead still, for a minute
    f.tick(t, 0.016); f.stateAt = t;
    // "away" = aiming somewhere clearly off the person
    const isAway = Math.hypot(f.gazeX.target - 0.5 * (0.35 + 0.65), f.gazeY.target) > 0.2;
    if (isAway && !wasAway) away++;
    if (!isAway && wasAway) backAfterAway++;
    wasAway = isAway;
  }
  ok(away >= 8, "the eyes break away from a person who never moves", `${away} breaks in 60 s`);
  ok(backAfterAway >= away - 1, "and come back to them each time", `${backAfterAway} returns`);
  // Not so often that it reads as distraction: the design is a break every few seconds.
  ok(away <= 25, "and not so often that it reads as distraction", `${away} in 60 s`);
}

// ---- 2. with nobody there, it is the old wandering, and the break timer resets -------------------
{
  const f = woken();
  for (let t = 2000; t < 12000; t += 16) { f.tick(t, 0.016); f.stateAt = t; }
  ok(f.nextAvert === 0, "with nobody to look at, the break timer is cleared");
}

// ---- 3. expression comes from the LIDS, not from scaling the eye --------------------------------
// The old vocabulary could only squash the whole eye. SOFT is the case that shows it: it used to be
// 0.95 x 0.88 of the body, and now it is full size with its meaning carried by the lids.
{
  const soft = woken("SOFT");
  const neutral = woken("NEUTRAL");
  ok(Math.abs(soft.bodyW.target - neutral.bodyW.target) < 1e-9
     && Math.abs(soft.bodyH.target - neutral.bodyH.target) < 1e-9,
     "SOFT is the same size as NEUTRAL -- the eye no longer squashes to emote",
     `SOFT ${soft.bodyW.target.toFixed(0)}x${soft.bodyH.target.toFixed(0)}`);
  ok(EMOTES.SOFT[0] !== SHAPES.SOFT.wm || EMOTES.SOFT[1] !== SHAPES.SOFT.hm,
     "  and that is a real change from the old table, not a coincidence",
     `old ${EMOTES.SOFT[0]}x${EMOTES.SOFT[1]}, new ${SHAPES.SOFT.wm}x${SHAPES.SOFT.hm}`);

  const [e] = soft.eyes();
  ok(Math.abs(e.lidLower - SHAPES.SOFT.ll) < 0.02 && Math.abs(e.lidCurve - SHAPES.SOFT.lc) < 0.02,
     "  and the lids carry it instead", `lower ${e.lidLower.toFixed(2)}, curve ${e.lidCurve.toFixed(2)}`);
}

// ---- 4. every emote has a shape, and the blink rests where the emote does ------------------------
// The prototype crashed here: applyEmote was guarded for a name the other table lacked and the
// blink was not, so the first blink threw. One table, one resting lid, checked for all of them.
for (const name of Object.keys(EMOTES)) {
  ok(SHAPES[name] !== undefined, `${name} has a lid shape`);
}
{
  const f = woken("SLEEPY");
  let blinks = 0, wasBlink = false;
  for (let t = 2000; t < 40000; t += 16) {
    f.tick(t, 0.016); f.stateAt = t;
    if (f.blinking && !wasBlink) blinks++;
    wasBlink = f.blinking;
  }
  ok(blinks > 5, "it blinks", `${blinks} blinks`);
  ok(!f.blinking ? Math.abs(f.lid.target - SHAPES.SLEEPY.lu) < 1e-9 : true,
     "and the blink leaves the lid where the emote rests", `target ${f.lid.target}`);
}

// ---- 5. no emote touches the pupil ---------------------------------------------------------------
// "The pupil is too big for several of them -- in general we're walking into the uncanny valley."
// The channel is gone rather than tuned, so there is nothing to creep back.
{
  const fields = new Set();
  for (const name of Object.keys(EMOTES)) for (const e of woken(name).eyes()) Object.keys(e).forEach((k) => fields.add(k));
  ok(!fields.has("pupil"), "the per-eye inputs carry no pupil channel at all", [...fields].join(","));
  ok(!("pu" in SHAPES.NEUTRAL), "and the shape table has no pupil field");
}

console.log(failed ? `\n${failed} FAILED` : "\nall lid and gaze checks passed");
process.exit(failed ? 1 : 0);
