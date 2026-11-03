// THEMES FROM THE CONTENT FOLDER — a theme is DATA, where a skin is code.
//
// A theme is a handful of per-state colours, served by the dashboard at
// `/face/content/themes/<id>.json` and listed by its `/api/head/looks`. It draws nothing and runs
// nothing, so unlike a
// code skin it needs no module, no import and no drawing contract — what it needs is a check
// that it is not recolouring something it has no business recolouring, which is most of this file.
//
// EVERY THEME IS FETCHED WHEN THE LIST ARRIVES, not when one is chosen, and that is deliberate.
// A theme is a few hundred bytes, and fetching them all up front is what lets `THEME <id>` answer
// truthfully: the protocol's reply says what was ACCEPTED, and a reply cannot be held open for a
// fetch. A code skin has to tolerate arriving late because a module is big and the default eyes
// can stand in while it loads; a theme has no such stand-in — the face would come up in plain
// colours and then snap. So themes are either here or refused, and never in between.
//
// WHY A THEME IS REFUSED WHOLE, rather than having the offending colour dropped: the dashboard's
// discovery refuses it whole and reports it in `problems`, and if this side quietly stripped the
// key instead, the two would disagree about what exists — the picker offering a theme the face is
// showing only part of. Agreeing about what was refused matters more than salvaging three good
// colours out of four.

import { STATES, THEMES } from "./vocab.js";

const STATE_NAMES = new Set(STATES.map((s) => s[0]));

// THE STATES NO THEME MAY RECOLOUR, and the two different reasons they are here.
//
// The reserved ones (DEGRADED, E_STOP) are read from STATES rather than listed, so a reserved
// state added later is covered without anybody remembering this file. `stateColour` already
// ignores a theme for these — refusing them here as well is what makes the refusal VISIBLE, since
// a theme silently having no effect is the thing nobody can debug.
//
// CHARGING IS HERE FOR A DIFFERENT REASON and cannot be solved the same way. It is deliberately
// NOT `reserved`: a reserved state replaces the skin with the default eyes and suppresses the
// charge gauge, which would take the charging face apart (see vocab.js). So nothing downstream
// stops a theme recolouring it, and this is the only guard. The rule it protects: charging is
// purple in every theme, which a person learns once, and a theme that made it green would unteach
// it. The dashboard refuses such a theme first and reports it where somebody can see it; this is
// the backstop that stops a wrong looks payload fooling the face.
const UNTHEMEABLE = new Set([...STATES.filter((s) => s[4]).map((s) => s[0]), "CHARGING"]);

// `"#8ac926"` AND NOTHING ELSE — not a 24-bit integer, though the engine uses one internally, and
// not a CSS colour name. The dashboard's discovery accepts only this form, so a file written any
// other way is refused there and never listed; accepting more here would mean the two checks
// disagree about what a theme is, and the looser one would never be reached. `"lime"` and
// `"8ac926"` are mistakes, and in a repository this CI never builds a value quietly read as 0
// would surface only as a black face on the robot.
export function parseColour(v) {
  if (typeof v === "string" && /^#[0-9a-fA-F]{6}$/.test(v)) return parseInt(v.slice(1), 16);
  return null;
}

/**
 * Read one theme file: `{colours}` if it may be used, `{why}` if it may not.
 *
 * The file is `{"label": "Halloween", "colors": {"INIT": "#e8e2d0", ...}}`. Anything else at the
 * top level is ignored — the label is shown from the looks row, not from here — but `colors` must
 * be there, because a file without it is a theme that would do nothing and say nothing.
 *
 * `colors` IS SPELLED THAT WAY ON PURPOSE and is not a typo to tidy: the file format is shared
 * with the dashboard and the content repository, and this engine says `colours` everywhere else.
 * The seam is here, in this one line, which is the cheapest place for it to be.
 *
 * Deliberately strict about what it accepts. A theme comes from a repository nothing here builds,
 * so a typo in a state name has nothing else to catch it, and silently ignoring an unknown key
 * would make `"SAFEIDLE"` look like a theme that simply does nothing.
 */
export function readTheme(doc) {
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return { why: "not an object" };
  const given = doc.colors;
  if (!given || typeof given !== "object" || Array.isArray(given)) return { why: "no colors" };
  const colours = {};
  for (const [name, value] of Object.entries(given)) {
    if (!STATE_NAMES.has(name)) return { why: `${name} is not a state` };
    if (UNTHEMEABLE.has(name)) return { why: `${name} may not be recoloured` };
    const c = parseColour(value);
    if (c === null) return { why: `${name}'s colour is not "#rrggbb"` };
    colours[name] = c;
  }
  return { colours };
}

/**
 * Fetch and check every content theme the looks document lists.
 *
 * -> `{themes: Map<id, colours>, problems: [...]}`. Ids are lowercased, because `THEME HALLOWEEN`
 * has always worked and moving the theme out of the repository must not quietly make the protocol
 * case-sensitive.
 *
 * Failing is never fatal. A robot with no content folder, or a dashboard too old to list themes,
 * keeps every built-in theme and simply has no others — the face must come up.
 */
export async function loadThemes(doc, themesBase) {
  const themes = new Map(), problems = [];
  const listed = (doc && doc.themes) || [];
  await Promise.all(listed.map(async (t) => {
    if (!t || typeof t.id !== "string" || t.source === "builtin") return;
    const id = t.id.toLowerCase();
    // A CONTENT THEME MAY NOT TAKE A BUILT-IN'S NAME, for the same reason a content skin may not:
    // silently preferring one or the other gives a show a face nobody can account for.
    if (id in THEMES) { problems.push(`${id}: shadows a built-in theme`); return; }
    let doc2 = null;
    try {
      const r = await fetch(new URL(`${id}.json`, themesBase).href, { cache: "no-store" });
      if (r && r.ok === false) throw new Error(String(r.status));
      doc2 = await r.json();
    } catch {
      problems.push(`${id}: cannot load ${id}.json`);
      return;
    }
    const { colours, why } = readTheme(doc2);
    if (why) { problems.push(`${id}: ${why}`); return; }
    themes.set(id, colours);
  }));
  problems.sort();
  return { themes, problems };
}
