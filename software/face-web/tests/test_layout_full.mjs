// `layout: full` in engine/skins/asset.js: a library skin drawn ONCE across the whole face,
// aspect-fit and centred, instead of once per eye. A still card a show pops up for a bar.
//
// The image is BUILT IN MEMORY. The real first use is a private photo that is not in this repo and
// must not be: no fixture, no test asset, no filename. Nothing here needs a real picture anyway --
// what is being checked is the geometry, and a stub that reports a size exercises it exactly.
//
// Run: node software/face-web/tests/test_layout_full.mjs   (conformance.sh runs it)

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// A stub image whose size the test chooses, and which can be left undecoded on purpose.
let NEXT = { w: 0, h: 0, fail: false, hold: false };
globalThis.Image = class {
  set src(_) {
    this.naturalWidth = NEXT.w; this.naturalHeight = NEXT.h;
    if (NEXT.hold) return;                       // never fires: "still loading"
    if (NEXT.fail) this.onerror && this.onerror();
    else this.onload && this.onload();
  }
};
globalThis.document = { createElement: () => ({ setAttribute() {}, removeAttribute() {}, load() {}, pause() {} }) };

const here = dirname(fileURLToPath(import.meta.url));
const { makeAssetSkin, ASSET_PARAM_DEFAULTS } = await import(join(here, "../engine/skins/asset.js"));
const { DESIGN_W, DESIGN_H } = await import(join(here, "../engine/face.js"));

let failed = 0;
function ok(cond, what, detail = "") {
  console.log(`${cond ? "ok  " : "FAIL"}  ${what}${detail ? "   " + detail : ""}`);
  if (!cond) failed++;
}

// Draw both eyes and record every drawImage, with whatever transform was in force.
function render(params, img = { w: 1600, h: 600 }) {
  NEXT = { w: img.w, h: img.h, fail: img.fail || false, hold: img.hold || false };
  const sk = makeAssetSkin({ id: "still", type: "image", file: "x.png", params }, "http://x/", () => {});
  const calls = [], fellBack = [];
  const ctx = { save() {}, restore() {}, translate() {}, scale() {}, rotate() {},
                drawImage(_s, x, y, w, h) { calls.push({ x, y, w, h }); } };
  for (const side of ["left", "right"]) {
    const r = sk.draw(ctx, { cx: side === "left" ? 326 : 954, cy: 360, w: 480, h: 480, radius: 98,
                             lid: 0, browRaise: 0, browAngle: 0, gazeX: 0.8, gazeY: -0.5, side }, 3.0);
    if (r === false) fellBack.push(side);
  }
  return { sk, calls, fellBack };
}

// ---- 1. once for the face, not once per eye ----------------------------------------------------
{
  const { calls, fellBack } = render({ layout: "full" });
  ok(calls.length === 1, "a full skin draws ONCE, not once per eye", `${calls.length} draw(s)`);
  ok(fellBack.length === 0,
     "and the right eye reports handled, so the default eye is not drawn over the still",
     fellBack.join(",") || "neither eye fell back");
}

// ---- 2. aspect-fit and centred, both ways round -------------------------------------------------
// A face is not croppable: the whole image must be inside the design area, centred, with the bars
// on whichever axis is spare. Two shapes, so a scale that only works for one is caught.
for (const [img, want] of [
  [{ w: 1600, h: 600 }, { x: 0, y: 120, w: 1280, h: 480 }],      // wider than the face: bars above and below
  [{ w: 400, h: 900 }, { x: 480, y: 0, w: 320, h: 720 }],        // taller: bars left and right
]) {
  const { calls } = render({ layout: "full" }, img);
  const c = calls[0];
  const exact = c && c.x === want.x && c.y === want.y && c.w === want.w && c.h === want.h;
  ok(exact, `a ${img.w}x${img.h} image is fitted and centred`,
     c ? `${c.w}x${c.h} at ${c.x},${c.y}` : "nothing drawn");
  if (c) {
    ok(Math.abs(c.w / c.h - img.w / img.h) < 1e-9, `  and its aspect ratio is unchanged`);
    ok(c.x >= 0 && c.y >= 0 && c.x + c.w <= DESIGN_W + 1e-9 && c.y + c.h <= DESIGN_H + 1e-9,
       `  and nothing is cropped off the design area`);
  }
}

// ---- 3. a still is still -----------------------------------------------------------------------
// Gaze, spin and the eye geometry must make no difference: the same image lands in the same place
// whatever the face is doing, or a photo would drift about or rotate.
{
  const a = render({ layout: "full" }).calls[0];
  const b = render({ layout: "full", spin_dps: 120, follow_look: true, look: 0.9, mirror: true }).calls[0];
  ok(JSON.stringify(a) === JSON.stringify(b),
     "gaze, spin, look and mirror change nothing about where it lands", JSON.stringify(b));

  const { sk, calls } = render({ layout: "full", spin_dps: 120 });
  sk.spin("counter", 2);                       // the protocol answers OK; a still must not spin
  const after = render({ layout: "full", spin_dps: 120 }).calls[0];
  ok(JSON.stringify(calls[0]) === JSON.stringify(after), "and SPIN on a full skin does nothing");
}

// ---- 4. not ready: BOTH eyes fall back, never one ------------------------------------------------
// Half a face would be worse than none: the left would show a still and the right a default eye.
{
  const { calls, fellBack } = render({ layout: "full" }, { w: 1600, h: 600, hold: true });
  ok(calls.length === 0 && fellBack.length === 2,
     "a still that has not loaded stands BOTH default eyes in", `fell back: ${fellBack.join(",") || "neither"}`);

  // Decoded but sizeless would divide by zero and place the image nowhere; stand the eyes in too.
  const z = render({ layout: "full" }, { w: 0, h: 0 });
  ok(z.calls.length === 0 && z.fellBack.length === 2, "and so does one that decodes to no size");
}

// ---- 5. every existing entry is untouched --------------------------------------------------------
{
  ok(ASSET_PARAM_DEFAULTS.layout === "eyes", "layout defaults to eyes");
  const { calls } = render({});                       // no layout at all: today's behaviour
  ok(calls.length === 2, "a skin with no layout still draws once per eye", `${calls.length} draws`);
  const { calls: c2 } = render({ layout: "eyes" });
  ok(c2.length === 2, "and so does an explicit layout: eyes");
}

console.log(failed ? `\n${failed} FAILED` : "\nall layout checks passed");
process.exit(failed ? 1 : 0);
