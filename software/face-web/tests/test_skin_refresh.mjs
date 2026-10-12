// SKIN with a name added to the library after the page loaded: the face looks once more before
// falling back (protocol.js libraryRefresher). Headless: fetch and Image are stubbed.
//
//   node software/face-web/tests/test_skin_refresh.mjs

import { handle, libraryRefresher } from "../engine/protocol.js";
import { Skins } from "../engine/skins/index.js";

globalThis.Image = class { set src(_) {} addEventListener() {} decode() { return Promise.resolve(); } };
let index = [], delayMs = 0, fetches = 0;
globalThis.fetch = async () => {
  fetches++;
  const body = JSON.parse(JSON.stringify(index));
  if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
  return { json: async () => body };
};

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const cookie = { id: "cookie", kind: "skin", type: "image", file: "skins/cookie.png", params: {} };
const timed = async (p) => { const t = performance.now(); const r = await p; return [r, performance.now() - t]; };

const base = new URL("http://robot.invalid/library/");
const skins = new Skins();
await skins.loadLibrary(base);                          // the page loads: the library is empty
skins.refresh = libraryRefresher(skins, base, { gapMs: 2000, capMs: 250 });

index = [cookie];                                       // then the show adds a skin
let [r] = await timed(Promise.resolve(handle({}, {}, "SKIN cookie", 0, skins)));
check("a skin added after the page loaded is found on the second look", r === "OK skin cookie", r);
check("and it is what shows", skins.shownName() !== "default", skins.shownName());

const before = fetches;
let ms;
[r, ms] = await timed(Promise.resolve(handle({}, {}, "SKIN no_such_skin", 0, skins)));
check("an unknown name inside the rate limit answers as before",
      r === "ERR unknown skin no_such_skin (showing default)", r);
check("without another fetch", fetches === before, fetches - before);
check("and at once", ms < 50, `${ms.toFixed(0)} ms`);

const fresh = new Skins();
await fresh.loadLibrary(base);
fresh.library.clear();                                  // this page has not seen cookie
fresh.refresh = libraryRefresher(fresh, base, { gapMs: 0, capMs: 250 });
delayMs = 1000;                                         // a library read that runs long
[r, ms] = await timed(Promise.resolve(handle({}, {}, "SKIN cookie", 0, fresh)));
check("a slow re-read is cut off, so the reply beats the door's 0.4 s", ms < 350, `${ms.toFixed(0)} ms`);
check("and falls back as before", r === "ERR unknown skin cookie (showing default)", r);
await new Promise((res) => setTimeout(res, 900));       // the late read lands anyway
delayMs = 0;
r = await Promise.resolve(handle({}, {}, "SKIN cookie", 0, fresh));
check("the next SKIN finds what the late read brought", r === "OK skin cookie", r);

const plain = new Skins();                              // a door with no refresher (node_door.mjs)
r = handle({}, {}, "SKIN nope", 0, plain);
check("with no refresher the reply is synchronous, as before",
      r === "ERR unknown skin nope (showing default)", r);

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
