// A THEME LOADED FROM THE CONTENT FOLDER — per-state colours from a repository this CI never
// builds, which is the whole reason these checks exist.
//
// A theme cannot crash and cannot draw, so EVERY failure here is silent. A typo'd state name, a
// colour written as "lime", a theme that recolours CHARGING — each gives a face that comes up
// looking plausible and is not what the robot means. Nobody at the robot can tell a theme that
// was refused from a theme whose author wrote the wrong key; that is the distinction this file
// makes, and it is the only place it is made.
//
// THE RULE WORTH RESTATING: charging is purple in every theme. A person learns it once, and a
// theme that made it green would unteach it (vocab.js). CHARGING cannot be marked `reserved` to
// enforce that — doing so would replace the skin with the default eyes and suppress the gauge,
// taking the charging face apart — so the loader's refusal is the only thing in the way.
//
// The theme FILES are real and read through the real fetch path; only `fetch` itself is stubbed,
// because Node's does not do file://. The looks list is inline rather than a fixture file: it is
// three lines, and keeping it here means this test does not share a fixture with the skin loader.
//
// Run: node software/face-web/tests/test_themes.mjs   (conformance.sh runs it)

import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { readFileSync } from "node:fs";

globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };
globalThis.fetch = async (url) => {
  const u = String(url);
  if (!u.endsWith(".json")) throw new Error(`unexpected fetch ${u}`);
  let text;
  try { text = readFileSync(fileURLToPath(u), "utf8"); } catch { return { ok: false, status: 404, json: async () => null }; }
  return { ok: true, status: 200, json: async () => JSON.parse(text) };
};

const here = dirname(fileURLToPath(import.meta.url));
const BASE = pathToFileURL(join(here, "fixtures", "themes") + "/");
const { Face } = await import(join(here, "../engine/face.js"));
const { STATES, THEMES } = await import(join(here, "../engine/vocab.js"));
const { readTheme, parseColour } = await import(join(here, "../engine/themes.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}
const colourOf = (name) => STATES.find((s) => s[0] === name)[2];
const listed = (...ids) => ({ themes: ids.map((id) => ({ id, source: "content" })) });
const ALL = listed("fixture_theme", "fixture_charge", "fixture_estop", "fixture_typo",
                   "fixture_named", "fixture_ints", "fixture_missing", "plain");

// ---- 1. what is taken, and what is refused ------------------------------------------------------
const face = new Face();
const problems = await face.loadContentThemes(ALL, BASE);
const took = (id) => face.contentThemes.has(id);
const said = (id) => problems.some((p) => p.startsWith(id + ":"));

ok(took("fixture_theme"), "a content theme is fetched and taken");
ok(!("fixture_theme" in THEMES), "and it is NOT a built-in: nothing in this repo names it");
ok(took("fixture_ints"), "a colour given as a plain integer is taken too");

// THE ONE THAT MATTERS. Refused WHOLE, not stripped of its CHARGING key: the dashboard refuses
// the whole theme too, and the two sides disagreeing about what exists is worse than a lost theme.
ok(!took("fixture_charge"), "a theme recolouring CHARGING is refused");
ok(said("fixture_charge"), "and SAYS SO rather than vanishing", problems.join(" | "));
ok(!took("fixture_estop"), "a theme recolouring a reserved state is refused");

// The typo cases. Nothing else in the world would catch these.
ok(!took("fixture_typo"), "an unknown state name is refused, not ignored");
ok(!took("fixture_named"), "a colour that is not #rrggbb is refused, never coerced");
ok(!took("fixture_missing"), "a theme listed but not deployed is refused");
ok(said("fixture_missing"), "and names the file, rather than failing silently");
ok(!took("plain"), "a content theme shadowing a built-in is refused");
ok(problems.length === 6, "every refusal is reported, none silently",
   `${problems.length}: ${problems.join(" | ")}`);

// ---- 2. the engine uses it ----------------------------------------------------------------------
{
  face.setState("SAFE_IDLE");
  ok(face.setTheme("fixture_theme"), "THEME <content id> is accepted");
  ok(face.stateColour() === 0x8ac926, "and the state takes the theme's colour",
     "0x" + face.stateColour().toString(16));

  // A theme is a PARTIAL override, not a palette the face must be given in full.
  face.setState("AUTONOMOUS");
  ok(face.stateColour() === colourOf("AUTONOMOUS"), "a state the theme omits keeps its own colour");

  // `THEME HALLOWEEN` has always worked. Moving themes out of the repository must not quietly
  // make the protocol case-sensitive.
  ok(face.setTheme("FIXTURE_THEME"), "the id is case-insensitive, as a built-in's always was");
  ok(!face.setTheme("fixture_charge"), "a refused theme is unknown to THEME, not merely inert");
  ok(face.setTheme("none") && face.theme === "plain", "NONE still means plain");

  // A reserved state ignores the theme whatever it says -- the other half of the rule, enforced
  // downstream rather than at load, and worth pinning because the two live in different files.
  face.setTheme("fixture_theme");
  face.setState("E_STOP");
  ok(face.stateColour() === colourOf("E_STOP"), "a reserved state keeps its colour under a theme");
}

// ---- 3. a theme that goes away ------------------------------------------------------------------
// The content folder is redeployed, or an edit gets the active theme refused. Nothing may be left
// reading colours off a theme that is no longer there.
{
  const f = new Face();
  await f.loadContentThemes(listed("fixture_theme"), BASE);
  f.setTheme("fixture_theme");
  f.setState("SAFE_IDLE");
  ok(f.stateColour() === 0x8ac926, "a theme is showing");

  await f.loadContentThemes({ themes: [] }, BASE);
  ok(f.theme === "plain", "a theme disappearing falls back to plain", f.theme);
  ok(f.stateColour() === colourOf("SAFE_IDLE"), "and the state shows its own colour again");
}

// ---- 4. nothing at all ---------------------------------------------------------------------------
// A robot with no content folder, or a dashboard too old to list themes, must still come up.
{
  const f = new Face();
  const none = await f.loadContentThemes({}, BASE);
  ok(none.length === 0 && f.setTheme("plain"), "no themes listed is not an error");
}

// ---- 5. the reader, directly ---------------------------------------------------------------------
ok(readTheme({}).colours && Object.keys(readTheme({}).colours).length === 0,
   "an empty theme is allowed: it simply overrides nothing");
ok(readTheme(null).why && readTheme([]).why, "a non-object is refused");
ok(parseColour("#8AC926") === 0x8ac926, "#RRGGBB is case-insensitive");
ok(parseColour("8ac926") === null && parseColour("#8ac9") === null && parseColour(0x1000000) === null,
   "a colour missing its #, too short, or over 24 bits is refused");

console.log(failed ? `\n${failed} FAILED` : "\nall theme checks passed");
process.exit(failed ? 1 : 0);
