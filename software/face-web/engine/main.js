// The face page: the face model, a skin, the layers, the door, and — for measuring — a frame-time
// readout. Query parameters (all optional):
//   door=ws://127.0.0.1:8766   the door's websocket (door_dev.py on a laptop; the app on the tablet)
//   skin=<name>                which skin: a code skin (default, ring) or a library skin's id
//   library=<url>              the library folder (default ../library/, made by make_test_library.py)
//   track=1                    look at a person through the camera (track.js)
//   hud=1                      frame-time readout
//   video=<url>                a media layer, for measuring video decode beside the face ('v' cuts)
//   audio=1                    earcons audible
//   state=SAFE_IDLE            start in a state (a twin with no door otherwise shows NO LINK)
//   bg=%23334455               a background colour instead of black — a test that a skin's
//                              transparency is real (on black, a missing alpha plane is invisible)
// Keys (twin conveniences, never a robot path): 1-7 state, e emote, a auto, s next skin, t theme, v cut.

import { Face, DESIGN_W, DESIGN_H } from "./face.js";
import { STATES, EMOTES } from "./vocab.js";
import { handle, Earcons, libraryRefresher } from "./protocol.js";
import { Skins, CODE_SKINS } from "./skins/index.js";
import { Levels } from "./levels.js";

const q = new URLSearchParams(location.search);
// Served by the robot's dashboard at /face/ (face_door.py): the door and the library are this page's
// own origin, so the kiosk URL is just https://<robot>:8443/face/ and can never carry a wrong scheme.
const onRobot = location.pathname.startsWith("/face/");
if (onRobot && !q.has("door")) q.set("door", `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/face`);
if (onRobot && !q.has("library")) q.set("library", "/api/library/files/");
const face = new Face();
const audio = new Earcons(q.get("audio") === "1");
const skins = new Skins();
const levels = new Levels();                       // VOLUME / BRIGHT (head protocol v5)
// The ARMED overlay, loaded rather than imported so the page still runs on an engine without it.
let drawArmed = null;
import("./skins/armed.js").then((m) => { drawArmed = m.drawArmed; }).catch(() => {});
window.faceLevels = levels;                        // read by a harness through devtools
window.faceSkins = skins;                          // read by a harness through devtools
const libraryUrl = new URL(q.get("library") || "../library/", import.meta.url);
skins.loadLibrary(libraryUrl).then(() => {
  if (q.get("skin")) skins.select(q.get("skin"));
});
skins.refresh = libraryRefresher(skins, libraryUrl);   // a skin added after this page loaded (protocol.js)
let neck = null;                                   // {pan, tilt, owner} from a door, if it has a neck

const BG = q.get("bg") || "#000";
const canvas = document.getElementById("face");
const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
function fit() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(innerWidth * dpr); canvas.height = Math.round(innerHeight * dpr);
}
addEventListener("resize", fit); fit();

// ---- the layers: the face (canvas) and a media layer (video), cut by visibility ---------------
const video = document.getElementById("media");
if (q.get("video")) { video.src = q.get("video"); video.loop = true; video.muted = true; video.play().catch(() => {}); }
window.cut = (layer) => cut(layer);                // for a harness through devtools
export function cut(layer) { video.style.visibility = layer === "media" ? "visible" : "hidden"; }

// ---- measuring --------------------------------------------------------------------------------
const hud = q.get("hud") === "1" ? document.getElementById("hud") : null;
const frames = [], draws = [];
export const stats = { fps: 0, p50: 0, p95: 0, drawP95: 0, track: null };
function pct(a, p) { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; }
window.faceStats = stats;                           // read by a harness through devtools

// ---- the frame --------------------------------------------------------------------------------
let last = performance.now();
function frame(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  frames.push(now - last); last = now;
  if (frames.length > 240) frames.shift();
  face.tick(now, dt);

  const t0 = performance.now();
  const W = canvas.width, H = canvas.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
  const s = Math.min(W / DESIGN_W, H / DESIGN_H);
  // A twin with a neck: show the head's turn as a shift of the whole face (simulated, and labelled).
  const turn = neck ? (neck.pan - 90) * 4 : 0;
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
  const text = fullFace || armed ? null : face.quoteText();
  if (text) { ctx.fillStyle = "#5a5a5a"; ctx.font = "24px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillText(text, DESIGN_W / 2, 672); }
  // "A live show is armed" (head protocol v5 ARMED): drawn LAST, on top of the skin and the quote and
  // inside the design-space transform, so no skin or photo can hide it (Face & HRI's skins/armed.js).
  if (drawArmed && typeof face.armedState === "function") drawArmed(ctx, face.armedState(now), now / 1000);
  draws.push(performance.now() - t0); if (draws.length > 240) draws.shift();

  if (hud && frames.length % 15 === 0) {
    stats.p50 = pct(frames, 0.5); stats.p95 = pct(frames, 0.95); stats.fps = 1000 / stats.p50;
    stats.drawP95 = pct(draws, 0.95);
    if (q.get("video")) {
      const v = video.getVideoPlaybackQuality();
      stats.video = { dropped: v.droppedVideoFrames, total: v.totalVideoFrames, w: video.videoWidth, h: video.videoHeight,
                      showing: video.style.visibility === "visible" };
    }
    hud.textContent = `${stats.fps.toFixed(0)} fps  frame p50 ${stats.p50.toFixed(1)} p95 ${stats.p95.toFixed(1)} ms` +
      `  draw p95 ${stats.drawP95.toFixed(2)} ms  ${canvas.width}x${canvas.height}  skin ${skins.shownName()}` +
      (skins.error ? ` (${skins.error})` : "") +
      `  ${face.stateName()} ${face.emote}${face.pin ? " (pinned)" : ""}  theme ${face.theme}` +
      (neck ? `  neck ${neck.pan.toFixed(0)}/${neck.tilt.toFixed(0)} ${neck.owner} (simulated)` : "") +
      (stats.track ? `  track ${stats.track}` : "") +
      (stats.video ? `  video ${stats.video.w}x${stats.video.h} dropped ${stats.video.dropped}/${stats.video.total}${stats.video.showing ? " (showing)" : ""}` : "");
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ---- the door ---------------------------------------------------------------------------------
const doorUrl = q.get("door");
function connect() {
  const ws = new WebSocket(doorUrl);
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.neck) neck = msg.neck;
    if (msg.frames) startTap(ws, msg.frames);
    if (msg.settings) applySettings(msg.settings);
    if (msg.line !== undefined) {
      Promise.resolve(handle(face, audio, msg.line, performance.now(), skins, levels))
        .then((reply) => ws.send(JSON.stringify({ id: msg.id, reply })));
    }
  };
  // A lost door: back to the idle levels, so a dead runner cannot leave the robot dimmed or loud.
  ws.onclose = () => { stopTap(); levels.restoreIdle(); setTimeout(connect, 1000); };
}
if (doorUrl) connect();

// ---- the head's idle settings (the dashboard's Head & face page; backend/head_settings.py) ------
// Sent by the door on connect and on every change. They are what the face does when no show is
// running; a show's own cues override them, and VOLUME IDLE / BRIGHT IDLE return to them.
let tracking = false;
// What the settings last said, so that a reconnect (the same settings, sent again) does not undo what
// a running show is doing: the skin, theme and expressions are applied only when they CHANGE (or on
// the page's first load), never because the door reconnected (2026-10-13: a show's skin was reset).
const appliedSettings = {};
function changed(key, value) {
  if (Object.prototype.hasOwnProperty.call(appliedSettings, key) && appliedSettings[key] === value) return false;
  appliedSettings[key] = value; return true;
}
function applySettings(s) {
  levels.configure({ brightMin: s.bright_min, volMax: s.vol_max,
                     idle: { bright: s.bright_idle, ...(typeof s.volume_idle === "number" ? { volume: s.volume_idle } : {}) } });
  if (!levels.touched) {                       // no show holds the levels: show the idle ones now
    levels.idle("bright", 0.5);
    if (typeof s.volume_idle === "number") levels.idle("volume", 0.5);
  }
  skins.idle = { skin: s.skin || "default", theme: s.theme || "plain" };   // for SKIN IDLE / THEME IDLE
  if (changed("skin", s.skin) && s.skin && s.skin !== skins.chosen) skins.select(s.skin);
  if (changed("theme", s.theme) && s.theme && s.theme !== face.theme) face.setTheme(s.theme);
  if (typeof face.setMouth === "function") face.setMouth(!!s.mouth);
  if (changed("idle_eyes", s.idle_eyes)) face.setEmote(s.idle_eyes === false ? "NEUTRAL" : "AUTO", performance.now());
  const K = window.OpenDubsKiosk;
  if (K && typeof K.keepAwake === "function") K.keepAwake(s.screen_on !== false);
  const want = s.tracking === "tablet";        // the face's own camera; "d415" is the Thor's tracker
  if (want && !tracking) {
    tracking = true;
    import("./track.js").then((m) => m.start(face, stats)).catch((e) => { stats.track = "unavailable: " + e.message; });
  } else if (!want && tracking) {
    location.reload();                         // the camera is released with the page
  }
}

// ---- the body twin's picture (door_dev.py with OPENDUBS_FACE_FRAMES; the robot's door never asks) -
// The MuJoCo body twin shows this face on its head by copying it, never by drawing its own (a second
// drawing would drift). Asked for by the door: the face's design area, without the simulated turn
// (the body twin turns the head itself), scaled to the twin's texture, sent as RGB rows when a pixel
// changed. door_dev.py writes it where the Tab5 desktop face would (frame_tap.py).
let tapTimer = null;
function stopTap() { clearInterval(tapTimer); tapTimer = null; }
function startTap(ws, { w, h, fps }) {
  stopTap();
  stats.tap = { sent: 0, ticks: 0 };
  const small = document.createElement("canvas");
  small.width = w; small.height = h;
  const sctx = small.getContext("2d", { willReadFrequently: true });
  const rgb = new Uint8Array(w * h * 3);
  let sent = false;
  tapTimer = setInterval(() => {
    stats.tap.ticks++;
    if (ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > rgb.length) return;   // never queue up
    const W = canvas.width, H = canvas.height, s = Math.min(W / DESIGN_W, H / DESIGN_H);
    if (!W || !H) return;                                       // a minimised window: nothing to copy
    const turn = neck ? (neck.pan - 90) * 4 : 0;
    sctx.fillStyle = "#000"; sctx.fillRect(0, 0, w, h);
    sctx.drawImage(canvas, (W - DESIGN_W * s) / 2 - turn * s, (H - DESIGN_H * s) / 2, DESIGN_W * s, DESIGN_H * s, 0, 0, w, h);
    const px = sctx.getImageData(0, 0, w, h).data;
    let changed = !sent;
    for (let i = 0, j = 0; i < px.length; i += 4, j += 3) {
      if (rgb[j] !== px[i] || rgb[j + 1] !== px[i + 1] || rgb[j + 2] !== px[i + 2]) {
        changed = true; rgb[j] = px[i]; rgb[j + 1] = px[i + 1]; rgb[j + 2] = px[i + 2];
      }
    }
    if (changed) { ws.send(rgb); sent = true; stats.tap.sent++; }
  }, 1000 / (fps || 15));
}

// ---- twin conveniences ------------------------------------------------------------------------
const names = Object.keys(EMOTES);
let ei = 0, si = 0;
addEventListener("keydown", (k) => {
  const now = performance.now();
  if (k.key >= "1" && k.key <= "7") face.setState(STATES[+k.key - 1][0], now);
  else if (k.key === "e") face.setEmote(names[ei++ % names.length], now);
  else if (k.key === "a") face.setEmote("AUTO", now);
  else if (k.key === "s") { const n = skins.names(); skins.select(n[(n.indexOf(skins.chosen) + 1) % n.length]); }
  else if (k.key === "t") face.setTheme(face.theme === "plain" ? "halloween" : "plain");
  else if (k.key === "v") cut(video.style.visibility === "visible" ? "face" : "media");
});
// A twin with no door has no robot: say so on the face, as the head does (NO LINK), and keep the
// state fresh so it does not go stale while someone is looking at skins.
if (q.get("state")) face.setState(q.get("state"), performance.now());   // e.g. state=SAFE_IDLE
if (!doorUrl) setInterval(() => { if (face.state >= 0) face.stateAt = performance.now(); }, 1000);

import("./operator.js").then((m) => m.start({ onRobot }));   // long-press: STOP SHOW / DASHBOARD / FACE
if (q.get("track") === "1") tracking = true;
if (q.get("track") === "1") import("./track.js").then((m) => m.start(face, stats)).catch((e) => { stats.track = "unavailable: " + e.message; });
