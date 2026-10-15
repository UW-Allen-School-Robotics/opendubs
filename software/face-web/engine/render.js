// The offline render: the face drawn frame by frame on a show's clock, never the wall clock, so a
// show can be rendered to video (software/choreo) and the same show, seed and times always give the
// same pixels. Nothing here starts a timer, opens a socket, or reads performance.now(): time moves
// only when the caller says so, through apply() and step().
//
// The page wraps this as window.OpenDubsRender (render_page.js, `index.html?render=1`); Node tests
// call createRenderer() with a recording context (tests/test_render.mjs).

import * as faceModule from "./face.js";
import { Face } from "./face.js";
import { handle } from "./protocol.js";
import { Skins } from "./skins/index.js";
import { Levels } from "./levels.js";
import { drawFace } from "./frame.js";

/** A small seeded random source, [0, 1). The same seed gives the same sequence on every machine. */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function base64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/**
 * @param o.ctx        a 2D context of size w x h (the page's canvas; a recording stub in tests)
 * @param o.w, o.h     the frame size in pixels; the face's 16:9 design area is fitted and centred
 * @param o.seed       the random seed (blinks, glances, the idle quote)
 * @param o.library    the skin library's folder URL, or null for code skins only
 * @param o.skin       a skin to start in (a show usually says SKIN itself)
 * @param o.state      the robot state the face starts in (default AUTONOMOUS; STATE lines change it)
 * @param o.drawArmed  the ARMED overlay; undefined = load skins/armed.js, null = none
 */
export async function createRenderer({ ctx, w, h, seed = 1, library = null, skin = null, state = "AUTONOMOUS", drawArmed } = {}) {
  // Everything random in the engine follows the seed. The render owns its page (or its test), so the
  // global is replaced as well as the engine's own seam, which keeps an engine without one seeded too.
  const rng = mulberry32(seed);
  Math.random = rng;
  if (typeof faceModule.setRandom === "function") faceModule.setRandom(rng);

  const skins = new Skins();
  if (library) await skins.loadLibrary(library);   // read once: no second look during a render
  // An asset skin loads after it is chosen; until it has, the default eyes stand in. A render waits
  // for it (Face & HRI's skins.ready(): the skin chosen NOW, true once it can draw, false if it failed
  // or timed out), so the frame after a SKIN line is the skin, every time. A video skin is ready
  // once its first frame is, but which frame it shows later follows the wall clock: not repeatable.
  const skinReady = async () => typeof skins.ready === "function" ? (await skins.ready()) !== false : true;
  if (drawArmed === undefined) drawArmed = await import("./skins/armed.js").then((m) => m.drawArmed).catch(() => null);

  let t = 0, lastStep = 0;
  const levels = new Levels({}, { clock: () => t, timers: false, device: false });
  const face = new Face();
  const silent = { enabled: false, cue() {} };      // earcons are not part of the picture
  if (state) face.setState(state, 0);
  if (skin) { skins.select(skin); await skinReady(); }

  const advance = (to) => {
    if (typeof to !== "number" || !Number.isFinite(to)) throw new TypeError(`t_ms must be a number, got ${to}`);
    if (to < t) throw new RangeError(`time only runs forward: ${to} ms after ${t} ms`);
    t = to;
    levels.tick(t);
  };

  return {
    w, h, seed,
    face, skins, levels,                            // for a test to look at, not for a caller to drive
    /** A head-protocol line at show time `tMs`. Resolves to the reply the robot's face would give. */
    async apply(line, tMs) {
      advance(tMs);
      const reply = await handle(face, silent, String(line), tMs, skins, levels, { timers: false });
      // A skin that failed, or never arrived, has already fallen back to the default eyes: say so in
      // the reply, as the live face does for a skin that will not load, or that stretch of the video
      // reads as a show bug.
      if (/^\s*SKIN\b/i.test(String(line)) && String(reply).startsWith("OK") && !(await skinReady())) {
        const name = skins.chosen, why = skins.error ? skins.error.replace(`${name}: `, "") : "did not load in time";
        return `ERR skin ${name}: ${why} (showing default)`;
      }
      return reply;
    },
    /** Move the face to show time `tMs` and draw it. */
    step(tMs) {
      advance(tMs);
      const dt = Math.min(0.1, (tMs - lastStep) / 1000);
      lastStep = tMs;
      if (face.state >= 0) face.stateAt = tMs;      // the robot is talking to it: never NO LINK mid-show
      face.tick(tMs, dt);
      drawFace(ctx, w, h, tMs, { face, skins, drawArmed, bright: levels.level.bright });
    },
    /** The last step's pixels: {w, h, rgba} with rgba the w*h*4 bytes, base64. */
    frame() {
      return { w, h, rgba: base64(ctx.getImageData(0, 0, w, h).data) };
    },
    t: () => t,
  };
}
