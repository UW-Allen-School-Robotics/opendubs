/* Show composer — the bones of a timeline editor. Vanilla JS, no build step, no CDN.
 *
 * PREVIEW ONLY. Nothing in this file can reach the robot: it talks to the composer server, which
 * reads and writes show files and runs cue.py. The "preview" is the state readout drawn below.
 *
 * Data flow, deliberately one-way: the SHOW TEXT is the state. Every edit is sent to the server
 * as an operation on that text (api/edit), which applies it surgically and re-runs the validator,
 * and the editor redraws from the RESOLVED TIMELINE that comes back (cue.py export). The browser
 * never re-implements the validator; where it resolves a time itself (dragging), it is only to
 * draw, and the server's answer replaces it.
 *
 * Layout state (zoom, snap, mute) lives in localStorage and never in the show (standing rule 7).
 */
"use strict";

const SLOTS = ["ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT", "DANCE"];
const LANES = ["CLIPS", ...SLOTS];
const $ = (id) => document.getElementById(id);

const S = {
  info: null, path: null, text: "", savedText: "", data: null, undo: [],
  selected: null,           // authored cue index
  t: 0, pps: 60, snap: "beat", muted: new Set(),
  playing: false, metro: false, taps: [], tapBpm: null,
  audio: { ctx: null, buffer: null, peaks: null, src: null, gain: null, startedCtx: 0, startedT: 0,
           nextClick: 0, file: null },
};

/* ---------- small helpers ---------- */
const store = {
  get(k, d) { try { const v = localStorage.getItem("composer." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("composer." + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};
function toast(msg, ms = 3200) {
  const el = $("toast"); el.textContent = msg; el.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (el.hidden = true), ms);
}
/* Touch has no hover: a long press on anything with hover text shows that text as a toast. */
(function longPressHelp() {
  let timer = null, x = 0, y = 0;
  const cancel = () => { clearTimeout(timer); timer = null; };
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    const el = e.target.closest("[title]"); if (!el || !el.title) return;
    x = e.clientX; y = e.clientY;
    timer = setTimeout(() => toast(el.title, 4500), 550);
  });
  document.addEventListener("pointermove", (e) => { if (timer && Math.hypot(e.clientX - x, e.clientY - y) > 10) cancel(); });
  document.addEventListener("pointerup", cancel);
  document.addEventListener("pointercancel", cancel);
})();
/* A request that gets no answer at all (the composer stopped, restarted, or the network dropped)
 * reaches here as a bare TypeError — "Load failed" in Safari, "Failed to fetch" in Chrome. Say what
 * that means instead. */
function noAnswer(e) {
  return new Error(`the composer at ${location.host} did not answer (the browser said: ${e.message}). `
    + "Is it still running? Reload this page once it is — unsaved edits are in the YAML view.");
}
async function send(path, init) {
  try { return await fetch(path, init); } catch (e) { throw noAnswer(e); }
}
async function api(path, body) {
  const r = await send(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw new Error(j.error || j.detail || `HTTP ${r.status}`);
  return j;
}
const tl = () => S.data && S.data.timeline;
const music = () => tl() && tl().music;
const round = (x, n) => { const p = 10 ** n; return Math.round(x * p) / p; };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- time: the grid ---------- */
function beatOf(t) { const m = music(); return m ? (t - m.offset_s) / m.beat_s + 1 : null; }
function barLabel(t) {
  const m = music(); if (!m) return "";
  const b = beatOf(t); if (b < 1) return "pre-roll";
  const bi = Math.floor(b - 1 + 1e-9);
  return `bar ${Math.floor(bi / m.beats_per_bar) + 1}.${(bi % m.beats_per_bar) + 1}`;
}
function snapT(t) {
  const m = music();
  if (S.snap === "off") return t;
  if (!m) return Math.round(t * 4) / 4;            // no grid: quarter seconds
  const d = { bar: m.beat_s * m.beats_per_bar, beat: m.beat_s, half: m.beat_s / 2, quarter: m.beat_s / 4 }[S.snap];
  return Math.max(0, m.offset_s + Math.round((t - m.offset_s) / d) * d);
}
/* The first grid point at or after t: a time the validator moved later must never snap back. */
function snapUp(t) {
  const m = music();
  if (S.snap === "off") return t;
  if (!m) return Math.ceil(t * 4 - 1e-6) / 4;
  const d = { bar: m.beat_s * m.beats_per_bar, beat: m.beat_s, half: m.beat_s / 2, quarter: m.beat_s / 4 }[S.snap];
  return Math.max(0, m.offset_s + Math.ceil((t - m.offset_s) / d - 1e-6) * d);
}
/* A cue with its time replaced, keeping the author's key order. */
function withTime(c, t) {
  const tm = newCueTime(t), out = {};
  let placed = false;
  for (const [k, v] of Object.entries(c)) {
    if (["t", "beat", "bar"].includes(k)) { if (!placed) { Object.assign(out, tm); placed = true; } }
    else out[k] = v;
  }
  return placed ? out : { ...tm, ...out };
}
/* Resolve an authored time for DRAWING only (the server is authoritative). */
function authoredT(c) {
  const m = music();
  if ("t" in c) return +c.t;
  if (!m) return null;
  if ("beat" in c) return m.offset_s + (c.beat - 1) * m.beat_s;
  if ("bar" in c) {
    const [b, bb] = String(c.bar).split(".").map(Number);
    return m.offset_s + ((b - 1) * m.beats_per_bar + (bb || 1) - 1) * m.beat_s;
  }
  return null;
}
/* Write a new time back in the SAME form the author used (choreo-format-v1.md 3): a t cue stays
 * a t cue even when snapped to a beat; a bar cue stays a bar cue while it lands on a whole beat. */
function retimed(c, newT) {
  const out = { ...c };
  const m = music();
  if ("t" in c || !m) { delete out.beat; delete out.bar; out.t = round(newT, 3); return out; }
  const b = round(beatOf(newT), 4);
  if ("bar" in c && Math.abs(b - Math.round(b)) < 1e-6 && b >= 1) {
    const bi = Math.round(b) - 1;
    out.bar = `${Math.floor(bi / m.beats_per_bar) + 1}.${(bi % m.beats_per_bar) + 1}`;
    return out;
  }
  delete out.bar; out.beat = b; return out;
}
function newCueTime(t) {
  const m = music();
  if (m && S.snap !== "off") return { beat: round(beatOf(t), 4) };
  return { t: round(t, 3) };
}

/* ---------- loading and editing ---------- */
async function loadShow(path) {
  if (S.text !== S.savedText && !confirm("Discard unsaved changes?")) { $("show").value = S.path; return; }
  const d = await api("api/show?path=" + encodeURIComponent(path));
  S.path = path; S.undo = []; S.selected = null; S.savedText = d.text;
  S.muted = new Set(store.get("muted." + path, []));
  stop(); S.t = 0; postPlayhead(true);
  setData(d);
  store.set("lastShow", path);
  await loadAudio();
}
function setData(d) {
  S.data = d; S.text = d.text;
  render();
}
async function edit(edits, label) {
  try {
    const d = await api("api/edit", { path: S.path, text: S.text, edits });
    S.undo.push(S.text);
    setData(d);
    if (label) toast(label, 1600);
  } catch (e) { toast(e.message, 5000); }
}
async function undo() {
  if (!S.undo.length) return;
  const text = S.undo.pop();
  setData(await api("api/analyse", { path: S.path, text }));
}
async function save() {
  try {
    const d = await api("api/save", { path: S.path, text: S.text });
    S.savedText = d.text; setData(d); toast("saved " + S.path, 1600);
    if ($("musicDlg").open) { await refreshSongs(); renderMusicDlg(); }   /* who uses which song changed */
  } catch (e) { toast(e.message, 5000); }
}

/* ---------- audio: Web Audio, and the playhead is driven from its clock ---------- */
function ctx() {
  if (!S.audio.ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    S.audio.ctx = new AC();
    S.audio.gain = S.audio.ctx.createGain();
    S.audio.gain.connect(S.audio.ctx.destination);
  }
  return S.audio.ctx;
}
async function loadAudio() {
  const m = music();
  S.audio.buffer = null; S.audio.peaks = null;
  if (!m || !(m.file || m.song)) {
    $("waveMsg").textContent = m ? "no music in this show — the grid still works (♫ music adds a song)" : "no music: block — times are in seconds (tap tempo or ♫ music adds one)";
    drawWave(); return;
  }
  /* a library song (music: song:) or a file next to the show (music: file:) */
  let url, label;
  if (m.song) {
    url = "library/song/" + encodeURIComponent(m.song); label = "song " + m.song;
  } else {
    const dir = S.path.includes("/") ? S.path.slice(0, S.path.lastIndexOf("/") + 1) : "";
    const rel = dir + m.file;
    url = "audio/" + rel.split("/").map(encodeURIComponent).join("/"); label = m.file;
  }
  S.audio.file = url;
  $("waveMsg").textContent = "loading " + label + "…";
  try {
    const r = await send(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "HTTP " + r.status);
    const buf = await r.arrayBuffer();
    S.audio.buffer = await new Promise((ok, no) => ctx().decodeAudioData(buf, ok, no));
    S.audio.peaks = peaks(S.audio.buffer, 200);
    $("waveMsg").textContent = "";
  } catch (e) {
    $("waveMsg").textContent = `no audio (${label}): ${e.message || e} — the grid still works`;
  }
  render();
}
function peaks(buffer, perSec) {
  const ch = buffer.getChannelData(0), step = Math.max(1, Math.floor(buffer.sampleRate / perSec));
  const n = Math.ceil(ch.length / step), out = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) {
    let lo = 1, hi = -1;
    for (let k = i * step, e = Math.min(ch.length, k + step); k < e; k++) { const v = ch[k]; if (v < lo) lo = v; if (v > hi) hi = v; }
    out[2 * i] = lo; out[2 * i + 1] = hi;
  }
  return { perSec, data: out };
}
/* What is HEARD now, not what was scheduled: subtract the output latency the browser reports.
 * (The same correction the live sync design needs on the tablet — choreo-teach-and-sync.md.) */
function audioNow() {
  const a = S.audio, c = a.ctx;
  const lat = (c.outputLatency || 0) + (c.baseLatency || 0);
  return Math.max(a.startedT, a.startedT + (c.currentTime - a.startedCtx) - lat);
}
/* The twin feed (workplan 2.2): tell the server where the playhead is, so a MuJoCo or face twin
 * can follow. Time and play state only, fire-and-forget; a twin that is not running costs a
 * local POST every few hundred ms and nothing else. */
function postPlayhead(force) {
  const now = performance.now();
  if (!force && now - (postPlayhead._at || 0) < 250) return;
  postPlayhead._at = now;
  fetch("api/playhead", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ t: S.t, playing: S.playing }) }).catch(() => {});
}
function play() {
  const c = ctx();
  c.resume();
  const a = S.audio;
  a.startedCtx = c.currentTime + 0.05; a.startedT = S.t;
  a.gain.gain.value = S.muted.has("MUSIC") ? 0 : 1;
  if (a.buffer && S.t < a.buffer.duration) {
    a.src = c.createBufferSource(); a.src.buffer = a.buffer; a.src.connect(a.gain);
    a.src.start(a.startedCtx, S.t);
  }
  a.nextClick = Math.ceil(beatOf(S.t) || 0);
  S.playing = true; $("play").textContent = "⏸";
  postPlayhead(true);
  requestAnimationFrame(frame);
}
function stop() {
  const a = S.audio;
  if (a.src) { try { a.src.stop(); } catch (e) { /* already stopped */ } a.src = null; }
  S.playing = false; $("play").textContent = "▶";
  postPlayhead(true);
}
function scheduleClicks() {
  /* a lookahead scheduler: clicks are placed on the audio clock, so they cannot drift from it */
  const m = music(), a = S.audio, c = a.ctx;
  if (!S.metro || !m) return;
  const horizon = c.currentTime + 0.2;
  for (;;) {
    const tBeat = m.offset_s + (a.nextClick - 1) * m.beat_s;
    const when = a.startedCtx + (tBeat - a.startedT);
    if (when > horizon) break;
    if (when >= c.currentTime && a.nextClick >= 1) {
      const o = c.createOscillator(), g = c.createGain();
      const down = (a.nextClick - 1) % m.beats_per_bar === 0;
      o.frequency.value = down ? 1760 : 1100;
      g.gain.setValueAtTime(0.25, when); g.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
      o.connect(g); g.connect(c.destination); o.start(when); o.stop(when + 0.06);
    }
    a.nextClick++;
  }
}
function frame() {
  if (!S.playing) return;
  S.t = audioNow();
  scheduleClicks();
  const end = Math.max(tl() ? tl().duration_s : 0, S.audio.buffer ? S.audio.buffer.duration : 0) + 1;
  if (S.t > end) { stop(); }
  drawPlayhead(true);
  renderPreview();
  postPlayhead(false);
  requestAnimationFrame(frame);
}
function seek(t) {
  const was = S.playing; stop();
  S.t = Math.max(0, t); drawPlayhead(false); renderPreview();
  if (was) play(); else postPlayhead(true);
}

/* ---------- rendering ---------- */
function width() {
  const d = Math.max(tl() ? tl().duration_s : 0, S.audio.buffer ? S.audio.buffer.duration : 0, 20) + 4;
  return Math.min(30000, Math.ceil(d * S.pps));
}
function render() {
  const d = S.data;
  $("save").classList.toggle("dirty", S.text !== S.savedText);
  $("undo").disabled = !S.undo.length;
  $("fixture").hidden = !(d && d.info && d.info.limits_are_test_fixture);
  $("limitsName").textContent = d && d.info ? "· limits: " + d.info.limits : "";
  const st = $("status");
  if (!d) return;
  if (d.parse_error) { st.className = "chip bad"; st.textContent = "does not parse"; }
  else if (tl().valid) {
    const nw = (tl().warnings || []).length;
    st.className = nw ? "chip warn" : "chip ok";
    st.textContent = nw ? `valid · ${nw} warning${nw > 1 ? "s" : ""}` : "valid";
  }
  else { st.className = "chip bad"; st.textContent = `${tl().problems.length} problem${tl().problems.length > 1 ? "s" : ""}`; }
  renderHeads(); renderLanes(); drawRuler(); drawWave(); drawPlayhead(false);
  renderProblems(); renderInspector(); renderPreview();
  if (!$("yamlDrawer").hidden) $("yamlText").value = S.text;
}
function declared() { return new Set(tl() ? tl().slots : []); }
function renderHeads() {
  const h = $("heads"), dec = declared();
  const muteBtn = (k) => `<button class="mute ${S.muted.has(k) ? "on" : ""}" data-mute="${k}" title="Mute in the preview (never saved to the show)">M</button>`;
  let html = `<div class="head ruler"><span class="name dim">${music() ? `${music().bpm} BPM · ${music().beats_per_bar}/4` : "seconds"}</span></div>`;
  html += `<div class="head music ${S.muted.has("MUSIC") ? "muted" : ""}"><span class="name">MUSIC</span>${muteBtn("MUSIC")}</div>`;
  for (const lane of LANES) {
    const und = lane !== "CLIPS" && !dec.has(lane);
    const cls = ["head", und ? "undeclared" : "", S.muted.has(lane) ? "muted" : ""].join(" ");
    let btns = "";
    if (lane !== "CLIPS") btns += `<button data-add="${lane}" title="Add a ${lane} cue at the playhead">+</button>`;
    if (lane === "ARM_L" || lane === "ARM_R") btns += `<button data-teach="${lane}" title="Capture this arm's measured pose as a keyframe at the playhead (FAKE source in this build)">⦿</button>`;
    if (lane !== "CLIPS") btns += muteBtn(lane);
    html += `<div class="${cls}" title="${und ? "not declared in slots: — a cue here will fail validation" : ""}"><span class="name">${lane}</span>${btns}</div>`;
  }
  h.innerHTML = html;
}
function gridStyle(el) {
  const m = music(), pps = S.pps;
  if (m) {
    const beatW = m.beat_s * pps, barW = beatW * m.beats_per_bar, off = m.offset_s * pps;
    el.style.backgroundSize = `${barW}px 100%, ${beatW}px 100%`;
    el.style.backgroundPosition = `${off}px 0, ${off}px 0`;
  } else {
    el.style.backgroundSize = `${5 * pps}px 100%, ${pps}px 100%`;
    el.style.backgroundPosition = "0 0, 0 0";
  }
}
function byCue(list) {
  const m = new Map();
  for (const p of (list || [])) if (p.cue !== null && p.cue !== undefined) {
    if (!m.has(p.cue)) m.set(p.cue, []); m.get(p.cue).push(p.msg);
  }
  return m;
}
function problemsByCue() { return byCue(tl() ? tl().problems : []); }
// Warnings point at a cue like a problem but never make the show invalid (e.g. a head move that
// cannot finish before the next one).
function warningsByCue() { return byCue(tl() ? tl().warnings : []); }
function label(ev) {
  const c = ev.cue;
  switch (ev.slot) {
    case "GRIP_L": case "GRIP_R": return c.do || `open ${c.open}`;
    case "HEAD": return [c.pan !== undefined ? `p${Math.round(c.pan)}` : "", c.tilt !== undefined ? `t${Math.round(c.tilt)}` : ""].join(" ");
    case "LIFT": return c.do + (c.secs ? ` ${c.secs}s` : "");
    case "SOUND": return (c.sound || c.do) + (ev.output ? ` → ${ev.output}` : "");
    case "FACE": return c.do === "theme" ? `theme ${c.name}` : (c.name || c.do);
    case "DANCE": return c.move + (c.amp !== undefined && c.amp !== 1 ? ` ×${c.amp}` : "");
    case "LIGHT": return c.do === "color" ? "■" : c.do;
    default: return "";
  }
}
function renderLanes() {
  const W = width(), pps = S.pps, lanes = $("lanes"), dec = declared(), bad = problemsByCue(), warn = warningsByCue();
  $("canvas").style.width = W + "px";
  const parts = [];
  for (const lane of LANES) {
    const und = lane !== "CLIPS" && !dec.has(lane);
    parts.push(`<div class="row lane grid ${und ? "undeclared" : ""} ${S.muted.has(lane) ? "muted" : ""}" data-lane="${lane}">`);
    if (lane === "CLIPS") {
      for (const p of placements()) {
        const cls = ["place", S.selected === p.index ? "sel" : "", bad.has(p.index) ? "bad" : warn.has(p.index) ? "warn" : ""].join(" ");
        parts.push(`<div class="${cls}" data-cue="${p.index}" style="left:${p.start * pps}px;width:${Math.max(20, (p.end - p.start) * pps)}px" title="${esc(p.title)}">${esc(p.text)}</div>`);
      }
    } else if (tl() && tl().tracks[lane]) {
      for (const ev of tl().tracks[lane]) {
        const i = ev.src.index, fc = ev.src.clip ? "fromClip" : "";
        const sel = S.selected === i ? "sel" : "", b = bad.has(i) ? "bad" : warn.has(i) ? "warn" : "";
        const tip = esc(JSON.stringify(ev.cue) + `  @ ${ev.t.toFixed(3)} s (${ev.src.time})` + (ev.src.clip ? `  clip ${ev.src.clip} #${ev.src.instance + 1}` : ""));
        if (ev.kind === "keyframe") {
          parts.push(`<div class="seg ${b}" style="left:${ev.start * pps}px;width:${Math.max(0, ev.t - ev.start) * pps}px"></div>`);
          if (ev.end > ev.t) parts.push(`<div class="hold" style="left:${ev.t * pps}px;width:${(ev.end - ev.t) * pps}px"></div>`);
          parts.push(`<div class="kf ${fc} ${sel} ${b}" data-cue="${i}" data-t="${ev.t}" style="left:${ev.t * pps}px" title="${tip}"></div>`);
        } else {
          const sw = ev.slot === "LIGHT" && ev.cue.rgb ? `color:rgb(${ev.cue.rgb.join(",")})` : "";
          parts.push(`<div class="blk ${fc} ${sel} ${b}" data-cue="${i}" data-t="${ev.t}" style="left:${ev.start * pps}px;width:${Math.max(16, (ev.end - ev.start) * pps)}px;${sw}" title="${tip}">${esc(label(ev))}</div>`);
        }
      }
    }
    parts.push("</div>");
  }
  lanes.innerHTML = parts.join("");
  for (const el of lanes.querySelectorAll(".lane")) gridStyle(el);
}
/* clip placements, drawn from the EXPANDED events that came back from the validator */
function placements() {
  const out = new Map(), a = (S.data && S.data.authored) || [];
  if (!tl()) return [];
  for (const evs of Object.values(tl().tracks)) for (const ev of evs) {
    if (!ev.src.clip) continue;
    const p = out.get(ev.src.index) || { index: ev.src.index, start: Infinity, end: -Infinity };
    p.start = Math.min(p.start, ev.kind === "keyframe" ? ev.t : ev.start); p.end = Math.max(p.end, ev.end);
    out.set(ev.src.index, p);
  }
  for (const p of out.values()) {
    const c = a[p.index] || {};
    const at = authoredT(c); if (at !== null) p.start = Math.min(p.start, at);
    const dials = [c.repeat > 1 ? `×${c.repeat}` : "", c.strength !== undefined ? `str ${c.strength}` : "", c.time_scale !== undefined ? `ts ${c.time_scale}` : ""].filter(Boolean).join(" ");
    p.text = `${c.clip} ${dials}`; p.title = JSON.stringify(c);
  }
  return [...out.values()];
}
function drawRuler() {
  const r = $("ruler"), m = music(), pps = S.pps, W = width();
  const parts = [];
  if (m) {
    const barS = m.beat_s * m.beats_per_bar;
    const every = Math.max(1, Math.ceil(40 / (barS * pps)));
    for (let b = 1; ; b++) {
      const t = m.offset_s + (b - 1) * barS; if (t * pps > W) break;
      if ((b - 1) % every === 0) parts.push(`<div class="rulerTick" style="left:${t * pps}px">${b}</div>`);
    }
  } else {
    const every = Math.max(1, Math.ceil(50 / pps));
    for (let s = 0; s * pps < W; s += every) parts.push(`<div class="rulerTick" style="left:${s * pps}px">${s}s</div>`);
  }
  for (const mk of (tl() ? tl().markers : [])) parts.push(`<div class="marker" style="left:${mk.t * pps}px">⚑ ${esc(mk.name)}</div>`);
  r.innerHTML = parts.join("");
}
function drawWave() {
  const cv = $("wave"), W = width(), H = 64, pk = S.audio.peaks;
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.min(32767, Math.floor(W * dpr)); cv.height = H * dpr; cv.style.width = W + "px";
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  if (!pk) return;
  g.fillStyle = S.muted.has("MUSIC") ? "#2a3644" : "#3d7fb8";
  const n = pk.data.length / 2;
  for (let x = 0; x < W; x++) {
    const i0 = Math.floor((x / S.pps) * pk.perSec), i1 = Math.max(i0 + 1, Math.floor(((x + 1) / S.pps) * pk.perSec));
    if (i0 >= n) break;
    let lo = 1, hi = -1;
    for (let i = i0; i < Math.min(n, i1); i++) { lo = Math.min(lo, pk.data[2 * i]); hi = Math.max(hi, pk.data[2 * i + 1]); }
    g.fillRect(x, H / 2 - hi * (H / 2 - 2), 1, Math.max(1, (hi - lo) * (H / 2 - 2)));
  }
}
function drawPlayhead(follow) {
  const x = S.t * S.pps;
  $("playhead").style.left = x + "px";
  const sc = $("scroller");
  if (follow && (x > sc.scrollLeft + sc.clientWidth * 0.85 || x < sc.scrollLeft)) sc.scrollLeft = x - sc.clientWidth * 0.2;
  const b = barLabel(S.t);
  $("clock").textContent = `${S.t.toFixed(3)} s${b ? " · " + b : ""}`;
}

/* ---------- preview: per-slot state at the playhead ---------- */
function armAt(evs, t) {
  let prev = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0 };
  for (const kf of evs) {
    if (t < kf.start) return { pose: prev, state: "holding" };
    if (t <= kf.t) {
      const f = kf.t > kf.start ? (t - kf.start) / (kf.t - kf.start) : 1, pose = {};
      for (let j = 1; j <= 7; j++) pose[j] = prev[j] + (kf.pose[j] - prev[j]) * f;
      return { pose, state: "moving", to: kf };
    }
    prev = kf.pose;
  }
  return { pose: prev, state: "holding" };
}
function lastAt(evs, t) { let l = null; for (const e of evs) { if (e.t <= t + 1e-9) l = e; } return l; }
function renderPreview() {
  const T = tl(), t = S.t, out = [];
  $("previewT").textContent = `${t.toFixed(2)} s`;
  if (!T) { $("preview").innerHTML = ""; return; }
  const row = (k, v) => out.push(`<span class="k">${k}</span><span>${v}</span>`);
  const muted = (k) => S.muted.has(k) ? '<span class="dim">muted</span>' : null;
  for (const slot of SLOTS) {
    if (!T.tracks[slot]) continue;
    const evs = T.tracks[slot];
    if (muted(slot)) { row(slot, muted(slot)); continue; }
    if (slot === "ARM_L" || slot === "ARM_R") {
      const a = armAt(evs, t);
      const bars = [1, 2, 3, 4, 5, 6, 7].map((j) => {
        const v = a.pose[j], w = Math.min(50, Math.abs(v) / 180 * 50);
        return `<div class="jb" title="J${j} (outward sense)"><i style="left:${v >= 0 ? 50 : 50 - w}%;width:${w}%"></i><span>${j}:${v.toFixed(0)}</span></div>`;
      }).join("");
      row(slot, `<span class="${a.state}">${a.state}${a.to ? ` → ${a.to.t.toFixed(2)} s` : ""}</span><div class="joints">${bars}</div>`);
    } else if (slot === "HEAD") {
      let pan = null, tilt = null;
      for (const e of evs) if (e.t <= t + 1e-9) { if (e.cue.pan !== undefined) pan = e.cue.pan; if (e.cue.tilt !== undefined) tilt = e.cue.tilt; }
      row(slot, `<span title="Commanded target. The head turns toward it at 70 °/s per axis (the Tab5 firmware's slew limit), so it may still be travelling">pan ${pan === null ? "—" : pan.toFixed(0)}° · tilt ${tilt === null ? "—" : tilt.toFixed(0)}°</span>`);
    } else if (slot === "LIFT") {
      let net = 0, active = null;
      for (const e of evs) {
        if (e.start > t) break;
        const run = Math.min(t, e.end) - e.start;
        if (e.cue.do === "up") net += Math.max(0, Math.min(run, e.cue.secs));
        if (e.cue.do === "down") net = Math.max(0, net - Math.max(0, Math.min(run, e.cue.secs)));
        if (t >= e.start && t < e.start + (e.cue.secs || 0)) active = e;
      }
      row(slot, (active ? `<span class="moving">${active.cue.do.toUpperCase()} ${(t - active.start).toFixed(1)} of ${active.cue.secs} s</span>` : '<span class="holding">relays off</span>')
        + ` <span class="dim" title="Net time the lift has been driven up. Height per second is unmeasured, and a descent is stopped by the pinch guard near the bottom">· up ${net.toFixed(1)} s</span>`);
    } else if (slot === "SOUND") {
      const e = lastAt(evs, t);
      const on = e && t < e.end;
      row(slot, e ? `${on ? "♪ " : ""}${e.cue.sound || e.cue.do}${e.output ? ` <span class="dim">on the ${e.output}</span>` : ""}${on ? "" : ' <span class="dim">(done)</span>'}` : '<span class="dim">—</span>');
    } else if (slot === "LIGHT") {
      const e = lastAt(evs, t);
      row(slot, e && e.cue.do === "color" ? `<span class="swatch" style="background:rgb(${e.cue.rgb.join(",")});opacity:${(e.cue.br ?? 100) / 100}"></span> ${e.cue.rgb.join(", ")} @ ${e.cue.br ?? 100}%` : '<span class="dim">state colour (no show colour)</span>');
    } else if (slot === "FACE") {
      const e = lastAt(evs.filter((x) => x.cue.do !== "theme"), t);
      const th = lastAt(evs.filter((x) => x.cue.do === "theme"), t);
      row(slot, (e ? e.cue.name : '<span class="dim">face\'s own</span>')
        + (th ? ` <span class="dim" title="A theme stays after the show; an emote is handed back">· theme ${esc(th.cue.name)}</span>` : ""));
    } else if (slot === "DANCE") {
      const e = evs.find((x) => x.start <= t && t < x.end);
      row(slot, e ? `<span class="moving" title="Open loop, at placeholder base speeds; runs only in the twin until the robot floor test">${esc(e.cue.move)} ${(t - e.start).toFixed(1)} of ${(e.end - e.start).toFixed(1)} s</span>`
                  : '<span class="dim">still</span>');
    } else {
      const e = lastAt(evs, t);
      row(slot, e ? (e.cue.do || `${Math.round(e.cue.open * 100)}% open`) : '<span class="dim">—</span>');
    }
  }
  $("preview").innerHTML = `<div class="pv">${out.join("")}</div>`;
}

/* ---------- validator output ---------- */
// The first clause of a notice — up to a colon or dash — is its headline; the rest is on hover.
function shortNote(n) {
  const m = n.match(/^(.{12,90}?)(?::| — | - )/);
  return m ? m[1] : (n.length > 90 ? n.slice(0, 88) + "…" : n);
}
function renderProblems() {
  const d = S.data, el = $("problems");
  if (d.parse_error) { el.innerHTML = `<p class="err">${esc(d.parse_error)}</p>`; return; }
  const items = tl().problems.map((p) => `<li data-cue="${p.cue ?? ""}" class="err">${esc(p.msg)}${p.clip ? ` <span class="dim">(clip ${esc(p.clip)}${p.instance !== null ? " #" + (p.instance + 1) : ""})</span>` : ""}</li>`);
  const warns = (tl().warnings || []).map((w) => `<li data-cue="${w.cue ?? ""}" class="wrn" title="A warning: worth knowing, but the show is still valid">${esc(w.msg)}</li>`);
  const probs = (items.length ? `<ul>${items.join("")}</ul>`
    : '<p class="ok" title="Valid means allowed, not safe — and nothing is executable yet: there is no runner">✓ no problems</p>')
    + (warns.length ? `<ul class="warnings">${warns.join("")}</ul>` : "");
  // Notices are context, not errors: one line each (the full text on hover), folded by default.
  const notes = tl().notices;
  const open = store.get("notesOpen", false) ? " open" : "";
  const notesHtml = notes.length ? `<details class="notices"${open}><summary>${notes.length} note${notes.length > 1 ? "s" : ""}</summary><ul>${
    notes.map((n) => `<li class="notice note" title="${esc(n)}">${esc(shortNote(n))}</li>`).join("")}</ul></details>` : "";
  el.innerHTML = probs + notesHtml;
  const det = el.querySelector("details.notices");
  if (det) det.addEventListener("toggle", () => store.set("notesOpen", det.open));
}
function renderInspector() {
  const el = $("inspector"), i = S.selected, a = (S.data && S.data.authored) || [];
  if (i === null || !a[i]) { el.innerHTML = '<p class="dim" title="Tap an empty lane to move the playhead; + on a lane adds a cue there; drag a cue to retime it">Tap a cue to inspect it.</p>'; return; }
  const c = a[i], form = "t" in c ? "t" : "beat" in c ? "beat" : "bar" in c ? "bar" : "t";
  const rest = {}; for (const [k, v] of Object.entries(c)) if (!["t", "beat", "bar"].includes(k)) rest[k] = v;
  const evs = []; for (const v of Object.values(tl().tracks)) for (const e of v) if (e.src.index === i) evs.push(e);
  const line = S.data.cue_lines ? S.data.cue_lines[i] : null;
  const probs = (problemsByCue().get(i) || []).map((m) => `<div class="err">${esc(m)}</div>`).join("")
    + (warningsByCue().get(i) || []).map((m) => `<div class="wrn">${esc(m)}</div>`).join("");
  let resolved = "";
  if (evs.length === 1) {
    const e = evs[0];
    resolved = `t = ${e.t.toFixed(3)} s <span class="dim">(${esc(e.src.time)})</span> · span ${e.start.toFixed(3)}–${e.end.toFixed(3)} s`;
    if (e.pose) resolved += `<div class="dim">resolved pose (outward): ${Object.values(e.pose).map((v) => v.toFixed(1)).join(", ")}</div>`;
  } else if (evs.length) {
    resolved = `clip ${esc(c.clip)} → ${evs.length} cues, ${Math.min(...evs.map((e) => (e.kind === "keyframe" ? e.t : e.start))).toFixed(3)}–${Math.max(...evs.map((e) => e.end)).toFixed(3)} s <span class="dim">(edit the clip's own cues in the YAML)</span>`;
  }
  el.innerHTML = `
    <div class="meta">cue ${i}${line ? ` · line ${line}` : ""}${c.slot ? " · " + c.slot : ""}${c.clip ? " · clip placement" : ""}</div>
    <div class="resolved">${resolved}</div>${probs}
    <div class="row2">
      <select id="iForm">${["t", "beat", "bar"].map((f) => `<option ${f === form ? "selected" : ""}>${f}</option>`).join("")}</select>
      <input id="iTime" value="${esc(c[form] ?? "")}" inputmode="decimal">
      <button id="iApply">apply</button>
      <button id="iDelete" title="Delete this cue (Delete key)">delete</button>
      ${(c.slot === "ARM_L" || c.slot === "ARM_R") && c.joints ? '<button id="iSoonest" title="Move this keyframe to the soonest time the arm can legally reach it after its previous keyframe — earlier or later than now. The reason is shown.">⇤ soonest</button>' : ""}
    </div>
    <textarea id="iFields" spellcheck="false">${esc(JSON.stringify(rest))}</textarea>
    <div class="note">Fields as JSON. Key order is kept on save; new keys go last.</div>`;
  $("iApply").onclick = () => {
    let fields;
    try { fields = JSON.parse($("iFields").value); } catch (e) { toast("fields are not valid JSON: " + e.message, 4000); return; }
    const f = $("iForm").value, raw = $("iTime").value.trim();
    const v = f === "bar" ? raw : Number(raw);
    if (f !== "bar" && !Number.isFinite(v)) { toast(`${f} must be a number`); return; }
    const cue = {}; cue[f] = v;
    /* keep the author's key order: time first only if it was first before */
    const ordered = {};
    const keys = Object.keys(c);
    for (const k of keys) { if (["t", "beat", "bar"].includes(k)) Object.assign(ordered, cue); else if (k in fields) ordered[k] = fields[k]; }
    if (!Object.keys(ordered).some((k) => ["t", "beat", "bar"].includes(k))) Object.assign(ordered, cue);
    for (const [k, val] of Object.entries(fields)) if (!(k in ordered)) ordered[k] = val;
    edit([{ op: "set", index: i, cue: ordered }], "applied");
  };
  $("iDelete").onclick = () => deleteSelected();
  if ($("iSoonest")) $("iSoonest").onclick = async () => {
    try {
      const r = await api("api/earliest", { path: S.path, text: S.text, slot: c.slot, joints: c.joints, index: i });
      const t = snapUp(r.t), was = evs.length ? evs[0].t : null;
      await edit([{ op: "set", index: i, cue: withTime(c, t) }]);
      toast(`moved to ${t.toFixed(2)} s${was !== null ? ` (was ${was.toFixed(2)} s)` : ""}: ${r.reason}`
        + (r.next_problem ? ` · ${r.next_problem}` : ""), 8000);
    } catch (e) { toast(e.message, 5000); }
  };
}
function deleteSelected() {
  if (S.selected === null) return;
  const i = S.selected; S.selected = null;
  edit([{ op: "delete", index: i }], `deleted cue ${i}`);
}

/* ---------- adding, teaching ---------- */
const DEFAULTS = {
  GRIP_L: { do: "open" }, GRIP_R: { do: "open" }, HEAD: { pan: 90, tilt: 90 },
  LIFT: { do: "up", secs: 2 }, SOUND: { do: "play", sound: "attend" },
  FACE: { do: "emote", name: "NEUTRAL" }, LIGHT: { do: "color", rgb: [255, 255, 255], br: 50 },
  DANCE: { move: "sway", secs: 2 },
};
function addCue(slot) {
  const t = snapT(S.t);
  let body = DEFAULTS[slot];
  if (slot === "ARM_L" || slot === "ARM_R") {
    /* "hold the pose here": a keyframe equal to the interpolated pose — move it from there */
    const p = armAt(tl().tracks[slot] || [], t).pose, joints = {};
    for (let j = 1; j <= 7; j++) if (Math.abs(p[j]) > 0.05) joints[j] = round(p[j], 1);
    body = { joints: Object.keys(joints).length ? joints : { 7: 0 } };
  }
  edit([{ op: "add", cue: { ...newCueTime(t), slot, ...body } }], `added ${slot} at ${t.toFixed(2)} s`);
}
/* A STAND-IN for /joint_states. On the robot the browser would subscribe to /joint_states through
 * rosbridge (the transport notes/design/arm-telemetry-and-viz.md settles), read-only, and post ONE
 * sample to api/capture. Here it fabricates a sample in the controller's own convention — the left
 * arm's J1-J3 NEGATIVE — so the un-mirroring in cue.py is exercised end to end. */
const FakeJointSource = {
  name: "fake",
  sample() {
    const s = performance.now() / 1000, name = [], position = [];
    const outward = { 1: 10 + 8 * Math.sin(s / 3), 2: 35 + 15 * Math.sin(s / 5), 3: 5 * Math.sin(s / 7),
                      4: 70 + 20 * Math.sin(s / 4), 5: 0, 6: 10 * Math.sin(s / 6), 7: 25 * Math.sin(s / 2) };
    for (const side of ["left", "right"]) for (let j = 1; j <= 7; j++) {
      const signed = outward[j] * (side === "left" && j <= 3 ? -1 : 1);
      name.push(`openarm_${side}_joint${j}`); position.push(signed * Math.PI / 180);
    }
    return { name, position, source: "fake" };
  },
};
async function teach(slot) {
  const side = slot === "ARM_L" ? "left" : "right";
  try {
    const cap = await api("api/capture", { side, joint_state: FakeJointSource.sample() });
    /* A taught pose has no timing of its own: place it at the soonest time the arm can reach it
       (workplan 1.3), and say so when that is not where the playhead was. Never silently. */
    const asked = snapT(S.t);
    const r = await api("api/earliest", { path: S.path, text: S.text, slot, joints: cap.joints, t: asked });
    const t = r.moved ? snapUp(r.t) : asked;
    await edit([{ op: "add", cue: { ...newCueTime(t), slot, joints: cap.joints } }]);
    toast(`captured ${slot} from the ${cap.source.toUpperCase()} source: ` + Object.entries(cap.joints).map(([j, v]) => `J${j} ${v}`).join(", ")
      + (r.moved ? ` — placed at ${t.toFixed(2)} s, not ${asked.toFixed(2)} s: ${r.reason}` : "")
      + (r.next_problem ? ` · ${r.next_problem}` : ""), r.moved || r.next_problem ? 9000 : 5000);
  } catch (e) { toast(e.message, 5000); }
}

/* ---------- pointer interaction: tap selects, drag retimes, tap on empty seeks ---------- */
function laneX(ev) { const r = $("canvas").getBoundingClientRect(); return ev.clientX - r.left; }
let drag = null;
function onDown(ev) {
  const target = ev.target.closest("[data-cue]");
  if (!target || !target.closest("#lanes")) {
    if (ev.target.closest("#lanes") || ev.target.closest("#ruler") || ev.target.closest("#music")) {
      drag = { seek: true, x0: ev.clientX, y0: ev.clientY, id: ev.pointerId };
    }
    return;
  }
  const i = +target.dataset.cue, c = S.data.authored[i];
  drag = { i, el: target, x0: ev.clientX, moved: false, anchor: authoredT(c), left0: parseFloat(target.style.left), id: ev.pointerId };
  target.setPointerCapture(ev.pointerId);
  ev.preventDefault();
}
function onMove(ev) {
  if (!drag || drag.seek || ev.pointerId !== drag.id) return;
  const dx = ev.clientX - drag.x0;
  if (!drag.moved && Math.abs(dx) < 6) return;
  drag.moved = true;
  const t = snapT(Math.max(0, drag.anchor + dx / S.pps));
  drag.newT = t;
  drag.el.classList.add("dragging");
  drag.el.style.left = (drag.left0 + (t - drag.anchor) * S.pps) + "px";
  $("clock").textContent = `→ ${t.toFixed(3)} s${barLabel(t) ? " · " + barLabel(t) : ""}`;
}
function onUp(ev) {
  if (!drag || ev.pointerId !== drag.id) return;
  const d = drag; drag = null;
  if (d.seek) {
    if (Math.abs(ev.clientX - d.x0) < 8 && Math.abs(ev.clientY - d.y0) < 8) seek(laneX(ev) / S.pps);
    return;
  }
  if (!d.moved) { S.selected = d.i; renderLanes(); renderInspector(); return; }
  S.selected = d.i;
  const c = S.data.authored[d.i];
  if (d.anchor === null || d.newT === undefined || Math.abs(d.newT - d.anchor) < 1e-6) { renderLanes(); return; }
  edit([{ op: "set", index: d.i, cue: retimed(c, d.newT) }], `moved cue ${d.i} to ${d.newT.toFixed(3)} s`);
}

/* ---------- music: the show library's songs (plan 4.6) ----------
 * Songs live in the SHOW LIBRARY, outside the repository (library.py); a show names one by id
 * (music: song:). Adding goes through the server, which applies the library's rules — a source and a
 * licence, the type checked by content, a size limit, a hash. Attaching, or taking the music out of a
 * show, is an ordinary edit of the show text, so it shows in YAML, can be undone, and needs a save. */
const SONG_EXT = [".wav", ".mp3", ".ogg", ".flac", ".m4a"];
S.songs = []; S.upload = null;
async function refreshSongs() {
  try { S.songs = await api("api/library/songs"); } catch (e) { S.songs = []; toast("library: " + e.message, 5000); }
}
function slug(name) {
  return name.replace(/\.[^.]*$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "").slice(0, 64) || "song";
}
function fmtDur(s) { return s == null ? "?" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`; }
/* The robot plays songs from ITS library: library_sync.py pushes one there over ssh (content-addressed,
 * verified on the robot, never deleting). The composer does not run it — it cannot reach the robot,
 * by design (PREVIEW ONLY) — it shows the command. <robot> is the robot's ssh host. */
function pushCmd(id) {
  const lib = S.info ? S.info.library_dir : "<library>";
  return `python3 software/choreo/library_sync.py --library ${/\s/.test(lib) ? JSON.stringify(lib) : lib} push <robot> ${id}`;
}
function renderMusicDlg() {
  const m = music(), doc = S.data && S.data.timeline;
  $("libDir").textContent = S.info ? S.info.library_dir : "";
  $("musicNow").textContent = !doc ? "This show does not validate yet — its music is shown once it does."
    : !m ? "This show has no music block: times are in seconds."
    : m.song ? `Music: the library song “${m.song}” at ${m.bpm} BPM, beat 1 at ${m.offset_s} s.`
    : m.file ? `Music: the file ${m.file}, next to the show (an older form — choose a library song to replace it).`
    : `No music; the tempo grid is ${m.bpm} BPM, beat 1 at ${m.offset_s} s.`;
  $("songSel").innerHTML = `<option value="">— none —</option>` + S.songs.map((x) =>
    `<option value="${esc(x.id)}" ${m && m.song === x.id ? "selected" : ""}>${esc(x.id)} (${fmtDur(x.duration_s)})</option>`).join("");
  $("songRemove").disabled = !(m && (m.song || m.file));
  $("mBpm").value = m ? m.bpm : ""; $("mOff").value = m ? m.offset_s : "";
  /* the server knows only SAVED shows; the show open here may name a song before it is saved */
  const usedBy = (x) => (m && m.song === x.id && !x.used_by.includes(S.path) ? [S.path + " (open here)"] : []).concat(x.used_by);
  $("songList").innerHTML = S.songs.length ? S.songs.map((x) => ({ ...x, used_by: usedBy(x) })).map((x) => `
    <li><b>${esc(x.id)}</b> <span class="dim">${fmtDur(x.duration_s)} · ${esc(x.file)}</span><br>
      <span class="dim">source: ${esc(x.source)} · licence: ${esc(x.licence)}</span><br>
      ${x.used_by.length ? `<span class="dim">used by ${x.used_by.map(esc).join(", ")}</span>` : `<span class="dim">used by no show here</span>`}
      <div class="sync"><span class="dim">to the robot:</span> <code>${esc(pushCmd(x.id))}</code>
        <button data-copy="${esc(pushCmd(x.id))}" title="Copy the command that sends this song to the robot's library (over ssh; add --dry-run to see what would move first)">copy</button></div>
      <button data-del="${esc(x.id)}" ${x.used_by.length ? `disabled title="Still the music of ${esc(x.used_by.join(", "))} — take it out of those shows first"` : 'title="Delete this song from the library: its file and its entry. Cannot be undone."'}>delete from library</button></li>`).join("")
    : `<li class="dim">No songs yet${S.info && !S.info.library_exists ? " — the library folder is created when the first one is added" : ""}.</li>`;
  const u = S.upload;
  $("songHint").textContent = S.uploadError
    || (u ? `${u.file.name}, ${(u.file.size / 1048576).toFixed(1)} MB${u.duration ? ", " + fmtDur(u.duration) : ""}` : "");
  $("songHint").className = S.uploadError ? "err" : "dim";
  $("songAdd").disabled = !u;
}
async function openMusic(file) {
  await refreshSongs();
  if (file) setUpload(file);
  renderMusicDlg();
  if (!$("musicDlg").open) $("musicDlg").showModal();
}
function setUpload(file) {
  const ext = (file.name.match(/\.[^.]*$/) || [""])[0].toLowerCase();
  if (!SONG_EXT.includes(ext)) { toast(`a song is ${SONG_EXT.join(", ")} — not ${ext || "that"}`, 5000); return; }
  const maxMb = S.info ? S.info.max_song_mb : 80;
  if (file.size > maxMb * 1048576) { toast(`${file.name} is over the ${maxMb} MB limit for a song`, 5000); return; }
  S.upload = { file, duration: null }; S.uploadError = null;
  if (!$("songId").value) $("songId").value = slug(file.name);
  /* the browser's own reading of the length: the server uses it only when it cannot read one */
  const a = new Audio(), url = URL.createObjectURL(file);
  a.preload = "metadata";
  a.onloadedmetadata = () => { if (S.upload && S.upload.file === file) S.upload.duration = a.duration; URL.revokeObjectURL(url); renderMusicDlg(); };
  a.onerror = () => { URL.revokeObjectURL(url); toast("this browser cannot decode that file — the server may still read it", 5000); };
  a.src = url;
  renderMusicDlg();
}
function attachSong(id) {
  return edit([{ op: "music", key: "song", value: id }, { op: "music", key: "file", value: null }],
    `music is now “${id}” — save to keep it`).then(loadAudio);
}
async function addSong() {
  const u = S.upload; if (!u) return;
  const id = $("songId").value.trim(), source = $("songSource").value.trim(), licence = $("songLicence").value.trim();
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) { toast("the id is lower-case letters, digits, - and _, starting with a letter or digit", 5000); return; }
  if (!source || !licence) { toast("a song needs its source and its licence", 5000); return; }
  const q = new URLSearchParams({ filename: u.file.name, id, source, licence });
  if (u.duration && isFinite(u.duration)) q.set("duration_s", u.duration.toFixed(3));
  S.uploadError = null; $("songAdd").disabled = true; $("songHint").textContent = "adding…";
  try {
    /* the file is read into memory first: a read failure (the file moved, or no permission) is then
       its own message, and the request carries a plain body with a known length */
    let body;
    try { body = await u.file.arrayBuffer(); } catch (e) { throw new Error(`could not read ${u.file.name} from this computer: ${e.message}`); }
    const r = await send("api/library/songs?" + q, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body });
    const j = await r.json().catch(() => ({ error: `the composer answered HTTP ${r.status} with no explanation` }));
    if (!r.ok) throw new Error(`${j.error || j.detail || "HTTP " + r.status} (HTTP ${r.status})`);
    S.songs = j.songs; S.upload = null;
    ["songId", "songSource", "songLicence"].forEach((k) => ($(k).value = "")); $("songFile").value = "";
    toast(`added “${id}” to the library`, 2500);
    await attachSong(id);
  } catch (e) { S.uploadError = "not added: " + e.message; toast(S.uploadError, 7000); }
  renderMusicDlg();
}
async function deleteSong(id) {
  if (!confirm(`Delete the song “${id}” from the library?\n\nIts file and its entry are removed. This cannot be undone.`)) return;
  try {
    const r = await api("api/library/songs/delete", { id, confirm: id });
    S.songs = r.songs; toast(`deleted “${id}” from the library`, 2500);
  } catch (e) { toast(e.message, 7000); await refreshSongs(); }
  renderMusicDlg();
}
function bindMusic() {
  $("musicBtn").onclick = () => openMusic();
  $("musicClose").onclick = () => $("musicDlg").close();
  $("songUse").onclick = () => {
    const id = $("songSel").value;
    (id ? attachSong(id) : removeMusic()).then(renderMusicDlg);
  };
  $("songRemove").onclick = () => removeMusic().then(renderMusicDlg);
  $("mSet").onclick = () => {
    const bpm = Number($("mBpm").value), off = Number($("mOff").value);
    if (!Number.isFinite(bpm) || !Number.isFinite(off) || off < 0) { toast("BPM and beat 1 must be numbers (beat 1 ≥ 0)"); return; }
    edit([{ op: "music", key: "bpm", value: bpm }, { op: "music", key: "offset_s", value: off }], `tempo ${bpm} BPM, beat 1 at ${off} s`).then(renderMusicDlg);
  };
  $("songFile").onchange = (e) => e.target.files[0] && setUpload(e.target.files[0]);
  $("songAdd").onclick = addSong;
  $("songList").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-del]"); if (b) deleteSong(b.dataset.del);
    const c = e.target.closest("button[data-copy]");
    if (c) navigator.clipboard.writeText(c.dataset.copy).then(() => toast("copied — replace <robot> with the robot's ssh host", 3000),
                                                              () => toast("select the command and copy it", 3000));
  });
  /* drop an audio file anywhere: the music dialog opens with it */
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); $("drop").classList.add("over"); } });
  window.addEventListener("dragleave", () => $("drop").classList.remove("over"));
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); $("drop").classList.remove("over");
    const f = e.dataTransfer.files[0]; if (f) openMusic(f);
  });
}
function removeMusic() {
  const m = music();
  if (!m || !(m.song || m.file)) return Promise.resolve();
  return edit([{ op: "music", key: "song", value: null }, { op: "music", key: "file", value: null }],
    "music taken out of this show (the song stays in the library) — save to keep it").then(loadAudio);
}

/* ---------- tap tempo ---------- */
function tap(ev) {
  const now = ev.timeStamp / 1000;
  if (S.taps.length && now - S.taps[S.taps.length - 1] > 2.0) S.taps = [];
  S.taps.push(now); if (S.taps.length > 16) S.taps.shift();
  if (S.taps.length >= 4) {
    const iv = S.taps.slice(1).map((x, k) => x - S.taps[k]).sort((a, b) => a - b);
    S.tapBpm = round(60 / iv[Math.floor(iv.length / 2)], 1);       // median: one bad tap does not move it
    $("tapBpm").textContent = `${S.tapBpm} BPM (${S.taps.length} taps)`; $("tapUse").disabled = false;
  } else { $("tapBpm").textContent = `${S.taps.length}…`; }
}

/* ---------- wiring ---------- */
function bind() {
  $("show").onchange = (e) => loadShow(e.target.value);
  $("play").onclick = () => (S.playing ? stop() : play());
  $("rewind").onclick = () => { seek(0); $("scroller").scrollLeft = 0; };
  $("zoomIn").onclick = () => zoom(1.5);
  $("zoomOut").onclick = () => zoom(1 / 1.5);
  $("snap").onchange = (e) => { S.snap = e.target.value; store.set("snap", S.snap); };
  $("metro").onchange = (e) => { S.metro = e.target.checked; store.set("metro", S.metro); };
  $("tap").onpointerdown = tap;
  $("tapUse").onclick = () => S.tapBpm && edit([{ op: "music", key: "bpm", value: S.tapBpm }], `tempo set to ${S.tapBpm} BPM`).then(loadAudio);
  $("beatOne").onclick = () => edit([{ op: "music", key: "offset_s", value: round(S.t, 3) }], `beat 1 is now at ${S.t.toFixed(3)} s`);
  $("undo").onclick = undo;
  $("save").onclick = save;
  bindMusic();
  $("yamlBtn").onclick = () => { $("yamlText").value = S.text; $("yamlDrawer").hidden = false; };
  $("yamlClose").onclick = () => ($("yamlDrawer").hidden = true);
  $("yamlApply").onclick = async () => {
    const d = await api("api/analyse", { path: S.path, text: $("yamlText").value });
    S.undo.push(S.text); setData(d); toast(d.parse_error ? "applied, but it does not parse" : "applied", 1600);
  };
  $("heads").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.add) addCue(b.dataset.add);
    if (b.dataset.teach) teach(b.dataset.teach);
    if (b.dataset.mute) {
      const k = b.dataset.mute; S.muted.has(k) ? S.muted.delete(k) : S.muted.add(k);
      store.set("muted." + S.path, [...S.muted]);
      if (k === "MUSIC" && S.audio.gain) S.audio.gain.gain.value = S.muted.has("MUSIC") ? 0 : 1;
      render();
    }
  });
  $("problems").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-cue]"); if (!li || li.dataset.cue === "") return;
    S.selected = +li.dataset.cue; renderLanes(); renderInspector();
    const el = document.querySelector(`#lanes [data-cue="${S.selected}"]`);
    if (el) $("scroller").scrollLeft = parseFloat(el.style.left) - $("scroller").clientWidth * 0.3;
  });
  const sc = $("scroller");
  sc.addEventListener("pointerdown", onDown);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", () => { drag = null; renderLanes(); });
  sc.addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, e); } }, { passive: false });
  window.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || $("musicDlg").open;
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); return; }
    if (typing) return;
    if (e.key === " ") { e.preventDefault(); S.playing ? stop() : play(); }
    if (e.key === "Home") { e.preventDefault(); seek(0); $("scroller").scrollLeft = 0; }
    else if ((e.key === "Delete" || e.key === "Backspace") && S.selected !== null) { e.preventDefault(); deleteSelected(); }
    else if ((e.ctrlKey || e.metaKey) && e.key === "z") { e.preventDefault(); undo(); }
  });
  window.addEventListener("beforeunload", (e) => { if (S.text !== S.savedText) { e.preventDefault(); e.returnValue = ""; } });
}
function zoom(f, ev) {
  const sc = $("scroller"), anchorX = ev ? ev.clientX - sc.getBoundingClientRect().left : sc.clientWidth / 2;
  const tAt = (sc.scrollLeft + anchorX) / S.pps;
  S.pps = Math.max(8, Math.min(600, S.pps * f)); store.set("pps", S.pps);
  render();
  sc.scrollLeft = tAt * S.pps - anchorX;
}

async function main() {
  S.pps = store.get("pps", 60); S.snap = store.get("snap", "beat"); S.metro = store.get("metro", false);
  $("snap").value = S.snap; $("metro").checked = S.metro;
  bind();
  S.info = await api("api/info");
  const shows = await api("api/shows");
  $("show").innerHTML = shows.map((s) => `<option>${esc(s.path)}</option>`).join("");
  const want = new URLSearchParams(location.search).get("show") || store.get("lastShow", null);
  const first = shows.find((s) => s.path === want) || shows[0];
  if (first) { $("show").value = first.path; await loadShow(first.path); }
}
main().catch((e) => toast("composer failed to start: " + e.message, 10000));
