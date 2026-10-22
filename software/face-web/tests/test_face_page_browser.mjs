// The live face page in a real browser, for what only the page does:
//   - the camera sign follows a camera opening and closing (Chrome's fake camera stands in; in
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
// Whether the frame drew the camera sign (frame.js reports it; main.js keeps it in faceStats). What
// it draws is test_camera_indicator.mjs's business; here, that a real camera turns it on and off.
const DOT = "window.faceStats.cameraSign === true";
// Wait for a condition in the page, up to 3 s: the camera poll and the frame loop each take a moment.
const until = async (p, expr) => {
  for (let i = 0; i < 30; i++) { if (await p.evaluate(expr)) return true; await sleep(100); }
  return false;
};
try {
  const p = await c.open("/index.html?state=SAFE_IDLE");
  await sleep(1500);
  check("the page runs without an exception", p.exceptions().length === 0, JSON.stringify(p.exceptions()));
  check("no camera open: no indicator", (await p.evaluate("JSON.stringify(window.faceCameras)")) === '{"screen":false,"back":false}' &&
        !(await p.evaluate(DOT)), await p.evaluate("JSON.stringify(window.faceCameras)"));
  await p.evaluate("navigator.mediaDevices.getUserMedia({video: true}).then((s) => { window.__cam = s; return true; })");
  check("a camera opens: the face says so", await until(p, `window.faceCameras.screen === true && ${DOT}`),
        await p.evaluate("JSON.stringify(window.faceCameras)"));
  await p.evaluate("window.__cam.getTracks().forEach((t) => t.stop()); true");
  check("it closes: the indicator goes", await until(p, `window.faceCameras.screen === false && !(${DOT})`),
        await p.evaluate("JSON.stringify(window.faceCameras)"));

  const press = (ms) => p.evaluate(`(async () => {
    dispatchEvent(new PointerEvent('pointerdown', { clientX: 200, clientY: 200, isPrimary: true }));
    const ring = document.getElementById('touch').classList.contains('down');
    await new Promise((r) => setTimeout(r, ${ms}));
    dispatchEvent(new PointerEvent('pointerup', { clientX: 200, clientY: 200 }));
    return { ring, hint: document.getElementById('hint').classList.contains('show'), op: document.getElementById('op').classList.contains('open') };
  })()`);
  const tap = await press(120);
  check("a touch answers at once (the ring)", tap.ring, JSON.stringify(tap));
  check("a short tap shows the hint, and opens nothing", tap.hint && !tap.op, JSON.stringify(tap));
  await sleep(3400);
  check("  which goes by itself", (await p.evaluate("document.getElementById('hint').classList.contains('show')")) === false, "still showing");
  const hold = await press(1200);
  check("a one-second hold opens the controls, without the hint", hold.op && !hold.hint, JSON.stringify(hold));
  await p.close();
} catch (e) {
  console.log(`FAIL ${e.message}`); fail++;
}
console.log(`\n${pass} PASS, ${fail} FAIL`);
await c.stop();
process.exit(fail ? 1 : 0);
