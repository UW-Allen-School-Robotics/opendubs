// The face page: the face model, a skin, the layers, the door, and — for measuring — a frame-time
// readout. Query parameters (all optional):
//   door=ws://127.0.0.1:8766   the door's websocket (door_dev.py on a laptop; the app on the tablet)
//   skin=eyes|ring             which skin draws the eyes
//   track=1                    look at a person through the camera (track.js)
//   hud=1                      frame-time readout
//   video=<url>                a media layer, for measuring video decode beside the face ('v' cuts)
//   audio=1                    earcons audible
//   state=SAFE_IDLE            start in a state (a twin with no door otherwise shows NO LINK)
// Keys (twin conveniences, never a robot path): 1-7 state, e emote, a auto, s skin, t theme, v cut.

import { Face, DESIGN_W, DESIGN_H } from "./face.js";
import { STATES, EMOTES } from "./vocab.js";
import { handle, Earcons } from "./protocol.js";
import * as eyes from "./skins/eyes.js";
import * as ring from "./skins/ring.js";

const SKINS = { eyes, ring };
const q = new URLSearchParams(location.search);
const face = new Face();
const audio = new Earcons(q.get("audio") === "1");
let skin = SKINS[q.get("skin")] || eyes;
let neck = null;                                   // {pan, tilt, owner} from a door, if it has a neck

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
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
  const s = Math.min(W / DESIGN_W, H / DESIGN_H);
  // A twin with a neck: show the head's turn as a shift of the whole face (simulated, and labelled).
  const turn = neck ? (neck.pan - 90) * 4 : 0;
  ctx.setTransform(s, 0, 0, s, (W - DESIGN_W * s) / 2 - turn * s, (H - DESIGN_H * s) / 2);
  for (const e of face.eyes()) skin.draw(ctx, e, now / 1000);
  const text = face.quoteText();
  if (text) { ctx.fillStyle = "#5a5a5a"; ctx.font = "24px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillText(text, DESIGN_W / 2, 672); }
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
      `  draw p95 ${stats.drawP95.toFixed(2)} ms  ${canvas.width}x${canvas.height}  skin ${skin.name}` +
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
    if (msg.line !== undefined) ws.send(JSON.stringify({ id: msg.id, reply: handle(face, audio, msg.line, performance.now()) }));
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
if (doorUrl) connect();

// ---- twin conveniences ------------------------------------------------------------------------
const names = Object.keys(EMOTES);
let ei = 0, si = 0;
addEventListener("keydown", (k) => {
  const now = performance.now();
  if (k.key >= "1" && k.key <= "7") face.setState(STATES[+k.key - 1][0], now);
  else if (k.key === "e") face.setEmote(names[ei++ % names.length], now);
  else if (k.key === "a") face.setEmote("AUTO", now);
  else if (k.key === "s") skin = Object.values(SKINS)[++si % 2];
  else if (k.key === "t") face.setTheme(face.theme === "plain" ? "halloween" : "plain");
  else if (k.key === "v") cut(video.style.visibility === "visible" ? "face" : "media");
});
// A twin with no door has no robot: say so on the face, as the head does (NO LINK), and keep the
// state fresh so it does not go stale while someone is looking at skins.
if (q.get("state")) face.setState(q.get("state"), performance.now());   // e.g. state=SAFE_IDLE
if (!doorUrl) setInterval(() => { if (face.state >= 0) face.stateAt = performance.now(); }, 1000);

if (q.get("track") === "1") import("./track.js").then((m) => m.start(face, stats)).catch((e) => { stats.track = "unavailable: " + e.message; });
