// One frame of the face, drawn into a 2D context of any size: the page's live loop (main.js) and the
// offline render (render.js) both draw with this, so a rendered show looks like the robot's face.
//
// The design area (DESIGN_W x DESIGN_H) is fitted into W x H and centred, on `bg`. A reserved robot
// state (DEGRADED, E_STOP) draws the default eyes whatever skin is chosen, and the ARMED sign is drawn
// last, on top of everything (the skins' own notes refer to this as "main.js").

import { DESIGN_W, DESIGN_H } from "./face.js";
import { CODE_SKINS } from "./skins/index.js";

/**
 * @param ctx   a 2D context
 * @param W,H   its size in pixels
 * @param now   the face's clock, ms
 * @param o     {face, skins, drawArmed, bg, turn, song, bright, caption, cams, drawCamera, drawCharge, drawReticle}
 *              turn: a twin's simulated head turn, design px; song: AirPlay's {title, artist} or null;
 *              bright: 0..1, drawn as a dimming of the whole frame (the render; live, the device dims)
 *              caption: false = no line under the eyes (a rendered show: the idle quote is not the show's)
 *              words: the robot's state in plain words (head protocol CAPTION), drawn by drawCaption
 *              (Face & HRI's skins/caption.js); in its place, the line under the eyes
 *              cams: {screen, back}, which of the tablet's cameras are open; drawCamera: the skin's
 *              indicator (Face & HRI's skins/camera.js), else the plain one here
 * @returns     {fullFace, armed, camera}: camera is whether the camera sign was drawn
 */
export function drawFace(ctx, W, H, now, { face, skins, drawArmed = null, bg = "#000", turn = 0, song = null, bright = 1, caption = true,
                          cams = null, drawCamera = null, drawCharge = null, drawReticle = null,
                          words = null, drawCaption = null }) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
  const s = Math.min(W / DESIGN_W, H / DESIGN_H);
  ctx.setTransform(s, 0, 0, s, (W - DESIGN_W * s) / 2 - turn * s, (H - DESIGN_H * s) / 2);
  // A reserved state (DEGRADED, E_STOP) overrides any skin: "something is wrong" must look the same on
  // every robot and every show, so it is drawn by the default eyes in the state's own colour.
  const drawn = face.reserved() ? CODE_SKINS.default : skins.current;
  let skinDrew = true;
  for (const e of face.eyes()) {
    // An asset skin still loading (or failed) draws nothing: the default eyes stand in. Never blank.
    if (drawn.draw(ctx, e, now / 1000) === false) { CODE_SKINS.default.draw(ctx, e, now / 1000); skinDrew = false; }
  }
  // A `layout: full` skin is a picture across the whole face (a show's photo): the idle quote would
  // read as a caption printed on it, so it waits. Eye skins keep it; it is the face's own line.
  const fullFace = skinDrew && drawn !== CODE_SKINS.default &&
                   skins.library.get(skins.chosen)?.params?.layout === "full";
  // Nor while a show is armed: the ARMED sign owns the bottom of the face, and must read alone.
  const armed = typeof face.armedState === "function" && face.armedState(now);
  // On charge (head protocol v6 BATT, drawn by Face & HRI's skins/charge.js): what the robot itself is
  // doing comes before a song or the idle quote. Below ARMED, which owns the bottom while it shows.
  const charge = typeof face.chargeText === "function" ? face.chargeText(now) : null;
  // AirPlay playing and heard: what it is, in the quote's place.
  // The state in plain words (CAPTION) is for whoever cannot read the colours, so it comes first and shows
  // over any skin and with ARMED (its band ends above ARMED's frame). With Face & HRI's panel it is drawn
  // below; without it, it takes this line. When the dashboard sends none, the charge line says charging.
  const panel = words && drawCaption;
  const text = words && !panel ? words
    : !caption || fullFace || armed || panel ? null
    : charge ? charge
    : song ? `♪ ${song.title || "AirPlay"}${song.artist ? ` — ${song.artist}` : ""}` : face.quoteText();
  if (text) { ctx.fillStyle = "#5a5a5a"; ctx.font = "24px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillText(text, DESIGN_W / 2, 672); }
  // THE TRACKER'S RETICLE (Face & HRI's skins/reticle.js), when the Head & face page turns it on: FIRST
  // of the overlays, under the charge gauge, ARMED and the camera sign. Its centre is the face's own
  // target, the value that drives the eyes (clamped as the eyes clamp it), NOT the detector's box: a
  // wrong mapping or sign then shows as a box that moves the wrong way. HIDDEN in a reserved state,
  // and that is load-bearing: the motion amber is DEGRADED's amber, so it must never sit on that face.
  if (drawReticle && !face.reserved()) {
    const t = face.target;
    const live = t && Number.isFinite(t.x) && Number.isFinite(t.y) && now - t.at < 700;   // the eyes' own freshness
    if (live) {
      const c = (v) => Math.max(-1, Math.min(1, v));
      drawReticle(ctx, { x: DESIGN_W / 2 + c(t.x) * DESIGN_W / 2, y: DESIGN_H / 2 + c(t.y) * DESIGN_H / 2, kind: t.kind || "face" }, now / 1000);
    }
  }
  // The words' panel, AFTER the reticle: a robot looking down puts the reticle's lower edge on the caption
  // band, and the words are for someone already struggling to read the face (Face & HRI). It overlaps
  // none of the gauge, ARMED's frame or the camera sign, which follow.
  if (panel) drawCaption(ctx, words, { fault: face.reserved() });
  // The charge gauge round the edge (the outermost 8 px), FIRST of the overlays, under ARMED's frame
  // (which starts at 10 px) and the camera sign.
  if (drawCharge && typeof face.chargeState === "function") drawCharge(ctx, face.chargeState(now), now / 1000);
  // "A live show is armed" (head protocol v5 ARMED): drawn LAST, on top of the skin and the quote and
  // inside the design-space transform, so no skin or photo can hide it (Face & HRI's skins/armed.js).
  if (drawArmed && typeof face.armedState === "function") drawArmed(ctx, face.armedState(now), now / 1000);
  // A CAMERA IS OPEN (the user's condition for using the tablet's cameras, 2026-10-15): shown whenever
  // either camera is open, for tracking or a stream, over every skin, photo and reserved state, and
  // never when none is. Drawn after ARMED, so nothing covers it.
  const camera = !!(cams && (cams.screen || cams.back));
  if (camera) (drawCamera || drawCameraPlain)(ctx, cams, now / 1000);
  // BRIGHT, in the picture itself: black over the whole frame at (1 - bright) is exactly CSS
  // brightness(bright), which is how the page shows it live without the kiosk app.
  if (bright < 1) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = `rgba(0,0,0,${(1 - Math.max(0, bright)).toFixed(4)})`; ctx.fillRect(0, 0, W, H);
  }
  return { fullFace, armed, camera };
}

/** The plain camera indicator, until Face & HRI's skins/camera.js draws its own: a red dot at the
 *  top centre for each open camera, F (screen-side, the robot's front) and R (back-side, its rear).
 *  Top centre because the brows own the top corners (an ALERT brow reaches above the design area);
 *  the corridor between them is where the skin's sign sits too. */
export function drawCameraPlain(ctx, cams, _t) {
  const on = [cams.screen && "F", cams.back && "R"].filter(Boolean);
  ctx.save();
  ctx.font = "600 16px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  on.forEach((label, i) => {
    const x = DESIGN_W / 2 + (i - (on.length - 1) / 2) * 44, y = 60;
    ctx.beginPath(); ctx.arc(x, y, 14, 0, Math.PI * 2); ctx.fillStyle = "#3a0d0d"; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2); ctx.fillStyle = "#ff3b30"; ctx.fill();
    ctx.fillStyle = "#fff"; ctx.fillText(label, x, y + 1);
  });
  ctx.restore();
}
