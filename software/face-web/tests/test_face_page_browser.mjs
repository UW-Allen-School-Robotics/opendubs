// The live face page in a real browser, for what only the page does:
//   - the camera indicator follows a camera opening and closing (Chrome's fake camera stands in; in
//     the kiosk the app's cameras() reports the hardware as well),
//   - a short tap shows how to open the controls, and a long-press opens them.
// SKIP without Chrome (chrome_page.mjs). Muted and headless; nothing is left running.
//
//   node software/face-web/tests/test_face_page_browser.mjs

import { startChrome } from "./chrome_page.mjs";

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const c = await startChrome({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
if (!c) {
  console.log(`SKIP ${startChrome.why}: the face page check did not run`);
  process.exit(0);
}
// The indicator's place on the canvas (the dot at design 1240,40, fitted into the window), read as a pixel.
const DOT = `(() => {
  const cv = document.getElementById('face'), W = cv.width, H = cv.height, s = Math.min(W / 1280, H / 720);
  const x = Math.round((W - 1280 * s) / 2 + 1240 * s), y = Math.round((H - 720 * s) / 2 + 48 * s);   // inside the dot, below its letter
  const t = document.createElement('canvas'); t.width = W; t.height = H; const g = t.getContext('2d');
  g.drawImage(cv, 0, 0); const d = g.getImageData(x, y, 1, 1).data;
  return d[0] > 200 && d[1] < 120 && d[2] < 120; })()`;
try {
  const p = await c.open("/index.html?state=SAFE_IDLE");
  await sleep(1500);
  check("the page runs without an exception", p.exceptions().length === 0, JSON.stringify(p.exceptions()));
  check("no camera open: no indicator", (await p.evaluate("JSON.stringify(window.faceCameras)")) === '{"screen":false,"back":false}' &&
        !(await p.evaluate(DOT)), await p.evaluate("JSON.stringify(window.faceCameras)"));
  await p.evaluate("navigator.mediaDevices.getUserMedia({video: true}).then((s) => { window.__cam = s; return true; })");
  await sleep(800);
  check("a camera opens: the face says so", (await p.evaluate("window.faceCameras.screen")) === true && (await p.evaluate(DOT)) === true,
        await p.evaluate("JSON.stringify(window.faceCameras)"));
  await p.evaluate("window.__cam.getTracks().forEach((t) => t.stop()); true");
  await sleep(800);
  check("it closes: the indicator goes", (await p.evaluate("window.faceCameras.screen")) === false && (await p.evaluate(DOT)) === false,
        await p.evaluate("JSON.stringify(window.faceCameras)"));

  const press = (ms) => p.evaluate(`(async () => {
    dispatchEvent(new PointerEvent('pointerdown', { clientX: 200, clientY: 200 }));
    await new Promise((r) => setTimeout(r, ${ms}));
    dispatchEvent(new PointerEvent('pointerup', { clientX: 200, clientY: 200 }));
    return { hint: document.getElementById('hint').classList.contains('show'), op: document.getElementById('op').classList.contains('open') };
  })()`);
  const tap = await press(120);
  check("a short tap shows the hint, and opens nothing", tap.hint && !tap.op, JSON.stringify(tap));
  await sleep(3400);
  check("  which goes by itself", (await p.evaluate("document.getElementById('hint').classList.contains('show')")) === false, "still showing");
  const hold = await press(1700);
  check("a long-press opens the controls, without the hint", hold.op && !hold.hint, JSON.stringify(hold));
  await p.close();
} catch (e) {
  console.log(`FAIL ${e.message}`); fail++;
}
console.log(`\n${pass} PASS, ${fail} FAIL`);
await c.stop();
process.exit(fail ? 1 : 0);
