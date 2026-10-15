// The face page in render mode (`index.html?render=1`): no frame loop, door, camera, tracking, audio,
// AirPlay or operator overlay -- only window.OpenDubsRender, driven by the caller (render.js says how).
//
//   render=1      this mode
//   seed=N        the random seed (default 1)
//   w=W&h=H       the frame size, default 640x360. The face is 16:9 (the body twin's texture is
//                 320x180), so a 16:9 size fills it; any other is letterboxed on black.
//   library=<url> the skin library (default: the page's own, as live); skin=<name>; state=<STATE>
//   caption=1     draw the idle quote under the eyes, as the live page does (default: not drawn)

import { createRenderer } from "./render.js";

const q = new URLSearchParams(location.search);
const int = (k, d, lo, hi) => {
  const v = q.has(k) ? Number(q.get(k)) : d;
  if (!Number.isInteger(v) || v < lo || v > hi) throw new RangeError(`${k} must be a whole number from ${lo} to ${hi}`);
  return v;
};
const onRobot = location.pathname.startsWith("/face/");
const canvas = document.getElementById("face");

const ready = (async () => {
  const w = int("w", 640, 16, 3840), h = int("h", 360, 16, 2160), seed = int("seed", 1, 0, 2 ** 32 - 1);
  canvas.width = w; canvas.height = h;
  canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
  const ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
  const library = new URL(q.get("library") || (onRobot ? "/api/library/files/" : "../library/"), import.meta.url);
  const r = await createRenderer({ ctx, w, h, seed, library, skin: q.get("skin"), state: q.get("state") || "AUTONOMOUS",
                                    caption: q.get("caption") === "1" });
  api.apply = r.apply; api.step = r.step; api.frame = r.frame;
  return { w, h, seed };
})();

const notReady = () => { throw new Error("OpenDubsRender: await OpenDubsRender.ready first"); };
const api = { ready, apply: notReady, step: notReady, frame: notReady };
window.OpenDubsRender = api;
