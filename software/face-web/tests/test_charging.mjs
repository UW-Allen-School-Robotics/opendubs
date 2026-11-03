// CHARGING: the state's new colour, the engine's battery state, and the edge gauge.
//
// The robot never showed charging at all before this -- the dashboard sent SAFE_IDLE while it
// charged, and CHARGING's teal was close enough to SAFE_IDLE's green that it would not have
// registered if it had been sent. So the checks below are about the two things that make it
// actually readable, and about the one failure mode the Tab5's version already shipped:
//
//   IT MUST READ WITHOUT COLOUR. The length of the gauge carries the level, and the words carry
//   it again. Checked by measuring the filled area at several levels, and by reading the text.
//
//   IT MUST NEVER INVENT A LEVEL. The Tab5's band shipped with `g_soc = 0.62f` and nothing ever
//   fed it, so it drew a confident 62% that meant nothing. An unknown or stale SOC here draws no
//   fill and says no number.
//
// Run: node software/face-web/tests/test_charging.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { Face } = await import(join(here, "../engine/face.js"));
const { STATES, THEMES } = await import(join(here, "../engine/vocab.js"));
const { drawCharge, CHARGE_BAND } = await import(join(here, "../engine/skins/charge.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

const colourOf = (name) => STATES.find((s) => s[0] === name)[2];
const rgb = (c) => [(c >> 16) & 255, (c >> 8) & 255, c & 255];
// Distance in hue AND lightness, because two colours that differ only in one are the pair people
// confuse across a room -- which is exactly what teal CHARGING beside green SAFE_IDLE was.
function hsl(c) {
  const [r, g, b] = rgb(c).map((v) => v / 255);
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
  let h = 0;
  if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
  h = (h * 60 + 360) % 360;
  return { h, l };
}
function hueGap(a, b) { const d = Math.abs(hsl(a).h - hsl(b).h); return Math.min(d, 360 - d); }

// ---- 1. the colour -----------------------------------------------------------------------------
const CHARGING = colourOf("CHARGING");
ok(CHARGING !== 0x00c4a8, "CHARGING is no longer the teal that read as SAFE_IDLE", "0x" + CHARGING.toString(16));
{
  const [r, g, b] = rgb(CHARGING);
  ok(b > r && r > g, "CHARGING is a purple (blue over red over green)", `${r},${g},${b}`);
}
for (const other of ["SAFE_IDLE", "AUTONOMOUS", "DEGRADED", "E_STOP", "INIT", "MANUAL"]) {
  const gap = hueGap(CHARGING, colourOf(other));
  const dl = Math.abs(hsl(CHARGING).l - hsl(colourOf(other)).l);
  ok(gap >= 40 || dl >= 0.30, `CHARGING is distinct from ${other}`, `hue ${gap.toFixed(0)} deg, lightness ${dl.toFixed(2)}`);
}
// The one it is closest to on purpose: ARMED's violet. Different hue is not available in the purple
// range, so the separation is lightness plus form -- and the gauge is the form.
{
  const ARMED_VIOLET = (198 << 16) | (92 << 8) | 255;
  const dl = hsl(ARMED_VIOLET).l - hsl(CHARGING).l;
  ok(dl >= 0.14, "CHARGING is clearly DEEPER than the ARMED overlay's violet", `${dl.toFixed(2)} lighter`);
}
// NO THEME RECOLOURS CHARGING: charging is purple everywhere, a rule a person learns once.
// This used to read `!("CHARGING" in THEMES.halloween)`, naming the one theme that existed. That
// is now a theme in the content folder, and a check that names a theme cannot survive a theme
// moving -- so it asks the question of EVERY built-in instead. The same rule for themes outside
// this repository is enforced by the loader and checked in test_themes.mjs, which is the half
// this one cannot see.
ok(Object.values(THEMES).every((t) => !("CHARGING" in t)),
   "no built-in theme recolours CHARGING: charging is purple everywhere");

// ---- 2. the engine's battery state ---------------------------------------------------------------
function charging(soc, isCharging = true, at = 1000, now = 1000) {
  const f = new Face();
  f.setState("CHARGING", 0);
  f.setBatt(soc, isCharging, at);
  f._now = now;
  return f;
}
ok(charging(97).chargeState(1000).soc === 97, "a BATT soc reaches the gauge");
ok(charging(97).chargeText(1000) === "Charging 97%", "and the line reads as a person would say it",
   charging(97).chargeText(1000));
ok(charging(100, false).chargeText(1000) === "On charge 100%",
   "a pack taking no current says 'On charge', not 'Charged' -- full and stalled look the same here",
   charging(100, false).chargeText(1000));
ok(charging(null).chargeState(1000).soc === null, "an unknown soc stays unknown; nothing is invented");
ok(charging(null).chargeText(1000) === "Charging", "and the line simply drops the number",
   charging(null).chargeText(1000));
ok(charging(140).chargeState(1000).soc === 100 && charging(-5).chargeState(1000).soc === 0,
   "an out-of-range soc is clamped rather than drawn past the end of the perimeter");
ok(charging(62.4).chargeState(1000).soc === 62, "a fractional soc becomes a whole percent");

// Stale: the gauge stays, the number goes.
{
  const f = charging(80, true, 1000, 1000 + 95000);
  const c = f.chargeState(1000 + 95000);
  ok(c !== null && c.soc === null, "a BATT older than the stale window loses its NUMBER but keeps the gauge",
     JSON.stringify(c));
  const fresh = charging(80, true, 1000, 1000 + 60000);
  ok(fresh.chargeState(1000 + 60000).soc === 80, "and two missed refreshes are still believed", "60 s");
}

// Only the state turns it on, and a reserved state turns it off.
{
  const f = new Face(); f.setState("SAFE_IDLE", 0); f.setBatt(90, true, 0); f._now = 0;
  ok(f.chargeState(0) === null, "a BATT with no CHARGING state draws nothing (the state is the authority)");
  ok(f.chargeText(0) === null, "and says nothing");
  const g = new Face(); g.setState("E_STOP", 0); g.setBatt(90, true, 0); g._now = 0;
  ok(g.chargeState(0) === null, "a reserved state wins over the gauge, as it does over a skin");

  // THAT LAST CHECK PASSES EVEN WITHOUT THE reserved() GUARD, because E_STOP is not CHARGING and
  // the state check catches it first -- a mutation proved exactly that, so the guard was untested
  // and would have been dead code anyone could delete. It is here for ONE future: CHARGING being
  // marked reserved. So that is what is tested, by marking it so.
  const row = STATES.find((x) => x[0] === "CHARGING");
  const was = row[4];
  try {
    row[4] = true;
    const h = new Face(); h.setState("CHARGING", 0); h.setBatt(90, true, 0); h._now = 0;
    ok(h.chargeState(0) === null && h.chargeText(0) === null,
       "and if CHARGING itself were ever made reserved, the gauge would stop with it");
  } finally { row[4] = was; }
}

// ---- 3. the gauge reads without colour -----------------------------------------------------------
// A context that measures the AREA painted in the charge colour, so "how much is filled" is read
// off the drawing rather than assumed from the input.
function meter() {
  const out = { filled: 0, track: 0, calls: 0, runs: 0, log: [] };
  let style = "";
  const ctx = {
    save() {}, restore() {}, beginPath() {}, fill() {}, stroke() {},
    fillRect(x, y, w, h) {
      out.calls++;
      const a = Math.abs(w * h);
      if (style.startsWith("rgba(109, 48, 224")) { out.filled += a; out.runs++; }
      else if (style.startsWith("rgba(255,255,255,0.1")) out.track += a;
      out.log.push(`fillRect(${Math.round(x)},${Math.round(y)},${Math.round(w)},${Math.round(h)})`);
    },
    createLinearGradient: () => ({ addColorStop() {} }),
    arc() {}, rect() {}, roundRect() {}, moveTo() {}, lineTo() {}, fillText() {}, clip() {},
  };
  Object.defineProperty(ctx, "fillStyle", { set(v) { style = String(v); } });
  Object.defineProperty(ctx, "strokeStyle", { set() {} });
  Object.defineProperty(ctx, "lineWidth", { set() {} });
  Object.defineProperty(ctx, "font", { set() {} });
  return [ctx, out];
}
function fillFor(soc, t = 0) {
  const [ctx, out] = meter();
  drawCharge(ctx, { soc, charging: true }, t);
  return out;
}

ok(fillFor(100).track > 0, "the gauge draws a track, so a part-full bar reads as a fraction");
{
  const full = fillFor(100).filled;
  const rows = [0, 10, 25, 50, 75, 97].map((s) => [s, fillFor(s).filled / full]);
  let monotonic = true;
  for (let i = 1; i < rows.length; i++) if (rows[i][1] <= rows[i - 1][1]) monotonic = false;
  ok(monotonic, "more charge is more bar, at every level", rows.map(([s, f]) => `${s}%->${(f * 100).toFixed(0)}`).join(" "));
  for (const [s, frac] of rows) {
    if (s === 0) continue;
    ok(Math.abs(frac - s / 100) < 0.03, `the bar at ${s}% is ${s}% of the perimeter`, `${(frac * 100).toFixed(1)}%`);
  }
  ok(fillFor(0).filled === 0, "an empty pack fills nothing");
}
ok(Math.abs(CHARGE_BAND.perimeter - 2 * (1280 + 720)) < 1, "the gauge is the whole perimeter", CHARGE_BAND.perimeter);
ok(CHARGE_BAND.thick <= 9, "and it is outside the ARMED frame, which starts 10 px in", CHARGE_BAND.thick);

// ---- 4. nothing invented, nothing left up ---------------------------------------------------------
// AN UNKNOWN LEVEL MUST NOT LOOK LIKE A LEVEL, and this is the check that earned its keep: the
// first version of the gauge drew the whole perimeter in the charge colour at low alpha for
// "unknown", and measuring the painted area showed that for what it is -- a FULL ring, which reads
// as 100%. Dashes cannot be read as any level, so that is what unknown draws now.
{
  const full = fillFor(100);
  const unknown = fillFor(null, 0.3);
  ok(unknown.calls > 0, "an unknown level still shows a ring: charging, level not known", `${unknown.calls} rects`);
  ok(unknown.filled < full.filled * 0.75,
     "and it covers clearly less of the perimeter than a full pack",
     `${(unknown.filled / full.filled * 100).toFixed(0)}% of full`);
  // The decisive one: a level is ONE run (four rectangles at most, one per edge); a dashed ring is
  // many. Area alone would pass for a solid ring drawn at, say, 70%.
  // Counted over the CHARGE-COLOURED rectangles only: the track and the highlight are drawn either
  // way and would blur the distinction.
  const runsFull = full.runs, runsUnknown = unknown.runs;
  ok(runsFull <= 4 && runsUnknown > 8,
     "a known level draws one continuous run; an unknown one is visibly dashed",
     `${runsFull} vs ${runsUnknown} rects`);
}
for (const [what, c] of [["null", null], ["undefined", undefined]]) {
  const [ctx, out] = meter();
  drawCharge(ctx, c, 1);
  ok(out.calls === 0, `draws NOTHING for ${what} -- a gauge up off the charger is a plain lie`);
}

// ---- 5. alive, and a pure function ---------------------------------------------------------------
{
  const a = JSON.stringify(fillFor(60, 1.0).log), b = JSON.stringify(fillFor(60, 1.0).log);
  const c = JSON.stringify(fillFor(60, 2.4).log);
  ok(a === b, "the same inputs draw the same thing");
  ok(a !== c, "the gauge is visibly live even while the level holds still");
  ok(Math.abs(fillFor(60, 1.0).filled - fillFor(60, 2.4).filled) < 1,
     "but the LEVEL does not move with time: only the highlight does");
}

console.log(failed ? `\n${failed} FAILED` : "\nall charging checks passed");
process.exit(failed ? 1 : 0);
