// The camera indicator (engine/frame.js): the user's condition for using the tablet's cameras is that
// the face SAYS so whenever one is open, for tracking or a stream, and never when none is. Headless:
// a recording context and a stub face, so it runs in CI.
//
//   node software/face-web/tests/test_camera_indicator.mjs

const { drawFace } = await import("../engine/frame.js");

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };

function recorder() {
  const log = [];
  const ctx = new Proxy({}, {
    get(_, k) {
      if (k === "measureText") return () => ({ width: 10 });
      if (typeof k === "symbol") return undefined;
      return (...a) => { log.push([k, ...a]); };
    },
    set(_, k, v) { log.push(["=" + String(k), v]); return true; },
  });
  return { ctx, log };
}
const RED = "#ff3b30";
function draw(o = {}, { reserved = false, armed = null } = {}) {
  const { ctx, log } = recorder();
  const face = { reserved: () => reserved, eyes: () => [], armedState: () => armed, quoteText: () => null };
  const skins = { current: { draw: () => true }, library: new Map(), chosen: "default" };
  const drawArmed = (c, a) => { if (a) log.push(["ARMED"]); };
  drawFace(ctx, 1280, 720, 0, { face, skins, drawArmed, ...o });
  return log;
}
const reds = (log) => log.filter((e) => e[0] === "=fillStyle" && e[1] === RED).length;

check("no cameras open: no indicator", reds(draw()) === 0 && reds(draw({ cams: { screen: false, back: false } })) === 0, "");
check("the screen-side camera open: one dot", reds(draw({ cams: { screen: true, back: false } })) === 1, reds(draw({ cams: { screen: true } })));
check("the back-side camera open: one dot", reds(draw({ cams: { screen: false, back: true } })) === 1, "");
check("both open: two dots", reds(draw({ cams: { screen: true, back: true } })) === 2, "");
{
  const log = draw({ cams: { screen: true } }, { armed: "wait" });
  const a = log.findIndex((e) => e[0] === "ARMED"), r = log.findIndex((e) => e[0] === "=fillStyle" && e[1] === RED);
  check("drawn AFTER the ARMED sign, so nothing covers it", a >= 0 && r > a, `armed ${a}, dot ${r}`);
}
check("drawn in a reserved state (E_STOP) too", reds(draw({ cams: { screen: true } }, { reserved: true })) === 1, "");
{
  const log = draw({ cams: { back: true }, bright: 0.3 });
  const r = log.findIndex((e) => e[0] === "=fillStyle" && e[1] === RED);
  const dim = log.findIndex((e) => e[0] === "=fillStyle" && String(e[1]).startsWith("rgba(0,0,0"));
  check("drawn before BRIGHT's dimming (dimmed with the face, as the backlight would)", r >= 0 && dim > r, `dot ${r}, dim ${dim}`);
}
{
  const seen = [];
  const log = draw({ cams: { screen: true, back: true }, drawCamera: (c, cams, t) => seen.push(cams) });
  check("a skin's own indicator replaces the plain one", seen.length === 1 && reds(log) === 0, `${seen.length} calls, ${reds(log)} plain dots`);
  const none = [];
  draw({ cams: { screen: false, back: false }, drawCamera: () => none.push(1) });
  check("  and is not called when no camera is open", none.length === 0, none.length);
}

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
