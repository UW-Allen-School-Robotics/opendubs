// The offline render (engine/render.js): the same show, seed and times draw the same frames.
//
// Headless, so it runs in CI with no browser: the context RECORDS every drawing call and property it
// is given, and two renders are compared call for call. Identical calls into the same canvas are
// identical pixels; test_render_browser.mjs checks real pixels where a Chrome is available.
//
//   node software/face-web/tests/test_render.mjs

const { createRenderer } = await import("../engine/render.js");

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A 2D context that writes down everything done to it. Numbers are rounded to 1e-6 so the log
// compares what was drawn, not how a float was printed.
function recorder(w, h) {
  const log = [];
  const fmt = (v) => typeof v === "number" ? +v.toFixed(6) : (v && typeof v === "object" ? "[obj]" : v);
  const grad = () => ({ addColorStop: (...a) => log.push(["stop", ...a.map(fmt)]) });
  const fixed = {
    canvas: { width: w, height: h },
    measureText: (t) => ({ width: String(t).length * 10 }),
    createLinearGradient: (...a) => { log.push(["linGrad", ...a.map(fmt)]); return grad(); },
    createRadialGradient: (...a) => { log.push(["radGrad", ...a.map(fmt)]); return grad(); },
    getImageData: () => ({ data: new Uint8ClampedArray(w * h * 4) }),
  };
  const ctx = new Proxy({}, {
    get(_, k) {
      if (k in fixed) return fixed[k];
      if (typeof k === "symbol") return undefined;
      return (...a) => { log.push([k, ...a.map(fmt)]); };
    },
    set(_, k, v) { log.push(["=" + String(k), fmt(v)]); return true; },
  });
  return { ctx, log };
}

// A show's worth of lines, with times, and frames at 30 fps between them.
const SCRIPT = [
  [0, "SKIN ring"], [400, "EMOTE HAPPY"], [900, "MOVE wink_l"], [1500, "BRIGHT 0.4 1"],
  [2200, "ARMED WAIT"], [2600, "SKIN default"], [3000, "EMOTE AUTO"], [3400, "BRIGHT IDLE 0.5"],
];
async function render(seed, { stallAt = -1 } = {}) {
  const { ctx, log } = recorder(320, 180);
  const r = await createRenderer({ ctx, w: 320, h: 180, seed, drawArmed: null });
  const replies = [];
  let i = 0;
  for (let t = 0; t <= 4000; t += 1000 / 30) {
    while (i < SCRIPT.length && SCRIPT[i][0] <= t) { replies.push(await r.apply(SCRIPT[i][1], SCRIPT[i][0])); i++; }
    if (Math.abs(t - stallAt) < 20) await sleep(250);     // the wall clock moves; the show's must not care
    r.step(t);
  }
  return { log, replies, r };
}

const a = await render(7);
const b = await render(7, { stallAt: 1600 });
check("the same seed and times draw the same frames, call for call",
      a.log.length > 1000 && JSON.stringify(a.log) === JSON.stringify(b.log), `${a.log.length} vs ${b.log.length}`);
check("and give the same replies", JSON.stringify(a.replies) === JSON.stringify(b.replies), JSON.stringify(b.replies));
check("the replies are the robot's", a.replies[0] === "OK skin ring" && a.replies[3] === "OK bright 0.40", JSON.stringify(a.replies));
// A different seed must change something, or the seed is not reaching the engine: over 4 s the idle
// blinks and glances land at different times.
const c = await render(8);
check("a different seed draws something different", JSON.stringify(a.log) !== JSON.stringify(c.log), "identical");

// BRIGHT fades on the show's clock: from 1 to 0.4 over 1 s starting at 1500 ms.
{
  const { ctx } = recorder(64, 36);
  const r = await createRenderer({ ctx, w: 64, h: 36, seed: 1, drawArmed: null });
  await r.apply("BRIGHT 0.4 1", 1500);
  r.step(1500);
  await sleep(300);                                        // wall time passes; show time does not
  check("no fade without show time", Math.abs(r.levels.level.bright - 1) < 1e-9, r.levels.level.bright);
  r.step(2000);
  check("half way at half the fade", Math.abs(r.levels.level.bright - 0.7) < 1e-9, r.levels.level.bright);
  r.step(2600);
  check("and on the target after it", Math.abs(r.levels.level.bright - 0.4) < 1e-9, r.levels.level.bright);
}
// The dimming is in the picture: a black fill over the whole frame at 1 - bright.
{
  const { ctx, log } = recorder(64, 36);
  const r = await createRenderer({ ctx, w: 64, h: 36, seed: 1, drawArmed: null });
  await r.apply("BRIGHT 0.25", 0);
  r.step(0);
  const k = log.findLastIndex((e) => e[0] === "fillRect");
  check("BRIGHT 0.25 is drawn as black at 0.75 over everything",
        log[k - 1][0] === "=fillStyle" && log[k - 1][1] === "rgba(0,0,0,0.7500)" && log[k].join() === "fillRect,0,0,64,36",
        JSON.stringify(log.slice(k - 1, k + 1)));
}
// ARMED's lease follows the show's clock too: renewed, it stays; left alone, it lapses.
{
  const { ctx } = recorder(64, 36);
  const r = await createRenderer({ ctx, w: 64, h: 36, seed: 1, drawArmed: null });
  if (typeof r.face.setArmed === "function") {
    await r.apply("ARMED WAIT", 0); r.step(0);
    check("ARMED WAIT shows", r.face.armedState(0) === "wait", r.face.armedState(0));
    r.step(2500);
    check("  inside its lease", r.face.armedState(2500) === "wait", r.face.armedState(2500));
    r.step(4000);
    check("  and lapses at show time, not wall time", r.face.armedState(4000) === null, r.face.armedState(4000));
  }
}
// A SKIN line is answered once the skin can draw (an asset loads after it is chosen).
{
  const { ctx } = recorder(64, 36);
  const r = await createRenderer({ ctx, w: 64, h: 36, seed: 1, drawArmed: null });
  let waited = 0;
  r.skins.ready = async () => { await sleep(50); waited++; return true; };
  await r.apply("SKIN ring", 0);
  await r.apply("EMOTE HAPPY", 10);
  check("apply waits for a chosen skin to be ready, and only for SKIN", waited === 1, waited);
  r.skins.ready = async () => { r.skins.fallBack("ring", "image did not load"); return false; };
  const reply = await r.apply("SKIN ring", 20);
  check("a skin that fails to load is an ERR, not a silent default", reply === "ERR skin ring: image did not load (showing default)", reply);
}
// Time only runs forward, and says so.
{
  const { ctx } = recorder(64, 36);
  const r = await createRenderer({ ctx, w: 64, h: 36, seed: 1, drawArmed: null });
  r.step(1000);
  let err = null;
  try { r.step(500); } catch (e) { err = e; }
  check("a step back in time is refused", err instanceof RangeError, err);
  check("a frame is w*h*4 bytes, base64", Buffer.from(r.frame().rgba, "base64").length === 64 * 36 * 4, "");
}

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
