// The offline render in a real browser: `index.html?render=1` and window.OpenDubsRender, read back as
// PIXELS. Two pages rendering the same show with the same seed must give the same bytes.
//
// Needs Chrome or Chromium (CHROME=<path>, or found on the PATH / in /Applications). Without one it
// says SKIP and passes: test_render.mjs is the check that always runs. Muted, headless, its own
// profile, its own port; nothing is left running.
//
//   node software/face-web/tests/test_render_browser.mjs

import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, extname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  for (const p of ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium"]) if (existsSync(p)) return p;
  for (const n of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    try { return execFileSync("which", [n], { encoding: "utf8" }).trim(); } catch { /* next */ }
  }
  return null;
}
const chrome = findChrome();
if (!chrome || typeof WebSocket === "undefined") {
  console.log(`SKIP no ${chrome ? "WebSocket in this Node" : "Chrome"}: the real-pixel render check did not run`);
  process.exit(0);
}

// The face-web folder over HTTP (module scripts will not load from file://).
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
                ".png": "image/png", ".svg": "image/svg+xml", ".css": "text/css" };
const server = createServer(async (req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
  try {
    const body = await readFile(join(ROOT, path.endsWith("/") ? path + "index.html" : path));
    res.writeHead(200, { "content-type": TYPES[extname(path)] || "application/octet-stream" }); res.end(body);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

const profile = await mkdtemp(join(tmpdir(), "face-render-"));
const proc = spawn(chrome, ["--headless=new", "--mute-audio", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            "--remote-debugging-port=0", `--user-data-dir=${profile}`,
                            ...(process.platform === "linux" ? ["--no-sandbox"] : []), "about:blank"],
                   { stdio: "ignore" });
let ws;
async function done(code) {
  try { ws && ws.close(); } catch { /* closing */ }
  proc.kill("SIGKILL"); server.close();
  await sleep(200); await rm(profile, { recursive: true, force: true }).catch(() => {});
  process.exit(code);
}
try {
  let port = null;
  for (let i = 0; i < 100 && !port; i++) {
    await sleep(100);
    try { port = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n"); } catch { /* not yet */ }
  }
  if (!port) throw new Error("Chrome did not start");
  ws = new WebSocket(`ws://127.0.0.1:${port[0]}${port[1]}`);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const waiting = new Map();
  ws.onmessage = (m) => { const msg = JSON.parse(m.data); if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); } };
  const send = (method, params = {}, sessionId) => new Promise((r) => {
    const n = ++id; waiting.set(n, r); ws.send(JSON.stringify({ id: n, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  async function renderIn(seed, script, times) {
    const { result: { targetId } } = await send("Target.createTarget", { url: `${base}/index.html?render=1&seed=${seed}&w=320&h=180` });
    const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
    await send("Runtime.enable", {}, sessionId);
    const expr = `(async () => {
      for (let i = 0; i < 100 && !window.OpenDubsRender; i++) await new Promise((r) => setTimeout(r, 50));
      const R = window.OpenDubsRender; const info = await R.ready;
      const script = ${JSON.stringify(script)}, times = ${JSON.stringify(times)}, frames = [], replies = [];
      let i = 0;
      for (const t of times) {
        while (i < script.length && script[i][0] <= t) { replies.push(await R.apply(script[i][1], script[i][0])); i++; }
        R.step(t); frames.push(R.frame());
      }
      return { info, replies, frames };
    })()`;
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
    await send("Target.closeTarget", { targetId });
    if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails));
    return r.result.result.value;
  }

  const SCRIPT = [[0, "SKIN ring"], [500, "EMOTE HAPPY"], [1000, "MOVE wink_l"], [1600, "BRIGHT 0.25"]];
  const TIMES = [];
  for (let t = 0; t <= 2000; t += 1000 / 30) TIMES.push(t);
  const a = await renderIn(7, SCRIPT, TIMES);
  const b = await renderIn(7, SCRIPT, TIMES);
  check("the page is in render mode at the asked size", a.info.w === 320 && a.info.h === 180 && a.frames[0].w === 320, JSON.stringify(a.info));
  check("frames are w*h*4 bytes", Buffer.from(a.frames[0].rgba, "base64").length === 320 * 180 * 4, "");
  const same = a.frames.every((f, i) => f.rgba === b.frames[i].rgba);
  check(`two pages, same seed and times: the same pixels in all ${a.frames.length} frames`, same,
        a.frames.findIndex((f, i) => f.rgba !== b.frames[i].rgba));
  check("the same replies", JSON.stringify(a.replies) === JSON.stringify(b.replies), JSON.stringify(b.replies));
  const mean = (f) => { const d = Buffer.from(f.rgba, "base64"); let s = 0; for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2]; return s / (d.length / 4); };
  const lit = mean(a.frames[TIMES.findIndex((t) => t >= 1500)]), dim = mean(a.frames.at(-1));
  check("the face is drawn (not a black frame)", lit > 1, lit.toFixed(2));
  check("BRIGHT 0.25 dims the picture itself", dim < lit * 0.5, `${dim.toFixed(2)} vs ${lit.toFixed(2)}`);
} catch (e) {
  console.log(`FAIL ${e.message}`); fail++;
}
console.log(`\n${pass} PASS, ${fail} FAIL`);
await done(fail ? 1 : 0);
