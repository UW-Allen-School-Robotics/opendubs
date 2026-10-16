// THE CAMERA-ON SIGN, held to the promise it was approved on.
//
// The maintainers allowed the tablet's cameras to be robot sensors on one condition: a visible
// indicator on the face whenever any camera is open. That makes this a consent signal, and the
// checks below are written against the two ways a consent signal fails people:
//
//   IT IS NOT THERE WHEN A CAMERA IS. The sign must be drawn for either camera, must be readable
//   over a full-face photo (so: a near-opaque backing, not a translucent one), and must never be
//   invisible at any phase of its pulse -- a glance at the trough of a pulse that faded to nothing
//   sees no indicator at all.
//
//   IT IS THERE WHEN NO CAMERA IS. A sign that cries wolf is one people learn to ignore, and it
//   costs the same thing a week later. So nothing is drawn for {false, false}, and nothing is
//   drawn for the malformed inputs a polling caller can hand it -- null, undefined, {}.
//
// Also the lesson the ARMED label cost us: everything drawn is checked to be INSIDE the design
// area and inside the sign's own declared box. #227 put a label on the frame stroke and 2 px past
// the canvas edge, and the tests of the day checked what it cleared but never where it WAS.
//
// Run: node software/face-web/tests/test_camera_sign.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const { drawCameraOn, CAMERA_SIGN_BOX, CAMERA_DOT_FLOOR } =
  await import(join(here, "../engine/skins/camera.js"));

const W = 1280, H = 720;
// The corridor the sign must stay inside: the left brow ends at x 537, the right starts at 743,
// and the eye bodies start at y 101 at the largest emote. These are the numbers the sign's comment
// claims, restated here so a change to either has to face them.
const BROW_L = 537, BROW_R = 743, EYE_TOP = 101;

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A context that records every call AND the points it was asked to paint, so the sign's extent can
// be measured rather than eyeballed.
function recorder() {
  const log = [], pts = [], alphas = [];
  const push = (x, y, pad = 0) => { pts.push([x - pad, y - pad], [x + pad, y + pad]); };
  const style = (v) => {
    const m = /rgba?\(([^)]*)\)/.exec(String(v));
    if (m) { const p = m[1].split(",").map((s) => +s); alphas.push(p.length > 3 ? p[3] : 1); }
    log.push("style=" + v);
  };
  let lw = 1;
  const ctx = {
    log, pts, alphas,
    save: () => log.push("save"), restore: () => log.push("restore"),
    beginPath: () => log.push("beginPath"), closePath: () => log.push("closePath"),
    fill: () => log.push("fill"), stroke: () => log.push("stroke"), clip: () => log.push("clip"),
    moveTo: (x, y) => { log.push("moveTo"); push(x, y); },
    lineTo: (x, y) => { log.push("lineTo"); push(x, y); },
    arc: (x, y, r) => { log.push(`arc(${x},${y},${r})`); push(x, y, r + lw); },
    ellipse: (x, y, rx, ry) => { log.push("ellipse"); push(x, y, Math.max(rx, ry) + lw); },
    rect: (x, y, w, h) => { log.push(`rect(${x},${y},${w},${h})`); push(x, y); push(x + w, y + h); },
    roundRect: (x, y, w, h) => { log.push(`roundRect(${x},${y},${w},${h})`); push(x, y); push(x + w, y + h); },
    fillRect: (x, y, w, h) => { log.push("fillRect"); push(x, y); push(x + w, y + h); },
    quadraticCurveTo: () => log.push("q"), bezierCurveTo: () => log.push("b"),
    translate: () => log.push("translate"), scale: () => log.push("scale"), rotate: () => log.push("rotate"),
    setTransform: () => log.push("setTransform"), clearRect: () => log.push("clearRect"),
    drawImage: () => log.push("drawImage"), measureText: () => ({ width: 10 }),
    // Text is recorded WITH ITS EXTENT, from the declared font size and the same 0.62 estimate the
    // sign lays out with: a label that overflows its pill is the exact bug #227 shipped.
    fillText(s, x, y) {
      log.push(`fillText(${s},${x},${y})`);
      const px = parseFloat(/(\d+(?:\.\d+)?)px/.exec(ctx._font || "16px")?.[1] || 16);
      const w = String(s).length * px * 0.62;
      const x0 = ctx._align === "center" ? x - w / 2 : ctx._align === "right" ? x - w : x;
      push(x0, y - px); push(x0 + w, y + px * 0.25);
    },
    createLinearGradient: () => ({ addColorStop() {} }),
    createRadialGradient: () => ({ addColorStop() {} }),
  };
  Object.defineProperty(ctx, "fillStyle", { set: style });
  Object.defineProperty(ctx, "strokeStyle", { set: style });
  Object.defineProperty(ctx, "lineWidth", { set(v) { lw = v; log.push("lw=" + v); } });
  Object.defineProperty(ctx, "font", { set(v) { ctx._font = v; log.push("font=" + v); } });
  Object.defineProperty(ctx, "textAlign", { set(v) { ctx._align = v; log.push("align=" + v); } });
  Object.defineProperty(ctx, "textBaseline", { set(v) { log.push("baseline=" + v); } });
  return ctx;
}

function draw(cams, t = 0) { const c = recorder(); drawCameraOn(c, cams, t); return c; }

const ON = [["the front camera", { screen: true, back: false }],
            ["the rear camera", { screen: false, back: true }],
            ["both cameras", { screen: true, back: true }]];

// ---- 1. nothing when no camera is open ----------------------------------------------------------
for (const [what, cams] of [["both shut", { screen: false, back: false }],
                            ["null", null], ["undefined", undefined], ["an empty object", {}],
                            ["a missing back key", { screen: false }]]) {
  ok(draw(cams).log.length === 0, `draws NOTHING for ${what}`, draw(cams).log.slice(0, 3).join(" "));
}

// ---- 2. something, for either camera ------------------------------------------------------------
for (const [what, cams] of ON) {
  const c = draw(cams);
  ok(c.log.length > 0, `draws the sign for ${what}`, `${c.log.length} calls`);
  ok(c.log.some((l) => l.startsWith("fillText(CAMERA ON")), `says CAMERA ON for ${what}`);
  ok(c.log.filter((l) => l === "save").length === c.log.filter((l) => l === "restore").length,
     `balances save/restore for ${what}`);
}

// Which camera, in words -- the operator's question, answered without crowding the other one.
ok(draw({ screen: true, back: false }).log.some((l) => l.startsWith("fillText(front,")), "names the front camera");
ok(draw({ screen: false, back: true }).log.some((l) => l.startsWith("fillText(rear,")), "names the rear camera");
ok(draw({ screen: true, back: true }).log.some((l) => l.startsWith("fillText(front + rear,")), "names both");

// ---- 3. it stays where it says it does ----------------------------------------------------------
// Inside the canvas, inside its own declared box, and inside the corridor between the brows -- so
// it can never be clipped, and can never sit under a brow or over an eye.
// t = 0 and t = 0.5 are the extremes of the halo's travel, which is the only thing in the sign
// whose geometry moves. The three further samples were the same arithmetic again.
for (const [what, cams] of ON) {
  for (const t of [0, 0.5]) {
    const { pts } = draw(cams, t);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    ok(x0 >= 0 && x1 <= W && y0 >= 0 && y1 <= H,
       `${what} @t=${t}: inside the 1280x720 design area`, `x ${x0.toFixed(0)}..${x1.toFixed(0)} y ${y0.toFixed(0)}..${y1.toFixed(0)}`);
    ok(x0 >= BROW_L && x1 <= BROW_R,
       `${what} @t=${t}: inside the corridor between the brows`, `${x0.toFixed(0)}..${x1.toFixed(0)} vs ${BROW_L}..${BROW_R}`);
    ok(y1 <= EYE_TOP, `${what} @t=${t}: clear of the eye bodies`, `bottom ${y1.toFixed(0)} vs ${EYE_TOP}`);
    // The ARMED frame's top stroke spans y 10..26; the sign must not sit on it.
    ok(y0 >= 26, `${what} @t=${t}: clear of the ARMED frame`, `top ${y0.toFixed(0)}`);
  }
}

// The declared box is the truth, not a guess: anyone laying something out near the sign uses it.
{
  const { pts } = draw({ screen: true, back: true }, 0.5);
  const b = CAMERA_SIGN_BOX;
  const inside = pts.every(([x, y]) => x >= b.x - 14 && x <= b.x + b.w + 14 && y >= b.y - 14 && y <= b.y + b.h + 14);
  ok(inside, "everything drawn is within the declared CAMERA_SIGN_BOX (plus the halo)",
     `${b.x},${b.y} ${b.w}x${b.h}`);
}

// ---- 4. it is readable over a full-face photo ---------------------------------------------------
// The sign is drawn over the finished frame, which may be a white picture. A translucent backing
// would vanish into it, so the pill is near-opaque -- checked, because this is the kind of value a
// later tidy-up lowers for looks.
{
  const c = draw({ screen: true, back: false });
  const pill = c.log.find((l) => l.startsWith("style=rgba(10"));
  ok(!!pill && parseFloat(pill.split(",")[3]) >= 0.85, "the pill is near-opaque, so a white photo cannot swallow it", pill);
}

// ---- 5. the pulse never goes out ----------------------------------------------------------------
// The whole cycle is sampled, not a couple of convenient moments: a dot that faded to nothing at
// the trough would be no indicator at all for part of every second.
{
  let lowest = 1, at = 0;
  for (let t = 0; t < 4; t += 1 / 240) {
    const c = draw({ screen: true, back: false }, t);
    // The dot's own alpha: the last green fill before the text.
    const greens = c.log.filter((l) => l.startsWith("style=rgba(34, 226, 102"));
    const a = Math.max(...greens.map((l) => parseFloat(l.split(",")[3])));
    if (a < lowest) { lowest = a; at = t; }
  }
  ok(lowest >= CAMERA_DOT_FLOOR - 1e-6,
     "the dot never dims below its floor, at any phase of the pulse",
     `lowest ${lowest.toFixed(3)} at t=${at.toFixed(3)} (floor ${CAMERA_DOT_FLOOR})`);
  // THE SECOND CHECK IS NOT REDUNDANT, though it looks it. The first compares against the sign's
  // own exported floor, so lowering that constant satisfies it -- the test would move with the bug.
  // This one is an absolute, and it is the line that actually caught DOT_FLOOR = 0 when it was
  // tried. Do not tidy it away.
  ok(lowest > 0.5, "and the floor itself is high enough to be seen", lowest.toFixed(3));
}

// ---- 6. it is alive, and it is a pure function --------------------------------------------------
{
  const a = JSON.stringify(draw({ screen: true, back: false }, 1.0).log);
  const b = JSON.stringify(draw({ screen: true, back: false }, 1.0).log);
  const c = JSON.stringify(draw({ screen: true, back: false }, 1.4).log);
  ok(a === b, "the same inputs draw the same thing (nothing accumulates between frames)");
  ok(a !== c, "the sign is visibly alive: it changes with t");
  const one = JSON.stringify(draw({ screen: true, back: false }, 1.0).log);
  const two = JSON.stringify(draw({ screen: true, back: true }, 1.0).log);
  ok(one !== two, "one camera and two cameras do not look the same");
}

console.log(failed ? `\n${failed} FAILED` : "\nall camera-sign checks passed");
process.exit(failed ? 1 : 0);
