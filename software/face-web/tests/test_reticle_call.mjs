// The reticle's call site (engine/frame.js): drawn FIRST of the overlays, from the face's own target
// (what drives the eyes), never in a reserved state, never for a stale target.
//
//   node software/face-web/tests/test_reticle_call.mjs

const { drawFace } = await import("../engine/frame.js");
let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const ctx = new Proxy({}, { get: (_, k) => (k === "measureText" ? () => ({ width: 1 }) : () => {}), set: () => true });

function run(target, { reserved = false } = {}) {
  const order = [];
  const face = { reserved: () => reserved, eyes: () => [], armedState: () => "wait", quoteText: () => null,
                 chargeState: () => ({}), target };
  const skins = { current: { draw: () => true }, library: new Map(), chosen: "default" };
  drawFace(ctx, 1280, 720, 1000, { face, skins, cams: { screen: true },
    drawReticle: (c, t) => order.push(["reticle", t]), drawCharge: () => order.push(["charge"]),
    drawArmed: () => order.push(["armed"]), drawCamera: () => order.push(["camera"]) });
  return order;
}
const fresh = { x: 0.5, y: -0.5, conf: 0.8, at: 900, kind: "person" };
const o = run(fresh);
check("drawn first of the overlays", o.map((e) => e[0]).join() === "reticle,charge,armed,camera", o.map((e) => e[0]).join());
check("from the face's target, in design space", o[0][1].x === 960 && o[0][1].y === 180 && o[0][1].kind === "person", JSON.stringify(o[0][1]));
check("clamped as the eyes clamp it", run({ ...fresh, x: 3 })[0][1].x === 1280, run({ ...fresh, x: 3 })[0][1].x);
check("not in a reserved state", !run(fresh, { reserved: true }).some((e) => e[0] === "reticle"), "drawn");
check("not for a stale target", !run({ ...fresh, at: 100 }).some((e) => e[0] === "reticle"), "drawn");
check("not with no target", !run(null).some((e) => e[0] === "reticle"), "drawn");
console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
