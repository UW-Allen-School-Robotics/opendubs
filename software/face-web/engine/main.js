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
//   render=1                   not this page at all: the offline render (render_page.js, via boot.js)
// Keys (twin conveniences, never a robot path): 1-7 state, e emote, a auto, s next skin, t theme, v cut.

import { Face, DESIGN_W, DESIGN_H } from "./face.js";
import { drawFace } from "./frame.js";
import { STATES, EMOTES } from "./vocab.js";
import { handle, Earcons, libraryRefresher, captionNow } from "./protocol.js";
import { Skins } from "./skins/index.js";
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

// ---- which cameras are open: the face says so whenever one is (frame.js) ----------------------
// From the kiosk app, the hardware's own state (any app, any camera: OpenDubsKiosk.cameras()); and,
// in any browser, this page's own live video tracks, so a laptop twin's webcam shows it too. Either
// one open lights the indicator.
let drawCamera = null, drawCharge = null, drawReticle = null, showReticle = false, drawCaption = null;
import("./skins/caption.js").then((m) => { drawCaption = m.drawCaption || null; }).catch(() => {});
import("./skins/reticle.js").then((m) => { drawReticle = m.drawReticle || null; }).catch(() => {});
import("./skins/charge.js").then((m) => { drawCharge = m.drawCharge || null; }).catch(() => {});
import("./skins/camera.js").then((m) => { drawCamera = m.drawCameraOn || null; }).catch(() => {});
const cams = { screen: false, back: false };
const ownVideo = new Set();
if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
  const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    const s = await gum(c);
    for (const t of s.getVideoTracks()) { ownVideo.add(t); t.addEventListener("ended", () => ownVideo.delete(t)); }
    return s;
  };
}
function pollCameras() {
  let screen = false, back = false;
  for (const t of ownVideo) {
    if (t.readyState !== "live") { ownVideo.delete(t); continue; }
    if ((t.getSettings().facingMode || "user") === "environment") back = true; else screen = true;
  }
  const K = window.OpenDubsKiosk;
  if (K && typeof K.cameras === "function") {
    try {
      for (const c of JSON.parse(K.cameras())) {
        if (!c.open) continue;
        if (c.facing === "back") back = true; else screen = true;   // screen-side, or external: shown as F
      }
    } catch (e) { screen = screen || ownVideo.size > 0; }
  }
  cams.screen = screen; cams.back = back;
  window.faceCameras = cams;                        // read by a harness through devtools
}
setInterval(pollCameras, 500); pollCameras();
// Leaving the page closes its cameras, rather than trusting the browser to: on the robot tablet a page
// that lingered after its activity went (2026-10-15) kept the screen-side camera open, and the next
// face page could not open it.
addEventListener("pagehide", () => { for (const t of ownVideo) t.stop(); ownVideo.clear(); });
window.faceLevels = levels;                        // read by a harness through devtools
window.faceSkins = skins;                          // read by a harness through devtools
const libraryUrl = new URL(q.get("library") || "../library/", import.meta.url);
skins.loadLibrary(libraryUrl).then(() => {
  if (q.get("skin")) skins.select(q.get("skin"));
  // The robot's idle skin may have been chosen before the library had loaded, and fallen back to the
  // default eyes for that alone: now that it is here, choose it again (once; a show's own SKIN wins).
  else if (skins.idle && skins.chosen === skins.idle.skin && skins.error) skins.select(skins.idle.skin);
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
const health = { drawnAt: performance.now(), eyesOk: true, lost: false, fullFace: false, frames: 0,
                 errors: 0, lastError: null, refits: 0 };
let lastAlive = 0, lastDrawn = 0;
canvas.addEventListener("contextlost", () => { health.lost = true; });

function frame(now) {
  // Scheduled first, so nothing below can end the loop; a frame that throws is counted and reported.
  requestAnimationFrame(frame);
  // A canvas whose size no longer matches the page (laid out at 0 x 0 before the view had a size, say)
  // draws nowhere. Refit it here rather than trusting a resize event to arrive.
  const dpr = window.devicePixelRatio || 1;
  if (innerWidth && (canvas.width !== Math.round(innerWidth * dpr) || canvas.height !== Math.round(innerHeight * dpr))) {
    fit(); health.refits++;
  }
  if (power.save && now - lastDrawn < SAVE_FRAME_MS) return;     // saving power: every other frame
  lastDrawn = now;
  try { drawFrame(now); } catch (e) { health.errors++; health.lastError = String(e && e.message || e).slice(0, 160); }
}
function drawFrame(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  frames.push(now - last); last = now;
  if (frames.length > 240) frames.shift();
  face.tick(now, dt);

  const t0 = performance.now();
  const W = canvas.width, H = canvas.height;
  // A twin with a neck: show the head's turn as a shift of the whole face (simulated, and labelled).
  const turn = neck ? (neck.pan - 90) * 4 : 0;
  // AirPlay playing and heard: what it is, in the quote's place.
  const ap = window.OpenDubsAirplay, song = ap && ap.gate !== "mute" && ap.playing && ap.playing.active ? ap.playing : null;
  const drew = drawFace(ctx, W, H, now, { face, skins, drawArmed, bg: BG, turn, song, cams, drawCamera, drawCharge,
                                   drawReticle: showReticle ? drawReticle : null, words: captionNow(now), drawCaption });
  stats.cameraSign = drew.camera;
  health.drawnAt = now; health.eyesOk = drew.eyesOk; health.fullFace = drew.fullFace; health.frames++;
  // The kiosk app's watchdog (KioskActivity): a robot page that stops checking in is reloaded. From the
  // frame loop, so a page whose drawing has stopped stops checking in too.
  if (now - lastAlive > 5000) { lastAlive = now; try { window.OpenDubsKiosk?.alive?.(); } catch (e) {} }
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
}
requestAnimationFrame(frame);

// ---- the door ---------------------------------------------------------------------------------
const doorUrl = q.get("door");
let door = null;                                   // the open door, for the tracker's target below
// THE CONTENT FOLDER'S CODE SKINS (head-protocol.md "Discovering the looks"; Face & HRI's skins.loadLooks):
// the robot's discovered looks, read BEFORE the door opens, so a SKIN naming a content skin finds it; and
// again at every reconnect, so a redeployed content folder is picked up. A twin with no dashboard, or an
// older robot, has the built-ins only. Never holds the door for more than 3 s.
const LOOKS_URL = new URL("/api/head/looks", location.href).href;
const CONTENT_SKINS = new URL("/face/content/skins/", location.href).href;
function loadLooks() {
  if (typeof skins.loadLooks !== "function") return Promise.resolve();   // a face from before content skins
  return Promise.race([skins.loadLooks(LOOKS_URL, CONTENT_SKINS), new Promise((r) => setTimeout(r, 3000))]);
}
function connect() {
  loadLooks().finally(openDoor);
}
function openDoor() {
  const ws = new WebSocket(doorUrl);
  door = ws;
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

// ---- the head follows: where the tracker sees a person, to the robot (backend/neck_follow.py) ----
// Up to 10 a second while someone is seen, and one "nobody" when they are lost. The robot decides
// whether the neck moves (the setting, a show, RC); the page only says what it sees.
let sentNobody = true;
// And what the tracker is doing, every 2 s: off, loading, running (Hz, % found, the frame's size) or
// why it failed. The robot shows it on /api/head/follow and the Head & face page.
setInterval(() => {
  if (!door || door.readyState !== WebSocket.OPEN) return;
  const i = stats.trackInfo;
  const st = !tracking ? { state: "off" } : !i ? { state: "loading" } : {
    state: i.state, hz: i.hz, found_pct: i.found_pct, frame: i.frame, kind: i.kind, confidence: i.confidence,
    cues: i.cues, person_model: i.person_model,
    last_found_s: i.last_found_at ? +((performance.now() - i.last_found_at) / 1000).toFixed(1) : null };
  door.send(JSON.stringify({ track_status: st }));
}, 2000);
setInterval(() => {
  if (!door || door.readyState !== WebSocket.OPEN || !tracking) return;
  const t = face.target;
  const fresh = t && typeof t.ix === "number" && performance.now() - t.at < 400;
  if (fresh) {
    // age_ms: how long ago the frame behind this position was captured. A held cue repeats its old
    // position with a growing age, and the neck aims only at a NEW sighting (neck_follow.py).
    const age = typeof t.seen === "number" && t.seen > 0 ? Math.max(0, Math.round(performance.now() - t.seen)) : undefined;
    door.send(JSON.stringify({ track: { ix: t.ix, iy: t.iy, conf: t.conf, kind: t.kind || "face", age_ms: age } }));
    sentNobody = false;
  }
  else if (!sentNobody) { door.send(JSON.stringify({ track: null })); sentNobody = true; }
}, 100);

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
                     idle: { bright: s.bright_idle, volume: typeof s.volume_idle === "number" ? s.volume_idle : null } });
  if (!levels.touched) {                       // no show holds the levels: show the idle ones now
    levels.idle("bright", 0.5);
    if (typeof s.volume_idle === "number") levels.idle("volume", 0.5);
  }
  // For SKIN IDLE / THEME IDLE, and EMOTE AUTO when expressions are off (protocol.js).
  skins.idle = { skin: s.skin || "default", theme: s.theme || "plain", eyes: s.idle_eyes !== false };
  showReticle = s.show_reticle === true;       // the tracker's reticle: live, no reload
  // Not while a show holds the skin ("a show overrides these"): SKIN IDLE at its end applies the new one.
  if (changed("skin", s.skin) && s.skin && s.skin !== skins.chosen && !skins.showHeld) skins.select(s.skin);
  if (changed("theme", s.theme) && s.theme && s.theme !== face.theme) face.setTheme(s.theme);
  if (typeof face.setMouth === "function") face.setMouth(!!s.mouth);
  if (changed("idle_eyes", s.idle_eyes)) face.setEmote(s.idle_eyes === false ? "NEUTRAL" : "AUTO", performance.now());
  const K = window.OpenDubsKiosk;
  if (K && typeof K.keepAwake === "function") K.keepAwake(s.screen_on !== false);
  const want = s.tracking === "tablet";        // the face's own camera; "d415" is the Thor's tracker
  if (want && !tracking) {
    tracking = true;
    import("./track.js").then((m) => m.start(face, stats))
      .then(() => { if (power.battery) window.OpenDubsTracking?.pause("battery"); }).catch((e) => { stats.track = "unavailable: " + e.message; });
  } else if (!want && tracking) {
    location.reload();                         // the camera is released with the page
  }
}

// ---- the tablet's power (notes/design/power-and-shutdown.md §9) ---------------------------------
// The tablet is powered from the robot's drive bus, so on battery the robot is off or e-stopped. The
// face saves power at once: half brightness, half the frame rate, camera tracking off. When the screen
// sleeps is the kiosk app's decision (Power.kt), and so is waking it when power returns. A hot battery
// (HOT_C and up) while no show holds the levels dims and slows the face the same way (P-5).
const HOT_C = 40, SAVE_FRAME_MS = 30;
const power = { battery: false, hot: false, save: false, low: false };
window.facePower = power;                          // read by a harness through devtools
// Whether a caption is on the face now: the operator's hint (operator.js) sits in the same strip, so
// it stays away while one shows rather than drawing over it (Face & HRI's review, 2026-10-22).
window.faceCaptionShowing = () => !!captionNow(performance.now());
function pollPower() {
  const K = window.OpenDubsKiosk;
  if (!K || typeof K.power !== "function") return;   // a browser, or an app older than 0.5.0
  let p = null, t = null;
  try { p = JSON.parse(K.power()); } catch (e) { return; }
  try { t = JSON.parse(K.thermal()); } catch (e) { /* no reading */ }
  const battery = p.battery_mode === true;
  if (battery !== power.battery) {
    const T = window.OpenDubsTracking;
    if (T) { if (battery) T.pause("battery"); else T.resume("battery").catch(() => {}); }
  }
  power.battery = battery; power.low = p.low === true;
  power.hot = !!(t && typeof t.battery_c === "number" && t.battery_c >= HOT_C && !levels.touched);
  power.save = power.battery || power.hot;
  levels.setScale(power.save ? 0.5 : 1);
}
setInterval(pollPower, 2000); pollPower();

// ---- picking up a deploy by itself ---------------------------------------------------------------
// The kiosk has no reload control, and turning tracking on only adds a module to the page already
// running, so a deployed fix to this page sat unused until something reloaded it (2026-10-16). Every
// 15 s the page asks the dashboard which version of its files it serves (face_door.py), and reloads
// when that changes. Never while a show is armed or running (a reload would cut it); it tries again.
(function followDeploys() {
  let seen = null;                                   // "version boot" as first seen
  async function busy() {
    if (typeof face.armedState === "function" && face.armedState(performance.now())) return true;
    try {
      const p = await (await fetch("/api/choreo/palette", { cache: "no-store" })).json();
      return !!p.running;
    } catch (e) { return false; }   // no runner: no show can be running
  }
  // A deploy copies the face's files and THEN restarts the dashboard, so a change is seen while the old
  // process is about to stop. A reload then lost its scripts in the gap and left a page with no voice and
  // no door (2026-10-19). Reload only once the new version holds 5 s later and the page's code answers.
  // The gap can outlast those 5 s (2026-10-20: the page reloaded into the old process, which stopped while
  // the wake word's model was downloading), so the dashboard's own process counts too: a restart is a
  // change, and the page reloads again once the new process holds. Two reloads in a deploy, the last clean.
  const key = (j) => (j && j.version ? `${j.version} ${j.boot || ""}` : null);
  async function settled(v) {
    await new Promise((r) => setTimeout(r, 5000));
    try {
      const again = key(await (await fetch("/api/face/version", { cache: "no-store" })).json());
      const code = await fetch("engine/main.js", { method: "HEAD", cache: "no-store" });
      return again === v && code.ok;
    } catch (e) { return false; }                    // still restarting: the next round tries again
  }
  async function check() {
    try {
      const r = await fetch("/api/face/version", { cache: "no-store" });
      const v = r.ok ? key(await r.json()) : null;               // not served by a dashboard: nothing to follow
      if (v && seen === null) seen = v;
      else if (v && v !== seen && !(await busy()) && (await settled(v))) { location.reload(); return; }
    } catch (e) { /* the dashboard is restarting during the deploy: try again next round */ }
    setTimeout(check, 15000);
  }
  check();
})();

// ---- a touch stops the head --------------------------------------------------------------------
// Any touch on the robot's tablet stops the head at once (backend neck_follow.touched): the moment a
// finger lands, and again every second it stays down. Only from the tablet itself (the kiosk app's
// bridge), never from a laptop that has this page open.
(function touchHoldsHead() {
  if (!window.OpenDubsKiosk) return;
  // A TOUCH THAT NEVER LIFTS IS NOT A PERSON (the user's decision, 2026-10-24): on the robot's USB power
  // the tablet registers phantom touches (charger noise), and each paused the head. A touch still pauses
  // at once, as before; but a pointer with no up or cancel within PHANTOM_MS is a phantom: its renewals
  // stop, and the robot is told so, with what was seen, to end the pause (unless a real touch is still
  // holding) and log it. Real taps, and the face's one-second hold, lift well inside the window.
  const PHANTOM_MS = 2000, PAGE = "face";
  const downs = new Map();                         // pointerId -> {at, info, timer}
  let last = -1e9, lastLift = -1e9, renew = null;
  const beacon = (q) => { try { navigator.sendBeacon("/api/head/touch" + q); } catch (e) {} };
  const say = () => { last = performance.now(); beacon(""); };
  const stopRenew = () => { if (!downs.size) { clearInterval(renew); renew = null; } };
  const phantom = (id) => {
    const d = downs.get(id); if (!d) return;
    downs.delete(id);
    const holding = downs.size > 0 || performance.now() - lastLift < 4000;   // a real touch still counts
    const q = new URLSearchParams({ phantom: "1", release: holding ? "0" : "1", page: PAGE,
                                    ms: String(Math.round(performance.now() - d.at)), ...d.info });
    beacon("?" + q.toString());
    stopRenew();
  };
  addEventListener("pointerdown", (e) => {
    const now = performance.now();
    if (now - last > 250) say();
    downs.set(e.pointerId, { at: now, timer: setTimeout(() => phantom(e.pointerId), PHANTOM_MS),
      info: { x: String(Math.round(e.clientX)), y: String(Math.round(e.clientY)), type: String(e.pointerType),
              pressure: String(e.pressure), w: String(Math.round(e.width)), h: String(Math.round(e.height)) } });
    if (!renew) renew = setInterval(() => { if (downs.size) say(); else stopRenew(); }, 1000);
  }, { capture: true, passive: true });
  const up = (e) => {
    const d = downs.get(e.pointerId); if (!d) return;
    clearTimeout(d.timer); downs.delete(e.pointerId); lastLift = performance.now();
    stopRenew();
  };
  addEventListener("pointerup", up, { capture: true, passive: true });
  addEventListener("pointercancel", up, { capture: true, passive: true });
})();

// ---- a face that has gone blank reloads itself ---------------------------------------------------
// On 2026-10-17 the tablet's face went BLACK under its overlays (the camera sign still drew) after
// running for a while, with the page otherwise alive, and a reload is what clears it. On 2026-10-20 it
// went black again after a tablet restart, with the frame loop, the voice and the kiosk heartbeat all
// alive, and this watchdog said nothing: it checked the eyes' GEOMETRY, which was fine, never the
// PIXELS. So it now reads them back: where the eyes should be lit, is anything there?
// Every 2 s: the canvas's context lost, no frame drawn for 5 s while the page is visible, the eyes not
// drawable, or the eyes' pixels all background. Three bad checks in a row (6 s) and the page says why to
// the dashboard and reloads, never while a show is armed or running. At most once a minute.
// The same 2 s sends the face's render health to the dashboard (/api/head/face_health): numbers only.
const bgRgb = (() => {
  const c = document.createElement("canvas"); c.width = c.height = 1;
  const x = c.getContext("2d"); x.fillStyle = BG; x.fillRect(0, 0, 1, 1);
  return x.getImageData(0, 0, 1, 1).data;
})();
// Read through a small canvas of its own: getImageData on the face's own context, every 2 s, can make the
// browser move that canvas off the GPU for good (its read-frequently heuristic).
const probe = document.createElement("canvas"); probe.width = probe.height = 48;
const pctx = probe.getContext("2d", { willReadFrequently: true });
// "ok" (an eye is lit), "blank" (every eye that should be lit is background), "skip" (nothing to judge:
// a hidden page, a full-face picture, eyes shut or dark on purpose), or "error: ...".
function eyePixels() {
  if (document.visibilityState !== "visible") return "skip";
  const W = canvas.width, H = canvas.height;
  if (!W || !H) return "blank";
  if (health.fullFace || !health.eyesOk) return "skip";
  try {
    const s = Math.min(W / DESIGN_W, H / DESIGN_H);
    const turn = neck ? (neck.pan - 90) * 4 : 0;
    const ox = (W - DESIGN_W * s) / 2 - turn * s, oy = (H - DESIGN_H * s) / 2;
    let judged = 0;
    for (const e of face.eyes()) {
      if (Math.max(...e.rgb) < 40 || e.lid > 0.85) continue;          // dark or shut on purpose
      const x0 = Math.max(0, Math.floor(ox + (e.cx - e.w / 2) * s)), y0 = Math.max(0, Math.floor(oy + (e.cy - e.h / 2) * s));
      const x1 = Math.min(W, Math.ceil(ox + (e.cx + e.w / 2) * s)), y1 = Math.min(H, Math.ceil(oy + (e.cy + e.h / 2) * s));
      if (x1 - x0 < 2 || y1 - y0 < 2) continue;
      judged++;
      pctx.imageSmoothingEnabled = false;                              // samples, not a blur
      pctx.drawImage(canvas, x0, y0, x1 - x0, y1 - y0, 0, 0, 48, 48);
      const px = pctx.getImageData(0, 0, 48, 48).data;
      for (let i = 0; i < px.length; i += 4) {
        if (Math.abs(px[i] - bgRgb[0]) + Math.abs(px[i + 1] - bgRgb[1]) + Math.abs(px[i + 2] - bgRgb[2]) > 30) return "ok";
      }
    }
    return judged ? "blank" : "skip";
  } catch (e) { return "error: " + String(e && e.message || e).slice(0, 120); }
}
(function watchFace() {
  let bad = 0, pixels = "skip", lastFrames = 0;
  const why = () => {
    if (health.lost) return "the canvas context was lost";
    if (document.visibilityState === "visible" && performance.now() - health.drawnAt > 5000) return "no frame drawn for 5 s";
    if (!health.eyesOk) return "the eyes are not drawable (geometry not finite, or no size)";
    if (pixels === "blank") return "the eyes' pixels are all background";
    return null;
  };
  let lastReload = null;
  try { lastReload = JSON.parse(sessionStorage.getItem("face-reloaded-why") || "null"); } catch (e) {}
  setInterval(async () => {
    pixels = eyePixels();
    const r = why();
    bad = r ? bad + 1 : 0;
    if (onRobot) {
      const attrs = typeof ctx.getContextAttributes === "function" ? ctx.getContextAttributes() : {};
      const report = {
        at: Date.now(), fps: (health.frames - lastFrames) / 2,
        frame_age_ms: Math.round(performance.now() - health.drawnAt), context_lost: health.lost,
        pixels, eyes_ok: health.eyesOk, bad: r, bad_checks: bad, visible: document.visibilityState,
        canvas: { w: canvas.width, h: canvas.height, css_w: innerWidth, css_h: innerHeight,
                  dpr: window.devicePixelRatio || 1, desynchronized: attrs.desynchronized ?? null },
        refits: health.refits, errors: health.errors, last_error: health.lastError, last_reload: lastReload,
      };
      try { navigator.sendBeacon && navigator.sendBeacon("/api/head/face_health", JSON.stringify(report)); } catch (e) {}
    }
    lastFrames = health.frames;
    if (bad < 3) return;
    let showOn = typeof face.armedState === "function" && !!face.armedState(performance.now());
    try { showOn = showOn || !!(await (await fetch("/api/choreo/palette", { cache: "no-store" })).json()).running; } catch (e) {}
    if (showOn) return;
    let last = 0;
    try { last = +sessionStorage.getItem("face-reloaded-at") || 0; } catch (e) {}
    if (Date.now() - last < 60000) return;
    try {
      sessionStorage.setItem("face-reloaded-at", String(Date.now()));
      sessionStorage.setItem("face-reloaded-why", JSON.stringify({ why: r, at: Date.now() }));
    } catch (e) {}
    try { if (door && door.readyState === WebSocket.OPEN) door.send(JSON.stringify({ face_health: r })); } catch (e) {}
    setTimeout(() => location.reload(), 200);
  }, 2000);
})();

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
// Talking to the robot (voice.js): only where there is a robot to talk to, or a desk door that asks for it.
// Retried, not dropped: a failed import (the dashboard restarting as the page loaded) left the face deaf
// for good (2026-10-19). After a minute of failures, the page reloads.
const importRetry = (path, tries = 12) => import(path).catch((e) =>
  tries > 1 ? new Promise((r) => setTimeout(r, 5000)).then(() => importRetry(`${path}?retry=${tries}`, tries - 1))
            : (setTimeout(() => location.reload(), 1000), Promise.reject(e)));
if (onRobot || q.get("voice") === "1") importRetry("./voice.js").then((m) => m.start({ face })).catch(() => {});
import("./airplay.js").then((m) => m.start({ onRobot })).catch(() => {});   // AirPlay, live (on the robot)
if (q.get("track") === "1") tracking = true;
if (q.get("track") === "1") import("./track.js").then((m) => m.start(face, stats)).catch((e) => { stats.track = "unavailable: " + e.message; });
