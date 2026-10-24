// A CODE SKIN LOADED FROM THE CONTENT FOLDER, rather than compiled into this repository.
//
// This is the whole point of the content split: a skin lives in the private content repository,
// the dashboard serves it, and the face imports it at run time. Nothing in THIS repository's CI
// will ever see those modules — so the checks here are about the engine's half of the contract,
// which is the only half we can hold.
//
// Why it earns its place: every failure below produces A FACE THAT LOOKS FINE and is not what was
// asked for. A module that imports but exports no `draw`, a file that is not there, a skin that
// arrives after the show moved on — each ends with the default eyes and no symptom at the robot
// beyond "my skin didn't come up". The alternative to this test is somebody at the robot guessing
// which of those happened, which is exactly what a person cannot do by looking.
//
// The fixture is a real folder with a real module, imported through the real path: `fetch` is
// stubbed for the index (Node's fetch does not do file://), but the dynamic `import()` is genuine,
// so the import, the validator and the late-arrival handling are all exercised rather than mocked.
//
// Run: node software/face-web/tests/test_content_skins.mjs   (conformance.sh runs it)

import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { readFileSync } from "node:fs";

const here = dirname(fileURLToPath(import.meta.url));
const CONTENT = pathToFileURL(join(here, "fixtures", "content") + "/");

// The index comes over HTTP on the robot; here it is read from disk. Only the index: the modules
// themselves are imported for real.
globalThis.fetch = async (url) => {
  const u = String(url);
  if (!u.endsWith("index.json")) throw new Error(`unexpected fetch ${u}`);
  const body = readFileSync(fileURLToPath(u), "utf8");
  return { json: async () => JSON.parse(body) };
};
globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };
globalThis.Image = class { set src(_) {} };

const { Skins, CODE_SKINS } = await import(join(here, "../engine/skins/index.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}
function recorder() {
  const log = [];
  const ctx = { fillRect: (...a) => log.push("fillRect(" + a.join(",") + ")"),
                beginPath() {}, fill() {}, stroke() {}, save() {}, restore() {}, arc() {},
                roundRect() {}, rect() {}, moveTo() {}, lineTo() {}, quadraticCurveTo() {},
                clip() {}, fillText() {}, createLinearGradient: () => ({ addColorStop() {} }),
                createRadialGradient: () => ({ addColorStop() {} }), log };
  for (const k of ["fillStyle", "strokeStyle", "lineWidth", "lineCap", "font", "textAlign"])
    Object.defineProperty(ctx, k, { set() {} });
  return ctx;
}
const EYE = { side: "left", cx: 320, cy: 360, w: 480, h: 480, radius: 98, lid: 0, lidLower: 0,
              lidCurve: 0, browRaise: 0, browAngle: 0, gazeX: 0, gazeY: 0, sparkle: 0,
              mouth: null, colour: "rgb(46,204,88)", dim: "rgb(16,71,30)", rgb: [46, 204, 88] };

const skins = new Skins();
await skins.loadLibrary(CONTENT);

// ---- 1. discovered, without being named anywhere in this repository ------------------------------
ok(skins.names().includes("fixture_good"), "a content code skin is discovered from the index");
ok(!("fixture_good" in CODE_SKINS), "and it is NOT a built-in: nothing here names it");

// ---- 2. it loads and it draws --------------------------------------------------------------------
{
  const r = skins.select("fixture_good");
  ok(r.ok, "selecting it is accepted at once, without waiting for the import", JSON.stringify(r));
  // Accepted immediately and drawn by the default eyes meanwhile — the same behaviour an undecoded
  // image already has. The protocol's reply cannot wait for a fetch.
  ok(skins.current === CODE_SKINS.default, "and the default eyes stand in while it imports");

  ok(await skins.skinsReady() === true, "skinsReady waits for the import and reports success");
  const ctx = recorder();
  skins.current.draw(ctx, EYE, 0);
  ok(ctx.log.some((l) => l.startsWith("fillRect(315,355,10,10")),
     "the CONTENT module is what draws, not the default eyes", ctx.log[0] || "(nothing drawn)");
  ok(skins.shownName() === "fixture_good", "and it reports its own name", skins.shownName());
}

// ---- 3. the failures that would otherwise look like nothing ---------------------------------------
{
  const r = skins.select("fixture_nodraw");
  ok(r.ok, "a module that imports is accepted at first — nothing is known yet");
  ok(await skins.skinsReady() === false, "but skinsReady reports it never became usable");
  ok(skins.current === CODE_SKINS.default, "the face falls back to the default eyes");
  ok(skins.error && skins.error.includes("no draw"), "and says WHY, rather than silently showing eyes",
     skins.error);
}
{
  skins.select("fixture_missing");
  ok(await skins.skinsReady() === false, "a file that is not there resolves false");
  ok(skins.error && skins.error.includes("cannot load"), "and names the file", skins.error);
}

// ---- 4. a late arrival must not hijack a show that moved on ---------------------------------------
// The import is asynchronous, so a skin selected and then abandoned can finish AFTER the next one
// is up. Whatever is chosen when it lands decides whether it shows.
{
  const fresh = new Skins();
  await fresh.loadLibrary(CONTENT);
  fresh.select("fixture_good");     // starts importing
  fresh.select("ring");             // the show moves on before it lands
  await new Promise((r) => setTimeout(r, 50));
  ok(fresh.shownName() === "ring", "a module finishing late does not replace the current skin",
     fresh.shownName());
}

// ---- 5. a built-in is unaffected -------------------------------------------------------------------
{
  const r = skins.select("fireworks");
  ok(r.ok && skins.current === CODE_SKINS.fireworks, "built-ins still select instantly and directly");
  ok(await skins.skinsReady() === true, "and are ready with nothing to load");
}

console.log(failed ? `\n${failed} FAILED` : "\nall content-skin checks passed");
process.exit(failed ? 1 : 0);
