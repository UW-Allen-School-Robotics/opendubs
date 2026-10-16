// THE CAPTION: the words that say what the robot is doing, for somebody who does not know the
// colour code.
//
// The checks follow from who it is FOR. It is the second channel beside the state colour, added
// for a person who cannot use the first one -- so the things that matter are that it is legible
// without reference to colour, that it never hides the things it must not, and that it cannot be
// made to overflow by text nobody here chooses (the dashboard writes the words, up to the
// protocol's 60 characters).
//
// Run: node software/face-web/tests/test_caption.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const { drawCaption, CAPTION_BAND } = await import(join(here, "../engine/skins/caption.js"));

const W = 1280;
let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// A context that measures like a real one: a per-character width proportional to the font size, so
// "does it shrink when the words get long" is actually exercised rather than stubbed away.
function recorder({ measure = true } = {}) {
  const out = { rects: [], texts: [], styles: [], fonts: [] };
  let px = 16, fillStyle = "";
  const ctx = {
    save() {}, restore() {}, beginPath() {}, fill() {}, stroke() {},
    roundRect: (x, y, w, h) => out.rects.push({ x, y, w, h, style: fillStyle }),
    rect: (x, y, w, h) => out.rects.push({ x, y, w, h, style: fillStyle }),
    fillText: (s, x, y) => out.texts.push({ s, x, y, px, style: fillStyle }),
    arc() {}, moveTo() {}, lineTo() {}, clip() {}, fillRect() {},
    createLinearGradient: () => ({ addColorStop() {} }),
  };
  if (measure) ctx.measureText = (s) => ({ width: s.length * px * 0.55 });
  Object.defineProperty(ctx, "font", {
    set(v) { out.fonts.push(v); const m = /(\d+(?:\.\d+)?)px/.exec(v); if (m) px = parseFloat(m[1]); },
    get() { return `${px}px`; },
  });
  Object.defineProperty(ctx, "fillStyle", { set(v) { fillStyle = String(v); out.styles.push(String(v)); } });
  Object.defineProperty(ctx, "strokeStyle", { set(v) { out.styles.push(String(v)); } });
  Object.defineProperty(ctx, "lineWidth", { set() {} });
  Object.defineProperty(ctx, "textAlign", { set() {} });
  Object.defineProperty(ctx, "textBaseline", { set() {} });
  return [ctx, out];
}
function draw(text, opts = {}, ctxOpts = {}) {
  const [ctx, out] = recorder(ctxOpts);
  drawCaption(ctx, text, opts);
  return out;
}

const PHRASES = ["Starting up", "Ready", "Manual control", "Running a show", "Charging 95%",
                 "Emergency stop", "Problem: RC transmitter not connected"];

// ---- 1. it draws the words it is given, and nothing when there are none -------------------------
// One short phrase and the longest the dashboard can generate. The others exercise the identical
// layout path with different letters, which is width without information.
for (const p of [PHRASES[1], PHRASES[6]]) {
  const o = draw(p);
  ok(o.texts.length === 1 && o.texts[0].s === p, `draws "${p}"`);
  ok(o.rects.length > 0, `and a panel behind it`, `${o.rects.length}`);
}
for (const [what, v] of [["null", null], ["undefined", undefined], ["an empty string", ""],
                         ["whitespace", "   "], ["a number", 42], ["an object", {}]]) {
  ok(draw(v).texts.length === 0 && draw(v).rects.length === 0, `draws NOTHING for ${what}`);
}

// ---- 2. the words never carry the state's colour ------------------------------------------------
// The point of the caption: it is for the person who cannot read the colour. Amber words on an
// amber face would fail for exactly that person -- two channels that fail together are one channel.
const STATE_HUES = ["243, 182, 74", "255, 77, 77", "46, 204, 88", "0, 176, 235", "109, 48, 224",
                    "#f3b64a", "#ff4d4d", "#2ecc58", "#00b0eb", "#6d30e0"];
for (const fault of [false, true]) {
  const o = draw("Problem: lift not responding", { fault });
  const ink = o.texts[0].style;
  ok(/^(#fff|#ffffff|rgba\(255, 255, 255)/i.test(ink), `the words are white (fault=${fault})`, ink);
  ok(!STATE_HUES.some((h) => o.styles.some((s) => s.includes(h))),
     `and no state colour is used anywhere (fault=${fault})`);
}

// ---- 3. it stays inside its band --------------------------------------------------------------
// Above: the eyes' lowest reach, 619 at the tallest emote. Below: the ARMED frame's bottom stroke
// at 694. Both are hard: a caption over the eyes defaces the robot, and one over the ARMED frame
// hides a safety sign.
const EYES_LOWEST = 619, ARMED_FRAME_TOP = 694;
// THE BAND ITSELF, asserted directly. The panel is bottom-aligned, so moving CAPTION_BAND.top
// does not move a normal caption at all -- the constant only bites when the font is large enough
// for the band to cap the panel's height. A mutation raising it to 560 therefore changed nothing
// visible and slipped past every drawing check. The declared band is the promise; check it.
ok(CAPTION_BAND.top >= EYES_LOWEST, "the declared band starts below the eyes' lowest reach",
   `${CAPTION_BAND.top} vs ${EYES_LOWEST}`);
ok(CAPTION_BAND.bottom <= ARMED_FRAME_TOP, "and ends above the ARMED frame",
   `${CAPTION_BAND.bottom} vs ${ARMED_FRAME_TOP}`);
// The shortest and the longest: the panel's geometry is one calculation, and only its WIDTH
// varies with the words, so the extremes bracket every phrase between them.
for (const p of [PHRASES[1], PHRASES[6]]) {
  for (const fault of [false, true]) {
    const r = draw(p, { fault }).rects[0];
    ok(r.y >= EYES_LOWEST, `"${p}" (fault=${fault}) clears the eyes`, `top ${r.y.toFixed(0)} vs ${EYES_LOWEST}`);
    ok(r.y + r.h <= ARMED_FRAME_TOP, `and the ARMED frame`, `bottom ${(r.y + r.h).toFixed(0)} vs ${ARMED_FRAME_TOP}`);
    ok(r.x >= 0 && r.x + r.w <= W, `and stays on screen`, `x ${r.x.toFixed(0)}..${(r.x + r.w).toFixed(0)}`);
    ok(r.y >= CAPTION_BAND.top && r.y + r.h <= CAPTION_BAND.bottom, `and inside its declared band`);
  }
}

// ---- 4. it cannot be made to overflow by the words it is given ----------------------------------
// The dashboard writes the text, up to the protocol's 60 characters. The caption does not get to
// choose its own length, so it has to survive the longest one allowed.
{
  const sixty = "x".repeat(60);
  const wide = "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW";
  for (const [what, s] of [["60 narrow characters", sixty], ["60 wide ones", wide]]) {
    for (const fault of [false, true]) {
      const o = draw(s, { fault });
      const r = o.rects[0];
      ok(r.x >= 0 && r.x + r.w <= W, `${what} (fault=${fault}) still fits the screen`,
         `${r.w.toFixed(0)} px wide`);
    }
  }
  // THE WORDS FIT INSIDE THEIR PANEL, not merely on the screen. The panel's width is clamped to
  // the design area, so a caption whose TEXT is too wide still produces a panel that passes every
  // bounds check while the words run out of both ends of it. That is what a too-narrow width
  // estimate does, and it slipped past until this check existed.
  for (const [what, opts] of [["measuring", {}], ["without measureText", { measure: false }]]) {
    for (const fault of [false, true]) {
      const o = draw(wide, { fault }, opts);
      const r = o.rects[0], t = o.texts[0];
      const trueWidth = wide.length * t.px * 0.55;        // the recorder's own model of the font
      ok(trueWidth <= r.w - 2 * 28 + 1,
         `${what} (fault=${fault}): the words fit inside the panel, not just on the screen`,
         `text ${trueWidth.toFixed(0)} vs panel ${r.w.toFixed(0)} - padding`);
    }
  }

  // AND IT SHRINKS TO DO IT, rather than merely being clipped by the canvas.
  const small = draw(sixty).texts[0].px, big = draw("Ready").texts[0].px;
  ok(small < big, "a long caption is drawn smaller than a short one", `${small} vs ${big} px`);
  ok(small >= 26, "but never below the floor, where it would stop being worth drawing", `${small} px`);

  // THE FLOOR IS UNREACHABLE WITHIN THE PROTOCOL'S 60 CHARACTERS -- the shrink loop settles well
  // above it for anything that long, which is why lowering the floor changed nothing and no
  // mutation of it was caught. It is a bound against text that should never arrive, so it is
  // tested with text that should never arrive: if the cap is ever raised, or something bypasses
  // it, the caption must still be drawn at a readable size rather than shrink to a thread.
  const absurd = "a necessarily very long sentence ".repeat(8);
  const o = draw(absurd);
  ok(o.texts[0].px >= 26, "absurdly long text stops shrinking at the floor",
     `${absurd.length} chars -> ${o.texts[0].px} px`);
  ok(o.rects[0].x >= 0 && o.rects[0].x + o.rects[0].w <= W,
     "and its panel is still on screen, even though the words will not fit it");
}

// ---- 5. a fault is louder than a status ---------------------------------------------------------
{
  const normal = draw("Ready").texts[0].px;
  const fault = draw("Ready", { fault: true }).texts[0].px;
  ok(fault > normal, "a fault caption is larger than an ordinary one", `${fault} vs ${normal} px`);
}

// ---- 6. the panel is opaque enough to carry the words over anything ------------------------------
// It is drawn over whatever the face is doing: amber eyes, a purple charging face, a full-face
// photo a show put up. A translucent panel would leave legibility to luck.
{
  const o = draw("Emergency stop", { fault: true });
  const panel = o.styles.find((s) => s.startsWith("rgba(8"));
  ok(!!panel && parseFloat(panel.split(",")[3]) >= 0.8, "the panel is near-opaque", panel);
}

// ---- 7. it works on a context that cannot measure -------------------------------------------------
// Headless, or a canvas without measureText: the fallback estimate must still keep it on screen,
// and must err toward shrinking rather than overflowing.
{
  const o = draw("x".repeat(60), {}, { measure: false });
  const r = o.rects[0];
  ok(o.texts.length === 1, "it still draws with no measureText available");
  ok(r.x >= 0 && r.x + r.w <= W, "and still fits", `${r.w.toFixed(0)} px wide`);
}

// ---- 8. the same words draw the same thing -------------------------------------------------------
ok(JSON.stringify(draw("Charging 95%")) === JSON.stringify(draw("Charging 95%")),
   "it is a pure function of its inputs");

console.log(failed ? `\n${failed} FAILED` : "\nall caption checks passed");
process.exit(failed ? 1 : 0);
