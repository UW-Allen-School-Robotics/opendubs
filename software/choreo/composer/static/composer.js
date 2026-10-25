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

const SLOTS = ["ARM_L", "ARM_R", "GRIP_L", "GRIP_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT", "DANCE", "DRIVE", "DEVICE", "CRAWL"];
const LANES = ["NOTES", "CLIPS", ...SLOTS];
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
  S.path = path; S.undo = []; S.selected = null; S.selectedNote = null; S.savedText = d.text; S.diskSha = d.disk_sha || null;
  S.clip = null; S.multi = new Set(); $("clipBar").hidden = true;
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
/* Inside a clip (S.clip), the timeline is the clip's own and cue edits go into its cue list. */
S.clip = null; S.multi = new Set(); S.multiMode = false;
const inClip = (edits) => S.clip ? edits.map((e) => (["set", "delete", "add"].includes(e.op) ? { ...e, clip_name: S.clip } : e)) : edits;
async function edit(edits, label) {
  try {
    const d = await api("api/edit", { path: S.path, text: S.text, edits: inClip(edits), clip: S.clip });
    S.undo.push(S.text);
    setData(d);
    if (label) toast(label, 1600);
  } catch (e) { toast(e.message, 5000); }
}
async function undo() {
  if (!S.undo.length) return;
  const text = S.undo.pop();
  setData(await api("api/analyse", { path: S.path, text, clip: S.clip }));
}
async function save() {
  try {
    const d = await api("api/save", { path: S.path, text: S.text });
    S.savedText = d.text; S.diskSha = d.disk_sha || S.diskSha;
    setData(S.clip ? await api("api/analyse", { path: S.path, text: d.text, clip: S.clip }) : d);
    /* a show with reach/point_at targets is compiled on save: say what happened to them */
    const extra = [d.ik, d.sweep].filter(Boolean).join(" · ");
    toast("saved " + S.path + (extra ? " — " + extra : ""), /UNDER|not |refuse/.test(extra) ? 9000 : 2500);
    if ($("musicDlg").open) { await refreshSongs(); renderMusicDlg(); }   /* who uses which song changed */
  } catch (e) { toast(e.message, 5000); }
}

/* ---------- audio: Web Audio, and the playhead is driven from its clock ---------- */
/* PLAYBACK gets a FRESH AudioContext inside every Play click (freshCtx). Safari (found 2026-10-11)
 * starts a context made outside a user gesture "interrupted", and a context that lives for hours
 * stays bound to whatever output device existed when it was made — the tab then "plays sound" that
 * reaches no speaker. A context made in the click is allowed to run, on the output that is current.
 * DECODING uses an offline context, so loading a song never creates a playback context at all. */
function ctx() {
  if (!S.audio.ctx) freshCtx();
  return S.audio.ctx;
}
function freshCtx() {
  const old = S.audio.ctx;
  if (old) { try { old.close(); } catch (e) { /* already closed */ } }
  const AC = window.AudioContext || window.webkitAudioContext;
  const c = new AC();
  S.audio.ctx = c;
  S.audio.gain = c.createGain();
  S.audio.gain.connect(c.destination);
  /* a level meter on what is sent to the speakers: "signal but silence" is then visibly the
     output's problem, not the show's */
  S.audio.meter = c.createAnalyser(); S.audio.meter.fftSize = 512;
  S.audio.gain.connect(S.audio.meter);
  c.onstatechange = () => showAudioState();
  return c;
}
function decodeCtx() {
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  return OAC ? new OAC(2, 1, 48000) : ctx();
}
function showAudioState(level) {
  const el = $("audioState"); if (!el) return;
  const c = S.audio.ctx;
  if (!c) { el.textContent = ""; el.className = "dim"; return; }
  if (c.state !== "running" && S.playing) {
    el.textContent = `audio ${c.state} — press ▶ again (Safari: allow auto-play for this site)`; el.className = "wrn"; return;
  }
  if (!S.playing) { el.textContent = ""; el.className = "dim"; return; }
  const lvl = level === undefined ? null : level;
  const muted = S.muted.has("MUSIC") || (S.audio.gain && S.audio.gain.gain.value === 0);
  el.className = muted ? "wrn" : "dim";
  el.textContent = muted ? "music muted (M on the MUSIC lane)"
    : !S.audio.buffer ? "no music loaded"
    : `audio ▶ ${Math.round(c.sampleRate / 1000)} kHz · level ${lvl === null ? "…" : "▮".repeat(Math.min(10, Math.round(lvl * 20))) || "silent"}`;
}
function meterLevel() {
  const m = S.audio.meter; if (!m) return null;
  const d = new Float32Array(m.fftSize); m.getFloatTimeDomainData(d);
  let pk = 0; for (const v of d) pk = Math.max(pk, Math.abs(v));
  return pk;
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
    S.audio.buffer = await new Promise((ok, no) => decodeCtx().decodeAudioData(buf, ok, no));
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
/* The twins follow ONE show: whichever any composer page last sent (every edit, load and undo sends
 * it). With two pages open — a second window, a tab left on another show, a page that reloaded when
 * the server restarted — the twins could be playing the other page's show while this one plays, and
 * do nothing at all past its end. So Play claims them: it sends this page's show (or clip) again.
 * Fire-and-forget, like the playhead: nothing waits on it, and the music starts as before. */
function claimTwins() {
  api("api/analyse", { path: S.path, text: S.text, clip: S.clip }).catch(() => {});
}
async function play() {
  claimTwins();
  /* a fresh context, made inside this click (see freshCtx); wait until it is really running before
     anything is scheduled on its clock */
  const c = freshCtx();
  S.audio.startedCtx = c.currentTime; S.audio.startedT = S.t;     /* the clock stays put while it starts */
  S.playing = true; $("play").textContent = "⏸";
  try { if (c.state !== "running") await c.resume(); } catch (e) { /* the state shows it */ }
  if (!S.playing || S.audio.ctx !== c) return;          /* stopped, or replayed, while waiting */
  const a = S.audio;
  a.startedCtx = c.currentTime + 0.05; a.startedT = S.t;
  applyTwinVolume();
  if (a.buffer && S.t < a.buffer.duration) {
    a.src = c.createBufferSource(); a.src.buffer = a.buffer; a.src.connect(a.gain);
    a.src.start(a.startedCtx, S.t);
  }
  a.nextClick = Math.ceil(beatOf(S.t) || 0);
  showAudioState();
  postPlayhead(true);
  requestAnimationFrame(frame);
}
function stop() {
  const a = S.audio;
  if (a.src) { try { a.src.stop(); } catch (e) { /* already stopped */ } a.src = null; }
  S.playing = false; $("play").textContent = "▶";
  showAudioState();
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
  if (S.playing && (frame._n = (frame._n || 0) + 1) % 10 === 0) showAudioState(meterLevel());
  if (!S.playing) return;
  S.t = audioNow();
  applyTwinVolume();
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
    if (lane === "NOTES") {
      const open = notes().filter((n) => n.status === "open").length;
      html += `<div class="head notes" title="Notes for whoever edits this show next — often an agent. Write what you want changed, over a range of time and (optionally) some parts; the agent edits the other lanes and replies on each note."><span class="name">NOTES${open ? ` <b class="cnt">${open}</b>` : ""}</span>`
        + `<button data-addnote="1" title="Add a note at the playhead">+</button>`
        + `<button data-prompt="1" title="Copy the open notes as a prompt for any agent session (the file must be saved: the agent reads the file)">⧉</button></div>`;
      continue;
    }
    if (lane === "CLIPS") {
      html += `<div class="head"><span class="name">CLIPS</span><button data-newclip="1" title="A new, empty clip — then add its cues">+</button>`
        + `<button data-placeclip="1" title="Place a clip at the playhead">⇥</button></div>`;
      continue;
    }
    const und = !dec.has(lane);
    const cls = ["head", und ? "undeclared" : "", S.muted.has(lane) ? "muted" : ""].join(" ");
    let btns = "";
    if (lane !== "CLIPS") btns += `<button data-add="${lane}" title="Add a ${lane} cue at the playhead">+</button>`;
    if (lane === "ARM_L" || lane === "ARM_R") btns += `<button data-teach="${lane}" title="Capture this arm's measured pose as a keyframe at the playhead (FAKE source in this build)">⦿</button>`;
    if (lane !== "CLIPS") btns += muteBtn(lane);
    const tip = und ? `Not in this show's slots: list. Adding a cue here lists it; or tap the name to list it now` : "";
    html += `<div class="${cls}" title="${tip}"><span class="name" ${und ? `data-declare="${lane}"` : ""}>${lane}</span>${btns}</div>`;
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
    case "GRIP_L": case "GRIP_R": return c.do || (c.pose ? `pose ${c.pose}` : c.set ? "set " + Object.keys(c.set).join(",") : `open ${c.open}`);
    case "HEAD": return [c.pan !== undefined ? `p${Math.round(c.pan)}` : "", c.tilt !== undefined ? `t${Math.round(c.tilt)}` : ""].join(" ");
    case "LIFT": return c.do + (c.secs ? ` ${c.secs}s` : "");
    case "SOUND": return (c.sound || c.do) + (ev.output ? ` → ${ev.output}` : "");
    case "FACE": return c.do === "theme" ? `theme ${c.name}` : (c.name || c.do);
    case "DANCE": return c.move + (c.amp !== undefined ? ` ×${c.amp}` : "");
    case "DRIVE": return [c.forward_m ? `↑${c.forward_m} m` : "", c.left_m ? `←${c.left_m} m` : "", c.turn_deg ? `↺${c.turn_deg}°` : ""].filter(Boolean).join(" ") + " (twin)";
    case "LIGHT": return c.do === "color" ? "■" : c.do;
    case "CRAWL": return c.do === "walk" ? `${c.dir === "back" ? "◀ back" : "▶ walk"}${c.to ? " to " + (c.to === "start" ? "start" : c.to.join(",")) : c.dist_m ? ` ${c.dist_m} m` : ` ${c.secs}s`} (twin)`
      : c.do === "turn" ? `↺ pivot ${c.by_deg}° (twin)` : c.do === "dance" ? `✋ ${c.pattern} (twin)` : `pause ${c.secs ?? 1}s`;
    case "DEVICE": return `${c.do === "volume" ? "vol" : "bright"} ${Math.round((c.level ?? 0) * 100)}%` + (c.fade_s ? ` /${c.fade_s}s` : "");
    default: return "";
  }
}
function renderLanes() {
  const W = width(), pps = S.pps, lanes = $("lanes"), dec = declared(), bad = problemsByCue(), warn = warningsByCue();
  $("canvas").style.width = W + "px";
  const parts = [];
  for (const lane of LANES) {
    if (lane === "NOTES") {
      parts.push(`<div class="row lane grid notes" data-lane="NOTES">`);
      for (const n of notes()) {
        if (n.t0 === null || n.t0 === undefined) continue;
        const w = Math.max(14, (n.t1 - n.t0) * pps), sel = S.selectedNote === n.id ? "sel" : "";
        parts.push(`<div class="tnote ${n.status} ${sel}" data-note="${esc(n.id)}" style="left:${n.t0 * pps}px;width:${w}px" title="${esc(`${n.id} (${n.status}): ${n.text}` + (n.reply ? `\n↳ ${n.reply}` : "") + "\n(drag to move; drag the right edge to change where it ends)")}">${esc(n.text)}<span class="tnote-end"></span></div>`);
      }
      parts.push("</div>");
      continue;
    }
    const und = lane !== "CLIPS" && !dec.has(lane);
    parts.push(`<div class="row lane grid ${und ? "undeclared" : ""} ${S.muted.has(lane) ? "muted" : ""}" data-lane="${lane}">`);
    /* a note on this part shades its range here too, so the request sits over what it is about */
    for (const n of notes()) if (n.slots.includes(lane) && n.t0 !== null && n.t0 !== undefined && n.status === "open")
      parts.push(`<div class="noteshade" style="left:${n.t0 * pps}px;width:${Math.max(4, (n.t1 - n.t0) * pps)}px"></div>`);
    if (lane === "CLIPS") {
      for (const p of placements()) {
        const cls = ["place", isSel(p.index) ? "sel" : "", bad.has(p.index) ? "bad" : warn.has(p.index) ? "warn" : ""].join(" ");
        parts.push(`<div class="${cls}" data-cue="${p.index}" style="left:${p.start * pps}px;width:${Math.max(20, (p.end - p.start) * pps)}px" title="${esc(p.title)}">${esc(p.text)}</div>`);
      }
    } else if (tl() && tl().tracks[lane]) {
      for (const ev of tl().tracks[lane]) {
        const i = ev.src.index, fc = ev.src.clip ? "fromClip" : "";
        const sel = isSel(i) && !ev.src.clip ? "sel" : "", b = bad.has(i) ? "bad" : warn.has(i) ? "warn" : "";
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
/* The head's volume or brightness at t, as the head will have it (head protocol v5): the show-level
 * `device:` from 0, then each DEVICE cue, fading linearly over its fade_s. null: the show leaves it
 * to the head. Brightness is shown as the head would clamp it (its floor, bright_min, ~0.15). */
function deviceAt(T, what, t) {
  const key = what === "volume" ? "volume" : "brightness";
  let v = T && T.device && T.device[key] != null ? T.device[key] : null;
  for (const e of (T && T.tracks.DEVICE) || []) {
    if (e.cue.do !== key || e.t > t + 1e-9) continue;
    const f = e.cue.fade_s || 0, from = v == null ? 1 : v;
    v = f > 0 && t < e.t + f ? from + (e.cue.level - from) * (t - e.t) / f : e.cue.level;
  }
  return v;
}
function lastAt(evs, t) { let l = null; for (const e of evs) { if (e.t <= t + 1e-9) l = e; } return l; }
function deviceHTML(T, t) {
  const v = deviceAt(T, "volume", t), b = deviceAt(T, "brightness", t);
  const pc = (x) => `${Math.round(x * 100)}%`;
  return `<span title="The head's media volume (the show audio plays at it) and screen brightness. The twin plays the music at this volume; the head never dims below its floor (~15%)">`
    + `vol ${v == null ? '<span class="dim">head\'s own</span>' : pc(v)} · bright ${b == null ? '<span class="dim">head\'s own</span>' : pc(Math.max(0.15, b))}</span>`;
}
/* The twin's music follows the simulated device volume, as the head's media volume would. */
function applyTwinVolume() {
  const a = S.audio;
  if (!a.gain) return;
  const v = deviceAt(tl(), "volume", S.t);
  /* a muted MUSIC lane is silent; a muted DEVICE lane plays at full volume, as if the show set none */
  a.gain.gain.value = S.muted.has("MUSIC") ? 0 : S.muted.has("DEVICE") || v == null ? 1 : v;
}
function renderPreview() {
  const T = tl(), t = S.t, out = [];
  $("previewT").textContent = `${t.toFixed(2)} s`;
  if (!T) { $("preview").innerHTML = ""; return; }
  const row = (k, v) => out.push(`<span class="k">${k}</span><span>${v}</span>`);
  const muted = (k) => S.muted.has(k) ? '<span class="dim">muted</span>' : null;
  for (const slot of SLOTS) {
    if (slot === "DEVICE" && !T.tracks.DEVICE && T.device) {
      row(slot, deviceHTML(T, t)); continue;
    }
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
      row(slot, `<span title="Commanded target. The head turns toward it at 70 °/s per axis (the neck's own slew limit), so it may still be travelling">pan ${pan === null ? "—" : pan.toFixed(0)}° · tilt ${tilt === null ? "—" : tilt.toFixed(0)}°</span>`);
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
      row(slot, e ? `${on ? "♪ " : ""}${e.cue.sound || e.cue.do}${e.output ? ` <span class="dim">on the ${e.output}${e.role === "music" ? " · music (the projector when ready)" : ""}</span>` : ""}${on ? "" : ' <span class="dim">(done)</span>'}` : '<span class="dim">—</span>');
    } else if (slot === "LIGHT") {
      const e = lastAt(evs, t);
      row(slot, e && e.cue.do === "color" ? `<span class="swatch" style="background:rgb(${e.cue.rgb.join(",")});opacity:${(e.cue.br ?? 100) / 100}"></span> ${e.cue.rgb.join(", ")} @ ${e.cue.br ?? 100}%` : '<span class="dim">state colour (no show colour)</span>');
    } else if (slot === "FACE") {
      const e = lastAt(evs.filter((x) => x.cue.do !== "theme"), t);
      const th = lastAt(evs.filter((x) => x.cue.do === "theme"), t);
      row(slot, (e ? e.cue.name : '<span class="dim">face\'s own</span>')
        + (th ? ` <span class="dim" title="A theme lasts until the show ends: every end hands back the robot's idle skin, theme and emote">· theme ${esc(th.cue.name)}</span>` : ""));
    } else if (slot === "DEVICE") {
      row(slot, deviceHTML(T, t));
    } else if (slot === "CRAWL") {
      row(slot, crawlHTML(T, t));
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
  $("floor").innerHTML = T.crawler ? floorSVG(T, t) : "";
}

/* ---------- the crawling hand (twin only; effectors.py) ---------- */
// The leg in progress at t, and the hand's place on the validated (nominal) path.
function crawlAt(T, t) {
  const cr = T.crawler, legs = cr.legs || [];
  let leg = null;
  for (const l of legs) if (l.t <= t && t < l.t + l.secs) leg = l;
  let p = cr.path[0];
  for (const q of cr.path) { if (q[0] <= t + 1e-9) p = q; else break; }
  return { leg, p };
}
const cms = (v) => (v * 100).toFixed(1);
function crawlHTML(T, t) {
  const { leg, p } = crawlAt(T, t);
  const tip = "SIM, NOT HARDWARE: the speeds are simulated (and scaled for a trailing leash where marked); the timeline holds each leg for its SLOWEST case";
  let what = '<span class="dim">standing (idle: tapping the beat)</span>';
  if (leg && leg.do === "walk") what = `<span class="moving" title="${esc(tip)}">${leg.dir === "back" ? "backing" : "walking"} (${esc(leg.gait)}) · ${cms(leg.speed[0])}–${cms(leg.speed[1])} cm/s${leg.speed_key.startsWith("dragging") ? " dragging the leash" : ""} · ${(t - leg.t).toFixed(1)} of ${leg.secs.toFixed(0)} s</span>`;
  else if (leg && leg.do === "turn") what = `<span class="moving" title="${esc(tip)}">pivot ${leg.by > 0 ? "left" : "right"} ${leg.by}° · ${leg.rate[0]}–${leg.rate[1]} °/s · ${(t - leg.t).toFixed(1)} of ${leg.secs.toFixed(0)} s</span>`;
  else if (leg && leg.do === "dance") what = `<span class="moving">finger dance: ${esc(leg.pattern)}</span>`;
  return `${what} <span class="dim">· (${p[1].toFixed(2)}, ${p[2].toFixed(2)}) m, ${p[3].toFixed(0)}°</span>`;
}
// Top-down: the chassis (at its start pose), the leash's anchor and reach, the declared floor area,
// the predicted path with its uncertainty, and the hand at the playhead. x forward is UP the panel.
function floorSVG(T, t) {
  const cr = T.crawler, W = 300, H = 300;
  const pts = cr.path.map((q) => [q[1], q[2]]);
  const xs = [-0.05, 0.62, cr.anchor[0] + cr.length_m * 0.6, ...pts.map((q) => q[0])], ys = [-0.35, 0.35, ...pts.map((q) => q[1])];
  if (cr.area) { xs.push(...cr.area[0]); ys.push(...cr.area[1]); }
  const x0 = Math.min(...xs) - 0.1, x1 = Math.max(...xs) + 0.1, y0 = Math.min(...ys) - 0.1, y1 = Math.max(...ys) + 0.1;
  const k = Math.min(H / (x1 - x0), W / (y1 - y0));
  // screen: up = +x (forward), left = +y (the robot's left)
  const sx = (y) => W / 2 - (y - (y0 + y1) / 2) * k, sy = (x) => H - (x - x0) * k;
  const P = (x, y) => `${sx(y).toFixed(1)},${sy(x).toFixed(1)}`;
  const { p } = crawlAt(T, t);
  const reach = (cr.length_m - cr.slack_m) * k;
  let svg = `<svg class="floor" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Floor plan of the crawling hand">`;
  if (cr.area) svg += `<rect x="${sx(cr.area[1][1])}" y="${sy(cr.area[0][1])}" width="${(cr.area[1][1] - cr.area[1][0]) * k}" height="${(cr.area[0][1] - cr.area[0][0]) * k}" class="area"><title>The declared floor area (crawler.area)</title></rect>`;
  svg += `<circle cx="${sx(cr.anchor[1])}" cy="${sy(cr.anchor[0])}" r="${reach}" class="reach"><title>The leash's reach: ${cr.length_m} m less ${cr.slack_m} m slack</title></circle>`;
  const ko = cr.keep_off_m || 0;
  svg += `<rect x="${sx(0.3245 + ko)}" y="${sy(0.612 + ko)}" width="${(0.649 + 2 * ko) * k}" height="${(0.644 + 2 * ko) * k}" rx="${ko * k}" class="keepoff"><title>Kept clear: the hand's edge stays ${Math.round(ko * 100)} cm from the chassis (keep_off_m)</title></rect>`;
  svg += `<rect x="${sx(0.3245)}" y="${sy(0.612)}" width="${0.649 * k}" height="${0.644 * k}" class="chassis"><title>The chassis (at its start pose)</title></rect>`;
  svg += `<polyline points="${pts.map((q) => P(q[0], q[1])).join(" ")}" class="band" style="stroke-width:${Math.max(2, 2 * (cr.hand_r + Math.max(...cr.path.map((q) => q[4]))) * k)}"><title>Where the hand may be: its size and the path's uncertainty (sim drift, speed range)</title></polyline>`;
  svg += `<polyline points="${pts.map((q) => P(q[0], q[1])).join(" ")}" class="path"><title>The predicted path (mid-range sim speeds)</title></polyline>`;
  svg += `<circle cx="${sx(cr.start[1])}" cy="${sy(cr.start[0])}" r="4" class="dock"><title>Where the hand starts</title></circle>`;
  svg += `<line x1="${sx(cr.anchor[1])}" y1="${sy(cr.anchor[0])}" x2="${sx(p[2])}" y2="${sy(p[1])}" class="leash"/>`;
  const hr = p[3] * Math.PI / 180, L = 0.1;
  svg += `<line x1="${sx(p[2])}" y1="${sy(p[1])}" x2="${sx(p[2] + L * Math.sin(hr))}" y2="${sy(p[1] + L * Math.cos(hr))}" class="hdg"/>`;
  svg += `<circle cx="${sx(p[2])}" cy="${sy(p[1])}" r="${Math.max(3, cr.hand_r * k * 0.6)}" class="hand"><title>The hand at the playhead</title></circle>`;
  svg += "</svg>";
  const legs = (cr.legs || []).filter((l) => l.do === "walk" || l.do === "turn").map((l) => l.do === "walk"
    ? `<li>${l.t.toFixed(1)} s: ${l.dir === "back" ? "back" : "out"} ${l.dist_range[0] !== l.dist_range[1] ? `${Math.round(l.dist_range[0] * 100)}–${Math.round(l.dist_range[1] * 100)} cm in ${l.secs.toFixed(0)} s` : `${(l.dist_m * 100).toFixed(0)} cm in ${(l.dist_m / l.speed[1]).toFixed(0)}–${l.secs.toFixed(0)} s`} at ${cms(l.speed[0])}–${cms(l.speed[1])} cm/s${l.speed_key.startsWith("dragging") ? " (dragging the leash)" : ""}</li>`
    : `<li>${l.t.toFixed(1)} s: pivot ${l.by}° in ${(Math.abs(l.by) / l.rate[1]).toFixed(0)}–${l.secs.toFixed(0)} s</li>`).join("");
  return `<div class="floorbox">${svg}<div class="floorlegend"><b>Crawling hand</b> <span class="badge sim" title="${esc(cr.provenance)}">SIM, NOT HARDWARE</span><ul>${legs}</ul><p class="dim">Dotted circle: the leash's reach (${(cr.length_m - cr.slack_m).toFixed(2)} m from its anchor${cr.anchor[0] + cr.length_m - cr.slack_m > x1 ? ", beyond this view" : ""}). Every leg is held on the timeline for its slowest simulated speed. Twin only: the runner refuses this show on the robot.</p></div></div>`;
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
function isSel(i) { return S.multi.size ? S.multi.has(i) : S.selected === i; }
function renderInspector() {
  if (S.selectedNote) return renderNoteInspector();
  if (S.multi.size > 1) return renderMultiInspector();
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
    resolved = `clip ${esc(c.clip)} → ${evs.length} cues, ${Math.min(...evs.map((e) => (e.kind === "keyframe" ? e.t : e.start))).toFixed(3)}–${Math.max(...evs.map((e) => e.end)).toFixed(3)} s <button data-openclip="${esc(c.clip)}" title="Show this clip's own cues on the timeline, from its start, and edit them there">edit the clip ✎</button>`;
  }
  el.innerHTML = `
    <div class="meta">cue ${i}${line ? ` · line ${line}` : ""}${c.slot ? " · " + c.slot : ""}${c.clip ? " · clip placement" : ""}</div>
    <div class="resolved">${resolved}</div>${probs}
    <div class="row2">
      <select id="iForm">${["t", "beat", "bar"].map((f) => `<option ${f === form ? "selected" : ""}>${f}</option>`).join("")}</select>
      <input id="iTime" value="${esc(c[form] ?? "")}" inputmode="decimal">
      <button id="iApply">apply</button>
      <button id="iDelete" title="Delete this cue (Delete key)">delete</button>
      ${!c.clip && !S.clip ? '<button data-makeclip="1" title="Turn this cue into a clip (shift-click to select more first)">make a clip…</button>' : ""}
      ${(c.slot === "ARM_L" || c.slot === "ARM_R") && c.joints && !S.clip ? '<button id="iSoonest" title="Move this keyframe to the soonest time the arm can legally reach it after its previous keyframe — earlier or later than now. The reason is shown.">⇤ soonest</button>' : ""}
    </div>
    ${c.slot === "SOUND" ? `<div class="row2"><label title="Dialogue always plays from the tablet; music from the projector's speaker when a projector is ready. Unmarked is dialogue.">plays as
      <select id="iRole"><option value="dialogue" ${c.role !== "music" ? "selected" : ""}>dialogue — always the tablet</option>
      <option value="music" ${c.role === "music" ? "selected" : ""}>music — the projector, when one is ready</option></select></label></div>` : ""}
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
  if ($("iRole")) $("iRole").onchange = () => {
    const cue = { ...c };
    if ($("iRole").value === "music") cue.role = "music"; else delete cue.role;    /* unmarked is dialogue */
    edit([{ op: "set", index: i, cue }], $("iRole").value === "music" ? "this sound is MUSIC" : "this sound is DIALOGUE");
  };
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
  DRIVE: { forward_m: 0.5, secs: 3 },          /* twin-only: the runner refuses DRIVE on the robot */
  DEVICE: { do: "volume", level: 0.6, fade_s: 2 },
  CRAWL: { do: "pause", secs: 2 },             /* twin-only: a crawling hand (tools: base) */
};
/* A show lists the parts it moves under `slots:` (the runner's preflight is built from it). Adding a
 * cue to a part not listed there lists it, in the same edit — one undo takes back both. */
function declareIfNeeded(slot) {
  if (declared().has(slot)) return [];
  /* and what the show must then require for that part (the validator's own rules, from api/info) */
  const have = (S.data && S.data.timeline && S.data.timeline.requires) || {};
  const need = (S.info && S.info.slot_requires && S.info.slot_requires[slot]) || {};
  const req = Object.entries(need).filter(([k, v]) => have[k] !== v).map(([key, value]) => ({ op: "require", key, value }));
  return [{ op: "declare", slot }, ...req];
}
function addCue(slot) {
  const t = snapT(S.t);
  let body = DEFAULTS[slot];
  if (slot === "ARM_L" || slot === "ARM_R") {
    /* "hold the pose here": a keyframe equal to the interpolated pose — move it from there */
    const p = armAt(tl().tracks[slot] || [], t).pose, joints = {};
    for (let j = 1; j <= 7; j++) if (Math.abs(p[j]) > 0.05) joints[j] = round(p[j], 1);
    body = { joints: Object.keys(joints).length ? joints : { 7: 0 } };
  }
  edit([...declareIfNeeded(slot), { op: "add", cue: { ...newCueTime(t), slot, ...body } }],
    `added ${slot} at ${t.toFixed(2)} s` + (declared().has(slot) ? "" : ` — and declared ${slot} in slots: (undo takes back both)`));
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
    /* inside a clip, time is the clip's own: place it at the playhead (the whole show is checked on the way back) */
    const r = S.clip ? { moved: false } : await api("api/earliest", { path: S.path, text: S.text, slot, joints: cap.joints, t: asked });
    const t = r.moved ? snapUp(r.t) : asked;
    await edit([...declareIfNeeded(slot), { op: "add", cue: { ...newCueTime(t), slot, joints: cap.joints } }]);
    toast(`captured ${slot} from the ${cap.source.toUpperCase()} source: ` + Object.entries(cap.joints).map(([j, v]) => `J${j} ${v}`).join(", ")
      + (r.moved ? ` — placed at ${t.toFixed(2)} s, not ${asked.toFixed(2)} s: ${r.reason}` : "")
      + (r.next_problem ? ` · ${r.next_problem}` : ""), r.moved || r.next_problem ? 9000 : 5000);
  } catch (e) { toast(e.message, 5000); }
}

/* ---------- pointer interaction: tap selects, drag retimes, tap on empty seeks ---------- */
function laneX(ev) { const r = $("canvas").getBoundingClientRect(); return ev.clientX - r.left; }
let drag = null;
function onDown(ev) {
  const nt = ev.target.closest("[data-note]");
  if (nt) {
    const n = notes().find((x) => x.id === nt.dataset.note);
    drag = { note: n, mode: ev.target.closest(".tnote-end") ? "end" : "move", el: nt, x0: ev.clientX, moved: false,
             left0: parseFloat(nt.style.left), w0: parseFloat(nt.style.width), id: ev.pointerId };
    nt.setPointerCapture(ev.pointerId); ev.preventDefault();
    return;
  }
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
  if (drag && drag.note && ev.pointerId === drag.id) {
    const dx = ev.clientX - drag.x0;
    if (!drag.moved && Math.abs(dx) < 6) return;
    drag.moved = true;
    const n = drag.note, len = (n.t1 ?? n.t0) - n.t0;
    if (drag.mode === "end") {
      drag.t1 = Math.max(n.t0, snapT((n.t1 ?? n.t0) + dx / S.pps));
      drag.el.style.width = Math.max(14, (drag.t1 - n.t0) * S.pps) + "px";
      $("clock").textContent = `note ends → ${drag.t1.toFixed(2)} s${barLabel(drag.t1) ? " · " + barLabel(drag.t1) : ""}`;
    } else {
      drag.t0 = Math.max(0, snapT(n.t0 + dx / S.pps)); drag.t1 = drag.t0 + len;
      drag.el.style.left = (drag.t0 * S.pps) + "px";
      $("clock").textContent = `note → ${drag.t0.toFixed(2)} s${barLabel(drag.t0) ? " · " + barLabel(drag.t0) : ""}`;
    }
    return;
  }
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
  if (d.note) {
    S.selectedNote = d.note.id; S.selected = null;
    if (!d.moved) { renderLanes(); renderInspector(); return; }
    const nn = { ...noteDoc(d.note) };
    if (d.mode === "move") nn.at = noteTime(d.t0);
    if (d.t1 !== undefined && d.t1 > (d.mode === "move" ? d.t0 : d.note.t0) + 1e-6) nn.to = noteTime(d.t1); else delete nn.to;
    edit([{ op: "note_set", id: d.note.id, note: nn }], `note ${d.note.id}: ${nn.at}${nn.to !== undefined ? " → " + nn.to : ""}`);
    return;
  }
  if (d.seek) {
    if (Math.abs(ev.clientX - d.x0) < 8 && Math.abs(ev.clientY - d.y0) < 8) seek(laneX(ev) / S.pps);
    return;
  }
  if (!d.moved) {
    /* shift-click, or the "select several" toggle (for touch), adds to the selection */
    if ((ev.shiftKey || S.multiMode) && S.selected !== null) {
      if (S.multi.has(d.i)) S.multi.delete(d.i); else S.multi.add(d.i);
      if (!S.multi.size) S.multi.add(d.i);
      S.selected = d.i;
    } else { S.selected = d.i; S.multi = new Set([d.i]); }
    S.selectedNote = null; renderLanes(); renderInspector(); return;
  }
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
  $("mRole").value = (m && m.role) || "dialogue"; $("mRole").disabled = !(m && (m.song || m.file));
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
  $("mRole").onchange = () => {
    const r = $("mRole").value;
    edit([{ op: "music", key: "role", value: r }], r === "music" ? "the show's track is MUSIC: the projector's speaker when one is ready"
      : "the show's track is DIALOGUE: always the tablet").then(renderMusicDlg);
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

/* ---------- timeline notes (plan #5): requests for the next editor, often an agent ----------
 * Stored in the show as `timeline_notes:` — never read by the runner. The loop: write notes here;
 * copy them as a prompt (⧉) or ask the choreography lane to "apply the notes in <show>"; the agent
 * snapshots the show, edits the other lanes, and replies on each note; this editor notices the file
 * changed on disk and shows the difference; History compares and restores any earlier version. */
S.selectedNote = null; S.diskSha = null;
const notes = () => (tl() && tl().timeline_notes) || [];
function nextNoteId() {
  const used = new Set(notes().map((n) => n.id));
  let k = notes().length + 1; while (used.has("n" + k)) k++; return "n" + k;
}
function noteTime(t) {
  /* at / to: "bar.beat" when the show has a beat grid (rounded to a beat), else seconds */
  const m = music();
  if (!m) return round(t, 2);
  const beat = Math.max(1, Math.round(beatOf(t))), bar = Math.floor((beat - 1) / m.beats_per_bar) + 1;
  return `${bar}.${((beat - 1) % m.beats_per_bar) + 1}`;
}
/* a note as written in the file (what note_set writes back), from its resolved form */
function noteDoc(n) {
  const out = { id: n.id, at: n.at };
  if (n.to !== null && n.to !== undefined) out.to = n.to;
  if (n.slots && n.slots.length) out.slots = n.slots;
  out.text = n.text; out.status = n.status;
  if (n.reply) out.reply = n.reply;
  if (n.by) out.by = n.by;
  return out;
}
function addNote() {
  const m = music(), t0 = S.t, t1 = S.t + (m ? m.beat_s * m.beats_per_bar : 2);
  const id = nextNoteId();
  const note = { id, at: noteTime(t0), to: noteTime(t1), text: "describe the change you want here", status: "open" };
  edit([{ op: "note_add", note }], `note ${id} added — write what you want changed`).then(() => {
    S.selectedNote = id; S.selected = null; renderLanes(); renderInspector();
    const ta = $("nText"); if (ta) { ta.focus(); ta.select(); }
  });
}
/* A note's at / to, as typed: with a beat grid, "30" is bar 30, "30.3" bar 30 beat 3, and "61s" is
 * seconds; without one, numbers are seconds. Anything else is refused WITH the reason — "30.5" in
 * 4/4 is not a beat, and silently accepting it collapsed the note to a point (found by the user). */
function parseWhen(x) {
  x = (x || "").trim();
  if (!x) return { value: undefined };
  const m = music();
  let mm = x.match(/^(\d+(?:\.\d+)?)\s*s$/);
  if (mm) return { value: Number(mm[1]), secs: Number(mm[1]) };
  if (!m) {
    if (/^\d+(\.\d+)?$/.test(x)) return { value: Number(x), secs: Number(x) };
    return { error: `“${x}”: write seconds, e.g. 12.5` };
  }
  mm = x.match(/^(\d+)(?:\.(\d+))?$/);
  if (!mm) return { error: `“${x}”: a bar like 9.3 (bar 9, beat 3), or seconds like 18.5s` };
  const bar = +mm[1], beat = mm[2] === undefined ? 1 : +mm[2], bpb = m.beats_per_bar;
  if (bar < 1 || beat < 1 || beat > bpb) {
    return { error: `“${x}”: bar.beat — a bar has ${bpb} beats (${bar}.1–${bar}.${bpb}); halfway through bar ${bar} is ${bar}.${Math.floor(bpb / 2) + 1}, the start of the next is ${bar + 1}.1; for seconds write ${x}s` };
  }
  return { value: mm[2] === undefined ? String(bar) : `${bar}.${beat}`, secs: m.offset_s + ((bar - 1) * bpb + beat - 1) * m.beat_s };
}
function renderNoteInspector() {
  const el = $("inspector"), n = notes().find((x) => x.id === S.selectedNote);
  if (!n) { S.selectedNote = null; return renderInspector(); }
  const slotsBox = SLOTS.map((s) => `<label class="chk"><input type="checkbox" data-nslot="${s}" ${n.slots.includes(s) ? "checked" : ""}>${s}</label>`).join("");
  const span = n.t0 === null ? "" : `${n.t0.toFixed(2)}${n.t1 !== n.t0 ? "–" + n.t1.toFixed(2) : ""} s`;
  el.innerHTML = `
    <div class="meta">note ${esc(n.id)} · ${esc(span)} ${n.by ? "· replied by " + esc(n.by) : ""}</div>
    <textarea id="nText" spellcheck="true" placeholder="What should change here? e.g. make this wave bigger and on the beat">${esc(n.text)}</textarea>
    <div class="row2">
      <label>at <input id="nAt" value="${esc(n.at ?? "")}" title="bar.beat like 9.3 (bar 9, beat 3), a bar like 9, or seconds like 18.5s"> <span id="nAtT" class="dim"></span></label>
      <label>to <input id="nTo" value="${esc(n.to ?? "")}" title="optional: bar.beat, a bar, or seconds like 22s — or drag the note's right edge"> <span id="nToT" class="dim"></span></label>
      <select id="nStatus" title="open: still to do · done · declined (the reply says why)">${["open", "done", "declined"].map((x) => `<option ${x === n.status ? "selected" : ""}>${x}</option>`).join("")}</select>
    </div>
    <div class="row2 slots" title="Which parts the note is about (none: the whole show at that time)">${slotsBox}</div>
    ${n.reply ? `<div class="reply">↳ ${esc(n.reply)}</div>` : ""}
    ${((tl() && tl().warnings) || []).filter((w) => w.msg.includes(`(${n.id})`)).map((w) => `<div class="wrn">${esc(w.msg)}</div>`).join("")}
    <div id="nErr" class="err"></div>
    <div class="row2"><button id="nApply">apply</button><button id="nDelete">delete note</button><button id="nCopy" title="Copy the open notes as a prompt">⧉ copy notes as a prompt</button></div>
    <div class="note">Notes never move the robot: the runner ignores them. Save, then hand them to an agent.</div>`;
  const show = () => {
    const a = parseWhen($("nAt").value), b = parseWhen($("nTo").value);
    $("nAtT").textContent = a.error ? "?" : a.secs !== undefined ? `= ${a.secs.toFixed(2)} s` : "";
    $("nToT").textContent = b.error ? "?" : b.secs !== undefined ? `= ${b.secs.toFixed(2)} s` : "";
    const err = a.error || b.error || (a.value === undefined ? "a note needs its start (at)" : "")
      || (b.secs !== undefined && a.secs !== undefined && b.secs < a.secs ? "the end (to) is before the start (at)" : "");
    $("nErr").textContent = err; $("nApply").disabled = !!err;
    return { a, b, err };
  };
  $("nAt").oninput = show; $("nTo").oninput = show; show();
  $("nApply").onclick = () => {
    const { a, b, err } = show(); if (err) return;
    const slots = [...el.querySelectorAll("[data-nslot]")].filter((c) => c.checked).map((c) => c.dataset.nslot);
    const nn = { id: n.id, at: a.value };
    if (b.value !== undefined) nn.to = b.value;
    if (slots.length) nn.slots = slots;
    nn.text = $("nText").value.trim() || n.text;
    nn.status = $("nStatus").value;
    if (n.reply) nn.reply = n.reply;
    if (n.by) nn.by = n.by;
    edit([{ op: "note_set", id: n.id, note: nn }], `note ${n.id} updated`);
  };
  $("nDelete").onclick = () => { S.selectedNote = null; edit([{ op: "note_delete", id: n.id }], `note ${n.id} deleted`); };
  $("nCopy").onclick = copyPrompt;
}
async function copyPrompt() {
  if (S.text !== S.savedText) { toast("save first — the agent reads the show FILE, and your latest notes are not in it yet", 6000); return; }
  try {
    const r = await api("api/notes/prompt?path=" + encodeURIComponent(S.path));
    await navigator.clipboard.writeText(r.prompt);
    toast("copied: paste it into any agent session — or ask the choreography lane to “apply the notes in " + S.path + "”", 7000);
  } catch (e) { toast("could not copy the prompt: " + e.message, 6000); }
}

/* ---- the file on disk: notice edits made outside the editor (an agent), never overwrite them ---- */
async function watchDisk() {
  if (!S.path || $("diskBar").dataset.pending === "1") return;
  try {
    const d = await api("api/show/disk?path=" + encodeURIComponent(S.path));
    if (S.diskSha && d.disk_sha !== S.diskSha && d.text !== S.text) {
      S.disk = d; $("diskBar").dataset.pending = "1"; $("diskBar").hidden = false;
      $("diskMsg").textContent = S.text !== S.savedText
        ? "This show changed on disk (an agent applying notes?) — and you have unsaved edits here."
        : "This show changed on disk (an agent applying notes?).";
    } else if (!S.diskSha || d.text === S.text) { S.diskSha = d.disk_sha; }
  } catch (e) { /* the composer may be restarting: the next poll tries again */ }
}
async function loadDisk() {
  const d = S.disk; if (!d) return;
  S.undo.push(S.text);                       /* your text stays one undo away */
  setData(await api("api/analyse", { path: S.path, text: d.text }));
  S.savedText = d.text; S.diskSha = d.disk_sha; S.disk = null;
  $("diskBar").hidden = true; $("diskBar").dataset.pending = "";
  toast("loaded the changes from disk — undo brings your previous text back", 5000);
  await loadAudio();
}
function keepMine() {
  S.diskSha = S.disk ? S.disk.disk_sha : S.diskSha; S.disk = null;
  $("diskBar").hidden = true; $("diskBar").dataset.pending = "";
  toast("kept yours: saving will replace the disk version (it is kept in History first)", 6000);
}
async function reviewDisk() {
  if (!S.disk) return;
  const r = await api("api/diff", { a: S.text, b: S.disk.text });
  showText("Changes on disk", r.diff || "(no difference in the text)", [["load them", loadDisk], ["keep mine", keepMine]]);
}

/* ---- history: every save, and every agent's snapshot, kept beside the shows ---- */
async function openHistory() {
  const list = await api("api/history?path=" + encodeURIComponent(S.path));
  const rows = list.length ? list.map((h) => `<li><button data-snap="${esc(h.name)}">${esc(h.when)}</button> <span class="dim">by ${esc(h.by)}</span></li>`).join("")
    : '<li class="dim">No earlier versions yet: one is kept every time the show is saved, and before an agent edits it.</li>';
  showText("History of " + S.path, "", [], `<ul class="hist">${rows}</ul>`);
  $("textBody").querySelectorAll("[data-snap]").forEach((b) => (b.onclick = async () => {
    const r = await api("api/history/read", { path: S.path, name: b.dataset.snap, text: S.text });
    showText(`Restore ${b.dataset.snap}?`, r.diff || "(identical to the text now)", [["restore into the editor", async () => {
      S.undo.push(S.text);
      setData(await api("api/analyse", { path: S.path, text: r.text }));
      $("textDlg").close(); toast("restored into the editor — save to keep it, undo to go back", 6000);
      await loadAudio();
    }], ["back", openHistory]]);
  }));
}
function showText(title, text, actions, html) {
  $("textTitle").textContent = title;
  $("textBody").innerHTML = (html || "") + (text ? `<pre class="diff">${text.split("\n").map((l) =>
    `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}">${esc(l)}</span>`).join("\n")}</pre>` : "");
  $("textActions").innerHTML = actions.map((a, k) => `<button data-act="${k}">${esc(a[0])}</button>`).join("");
  $("textActions").querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => { const f = actions[+b.dataset.act][1]; if (f !== openHistory) $("textDlg").close(); f(); }));
  if (!$("textDlg").open) $("textDlg").showModal();
}

/* ---------- show files: new, save a copy (duplicate), rename, delete ---------- */
async function refreshShows(select) {
  const shows = await api("api/shows");
  $("show").innerHTML = shows.map((x) => `<option>${esc(x.path)}</option>`).join("");
  if (select) $("show").value = select;
  return shows;
}
const slugOf = (p) => (p || "").split("/").pop().replace(/\.ya?ml$/, "");
async function openFiles() {
  const songs = await api("api/library/songs").catch(() => []);
  const cur = slugOf(S.path);
  const html = `
    <section class="files">
      <h3>New show</h3>
      <div class="row2"><input id="fNewName" placeholder="name, e.g. welcome-wave" autocomplete="off">
        <select id="fNewSong"><option value="">no music</option>${songs.map((x) => `<option>${esc(x.id)}</option>`).join("")}</select>
        <input id="fNewBpm" placeholder="BPM" size="5" inputmode="decimal">
        <button id="fNew">create and open</button></div>
      <p class="note">Starts empty: the first cue on a lane lists that part. With music, bars and beats are ready to use.</p>
      <h3>This show: ${esc(S.path || "")}</h3>
      <div class="row2"><input id="fCopyName" value="${esc(cur)}-copy" autocomplete="off">
        <button id="fCopy" title="Write what you are editing under a new name and switch to it; the original file is untouched">save a copy and open it</button></div>
      <div class="row2"><input id="fRenName" value="${esc(cur)}" autocomplete="off">
        <button id="fRen" title="Rename the file; its id (and its name, if it came from the file name) follow, and so does its History">rename</button></div>
      <div class="row2"><button id="fDel" class="danger" title="Delete this show file. Its last version is kept in History.">delete this show…</button></div>
      <p class="note">Names: letters, digits, - and _; ".yaml" is added. Shows live in ${esc((S.info && S.info.shows_dir) || "the shows folder")}.</p>
    </section>`;
  showText("Show files", "", [], html);
  const go = async (fn, okMsg) => {
    try { const d = await fn(); $("textDlg").close(); return d; } catch (e) { toast(e.message, 6000); }
  };
  $("fNew").onclick = () => go(async () => {
    if (S.text !== S.savedText && !confirm("Discard unsaved changes to " + S.path + "?")) throw new Error("kept editing " + S.path);
    const d = await api("api/show/new", { name: $("fNewName").value, song: $("fNewSong").value || null, bpm: $("fNewBpm").value || null });
    S.savedText = S.text; await refreshShows(d.path); await loadShow(d.path);
    toast(`new show ${d.path} — add cues with + on a lane`, 4000);
  });
  $("fCopy").onclick = () => go(async () => {
    const d = await api("api/show/save-as", { path: S.path, name: $("fCopyName").value, text: S.text });
    S.savedText = S.text;                                /* the edits now live in the copy */
    await refreshShows(d.path); await loadShow(d.path);
    toast(`saved as ${d.path} — the original is unchanged`, 4000);
  });
  $("fRen").onclick = () => go(async () => {
    if (S.text !== S.savedText) throw new Error("save first — rename moves the file as it is on disk");
    const d = await api("api/show/rename", { path: S.path, name: $("fRenName").value });
    await refreshShows(d.path); await loadShow(d.path);
    toast(`renamed to ${d.path} (its History came along)`, 4000);
  });
  $("fDel").onclick = () => go(async () => {
    if (!confirm(`Delete the show ${S.path}?\n\nIts last version is kept in History (${(S.info && S.info.shows_dir) || ""}/.history/).`)) throw new Error("not deleted");
    const d = await api("api/show/delete", { path: S.path, confirm: S.path });
    S.savedText = S.text; S.path = null;
    const shows = await refreshShows();
    if (shows.length) { $("show").value = shows[0].path; await loadShow(shows[0].path); }
    toast(`deleted ${d.deleted} — kept in History as ${d.kept}`, 6000);
  });
}

/* ---------- clips: make one from cues, a new one, edit inside one (the user's composer item #1) ---------- */
function renderMultiInspector() {
  const a = (S.data && S.data.authored) || [], idx = [...S.multi].sort((x, y) => x - y);
  const kinds = idx.map((i) => (a[i] && (a[i].slot || (a[i].clip ? "clip " + a[i].clip : "?")))).join(", ");
  $("inspector").innerHTML = `
    <div class="meta">${idx.length} cues selected · ${esc(kinds)}</div>
    <div class="row2"><button data-makeclip="1" title="Turn these cues into a reusable clip: their times become the clip's own, and one placement takes their place">make a clip of these…</button>
      <button data-multimode="1" class="${S.multiMode ? "on" : ""}" title="On a touch screen: tap cues to add or remove them (or hold shift and click)">select several: ${S.multiMode ? "on" : "off"}</button>
      <button data-clearsel="1">clear</button></div>
    <div class="note">Shift-click (or "select several") adds and removes cues. A placement cannot go into a clip.</div>`;
}
async function makeClip() {
  const idx = [...(S.multi.size ? S.multi : new Set([S.selected]))].filter((i) => i !== null);
  if (S.clip) { toast("a clip cannot contain a clip — go back to the show first", 5000); return; }
  const name = prompt("Name the new clip (letters, digits, - and _):", "move" + ((tl().clips_count || 0) + 1));
  if (!name) return;
  try {
    const d = await api("api/clip/make", { path: S.path, text: S.text, indices: idx, name });
    S.undo.push(S.text); S.multi = new Set(); S.selected = null; setData(d);
    toast(`clip ${name} made from ${idx.length} cue${idx.length > 1 ? "s" : ""} — one placement took their place (undo takes it back)`, 6000);
  } catch (e) { toast(e.message, 6000); }
}
async function newClip() {
  if (S.clip) { toast("go back to the show to make another clip", 4000); return; }
  const name = prompt("Name the new clip (letters, digits, - and _):"); if (!name) return;
  const m = music(), len = prompt(m ? "How long is it, in beats?" : "How long is it, in seconds?", m ? "4" : "2"); if (len === null) return;
  const n = Number(len); if (!(n > 0)) { toast("a clip's length is a positive number", 4000); return; }
  await edit([{ op: "clip_set", name, clip: m ? { length_beats: Math.round(n) } : { length_s: n } }], `clip ${name} made — add its cues`);
  openClip(name);
}
async function placeClip() {
  if (S.clip) { toast("go back to the show to place a clip", 4000); return; }
  const names = (S.data && S.data.clip_names) || [];
  if (!names.length) { toast("this show has no clips yet — + on the CLIPS lane makes one", 5000); return; }
  const name = prompt("Place which clip at the playhead? (" + names.join(", ") + ")", names[0]); if (!name) return;
  edit([{ op: "add", cue: { ...newCueTime(snapT(S.t)), clip: name } }], `clip ${name} placed at ${S.t.toFixed(2)} s`);
}
async function openClip(name) {
  try {
    const d = await api("api/analyse", { path: S.path, text: S.text, clip: name });
    if (d.parse_error) throw new Error(d.parse_error);
    S.clip = name; S.selected = null; S.multi = new Set(); S.selectedNote = null;
    setData(d); seek(0); $("scroller").scrollLeft = 0;
    const L = d.timeline.clip || {};
    $("clipMsg").textContent = `Editing the clip “${name}”${L.length_beats ? `, ${L.length_beats} beat${L.length_beats === 1 ? "" : "s"}` : L.length_s ? `, ${L.length_s} s` : ""} — its own cues, from its start. Edits go into the clip; save as usual.`;
    $("clipBar").hidden = false;
  } catch (e) { toast(e.message, 5000); }
}
async function closeClip() {
  S.clip = null; S.selected = null; S.multi = new Set();
  setData(await api("api/analyse", { path: S.path, text: S.text }));
  $("clipBar").hidden = true;
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
  $("historyBtn").onclick = openHistory;
  $("filesBtn").onclick = openFiles;
  $("textClose").onclick = () => $("textDlg").close();
  $("diskReview").onclick = reviewDisk; $("diskLoad").onclick = loadDisk; $("diskKeep").onclick = keepMine;
  setInterval(watchDisk, 2000);
  $("yamlBtn").onclick = () => { $("yamlText").value = S.text; $("yamlDrawer").hidden = false; };
  $("yamlClose").onclick = () => ($("yamlDrawer").hidden = true);
  $("yamlApply").onclick = async () => {
    const d = await api("api/analyse", { path: S.path, text: $("yamlText").value, clip: S.clip });
    S.undo.push(S.text); setData(d); toast(d.parse_error ? "applied, but it does not parse" : "applied", 1600);
  };
  $("heads").addEventListener("click", (e) => {
    const d = e.target.closest("[data-declare]");
    if (d) { edit(declareIfNeeded(d.dataset.declare), `${d.dataset.declare} is now in slots:`); return; }
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.addnote) addNote();
    if (b.dataset.newclip) newClip();
    if (b.dataset.placeclip) placeClip();
    if (b.dataset.prompt) copyPrompt();
    if (b.dataset.add) addCue(b.dataset.add);
    if (b.dataset.teach) teach(b.dataset.teach);
    if (b.dataset.mute) {
      const k = b.dataset.mute; S.muted.has(k) ? S.muted.delete(k) : S.muted.add(k);
      store.set("muted." + S.path, [...S.muted]);
      if (k === "MUSIC" || k === "DEVICE") applyTwinVolume();
      render();
    }
  });
  $("inspector").addEventListener("click", (e) => {
    const o = e.target.closest("[data-openclip]"); if (o) openClip(o.dataset.openclip);
    if (e.target.closest("[data-makeclip]")) makeClip();
    if (e.target.closest("[data-multimode]")) { S.multiMode = !S.multiMode; renderInspector(); }
    if (e.target.closest("[data-clearsel]")) { S.multi = new Set(); S.selected = null; renderLanes(); renderInspector(); }
  });
  $("clipBack").onclick = closeClip;
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
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || $("musicDlg").open || $("textDlg").open;
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
  const shows = await refreshShows();
  const want = new URLSearchParams(location.search).get("show") || store.get("lastShow", null);
  const first = shows.find((s) => s.path === want) || shows[0];
  if (first) { $("show").value = first.path; await loadShow(first.path); }
}
main().catch((e) => toast("composer failed to start: " + e.message, 10000));
