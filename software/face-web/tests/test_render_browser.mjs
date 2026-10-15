// The offline render in a real browser: `index.html?render=1` and window.OpenDubsRender, read back as
// PIXELS. Two pages rendering the same show with the same seed must give the same bytes.
//
// Needs Chrome or Chromium (CHROME=<path>, or found on the PATH / in /Applications). Without one it
// says SKIP and passes: test_render.mjs is the check that always runs. Muted, headless, its own
// profile, its own port; nothing is left running.
//
//   node software/face-web/tests/test_render_browser.mjs

import { startChrome } from "./chrome_page.mjs";

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };

const c = await startChrome();
if (!c) {
  console.log(`SKIP ${startChrome.why}: the real-pixel render check did not run`);
  process.exit(0);
}
try {
  async function renderIn(seed, script, times) {
    const page = await c.open(`/index.html?render=1&seed=${seed}&w=320&h=180`);
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
    try { return await page.evaluate(expr); } finally { await page.close(); }
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
await c.stop();
process.exit(fail ? 1 : 0);
