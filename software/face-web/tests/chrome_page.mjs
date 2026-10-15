// A headless Chrome for the page tests: the face-web folder over HTTP on a free port, a muted
// browser with its own profile and DevTools port, and a way to open a page and evaluate in it.
// Nothing is left running. Used by test_render_browser.mjs and test_face_page_browser.mjs.
//
//   const c = await startChrome({ args: [...] });   // null when no Chrome or no WebSocket: SKIP
//   const p = await c.open("/index.html?...");      // {evaluate(expr), events, close()}
//   await c.stop();

import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, extname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  for (const p of ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium"]) if (existsSync(p)) return p;
  for (const n of ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]) {
    try { return execFileSync("which", [n], { encoding: "utf8" }).trim(); } catch { /* next */ }
  }
  return null;
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
                ".png": "image/png", ".svg": "image/svg+xml", ".css": "text/css", ".wasm": "application/wasm" };

/** Resolves to null (with the reason in `startChrome.why`) when the page tests cannot run here. */
export async function startChrome({ args = [] } = {}) {
  const chrome = findChrome();
  if (!chrome || typeof WebSocket === "undefined") {
    startChrome.why = chrome ? "no WebSocket in this Node" : "no Chrome";
    return null;
  }
  // The face-web folder over HTTP (module scripts will not load from file://).
  const server = createServer(async (req, res) => {
    const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^(\.\.[/\\])+/, "");
    try {
      const body = await readFile(join(ROOT, path.endsWith("/") ? path + "index.html" : path));
      res.writeHead(200, { "content-type": TYPES[extname(path)] || "application/octet-stream" }); res.end(body);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;

  const profile = await mkdtemp(join(tmpdir(), "face-page-"));
  const proc = spawn(chrome, ["--headless=new", "--mute-audio", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                              "--remote-debugging-port=0", `--user-data-dir=${profile}`,
                              ...(process.platform === "linux" ? ["--no-sandbox"] : []), ...args, "about:blank"],
                     { stdio: "ignore" });
  const stop = async () => {
    try { ws && ws.close(); } catch { /* closing */ }
    proc.kill("SIGKILL"); server.close();
    await sleep(200); await rm(profile, { recursive: true, force: true }).catch(() => {});
  };
  let ws = null;
  try {
    let port = null;
    for (let i = 0; i < 300 && !port; i++) {           // 30 s: a CI runner can be slow to start it
      await sleep(100);
      try { port = readFileSync(join(profile, "DevToolsActivePort"), "utf8").split("\n"); } catch { /* not yet */ }
    }
    if (!port) throw new Error("Chrome did not start");
    ws = new WebSocket(`ws://127.0.0.1:${port[0]}${port[1]}`);
    await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  } catch (e) { await stop(); throw e; }

  let id = 0; const waiting = new Map(), listeners = new Set();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
    else for (const l of listeners) l(msg);
  };
  const send = (method, params = {}, sessionId) => new Promise((r) => {
    const n = ++id; waiting.set(n, r); ws.send(JSON.stringify({ id: n, method, params, ...(sessionId ? { sessionId } : {}) }));
  });

  async function open(path) {
    const { result: { targetId } } = await send("Target.createTarget", { url: base + path });
    const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
    const events = [];
    const listen = (msg) => { if (msg.sessionId === sessionId) events.push(msg); };
    listeners.add(listen);
    await send("Runtime.enable", {}, sessionId);
    return {
      events,
      exceptions: () => events.filter((e) => e.method === "Runtime.exceptionThrown")
                              .map((e) => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text),
      async evaluate(expression) {
        const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId);
        if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || JSON.stringify(r.result.exceptionDetails));
        return r.result.result.value;
      },
      send: (method, params = {}) => send(method, params, sessionId),
      async close() { listeners.delete(listen); await send("Target.closeTarget", { targetId }); },
    };
  }
  return { base, open, stop };
}
