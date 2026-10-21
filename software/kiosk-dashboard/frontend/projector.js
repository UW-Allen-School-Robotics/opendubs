/* OpenDubs dashboard — the PROJECTOR card on the Shows page: is the show-video output set up?

   Reads the show runner's video status through the dashboard (GET /api/choreo/video, the Choreography
   lane's proxy) and sets the output for the next play (POST /api/choreo/video/output). It shows, per
   output (a Chromecast-style cast receiver, the Thor's HDMI): found and ready, or why not; which output
   is selected, with a control to change it; and while a show plays, its video and how far the picture
   is from the show's clock. A status older than STALE_S is shown as "not checked", never as ready.
   A card of its own in the Shows view, so it never edits the Shows page's own file (choreo.js). */
"use strict";
(() => {
  const view = document.getElementById("view-shows");
  if (!view) return;
  const STALE_S = 15, POLL_MS = 2000, SYNC_WARN_MS = 100;

  const CSS = `
    .pj-card { max-width: 900px; display: grid; gap: 10px; align-content: start; }
    .pj-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .pj-row > .pj-k { min-width: 120px; color: var(--dim); }
    .pj-row select { font: inherit; padding: 6px 8px; background: transparent; color: var(--txt);
                     border: 1px solid var(--line, #444); border-radius: 6px; }
    .pj-note { color: var(--dim); font-size: 13px; }
    .pj-play { font-variant-numeric: tabular-nums; }
    .pj-card .pj-bad { color: var(--bad); }
    .pj-row .pj-v { flex: 1 1 260px; min-width: 0; }
    .pj-val { min-width: 64px; text-align: center; font-variant-numeric: tabular-nums; }
    .pj-row { flex-wrap: nowrap; } .pj-row.pj-wrap { flex-wrap: wrap; }`;
  const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);

  const card = document.createElement("section");
  card.className = "card pj-card";
  card.innerHTML = `
    <h2>PROJECTOR <span id="pjBadge" class="badge dimbadge">—</span></h2>
    <div class="pj-row"><span class="pj-k">Cast</span><span class="sdot" id="pjCastDot"></span><span class="pj-v" id="pjCast">—</span></div>
    <div class="pj-row"><span class="pj-k">HDMI</span><span class="sdot" id="pjHdmiDot"></span><span class="pj-v" id="pjHdmi">—</span></div>
    <div class="pj-row pj-wrap"><label class="pj-k" for="pjOut">Show video on</label>
      <select id="pjOut"><option value="">each show's own</option><option value="cast">the projector (cast)</option><option value="hdmi">HDMI</option><option value="off">nowhere (off)</option></select>
      <span class="pj-note" id="pjDefault"></span></div>
    <div class="pj-note pj-bad" id="pjNote" hidden></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Music</span><span class="pj-v" id="pjMusic">—</span></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Audio lead</span>
      <button class="iobtn" data-lead="audio_lead_s" data-d="-0.025">−25 ms</button>
      <span class="pj-val" id="pjAudioLead">—</span>
      <button class="iobtn" data-lead="audio_lead_s" data-d="0.025">+25 ms</button>
      <span class="pj-note">Set by ear: run a show, listen whether the arms land on the beat, nudge, run again. Used from the next run.</span></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Video lead</span>
      <button class="iobtn" data-lead="video_lead_s" data-d="-0.025">−25 ms</button>
      <span class="pj-val" id="pjVideoLead">—</span>
      <button class="iobtn" data-lead="video_lead_s" data-d="0.025">+25 ms</button></div>
    <div class="pj-row pj-wrap"><label class="pj-k" for="pjMusicAs">Music sent as</label>
      <select id="pjMusicAs"><option value="audio">audio</option><option value="video">video</option></select></div>
    <div class="pj-row pj-wrap pj-play" id="pjPlaying" hidden></div>
    <div class="pj-note" id="pjWhy"></div>`;
  view.appendChild(card);
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const dot = (id, cls) => { $(id).className = "sdot " + (cls || ""); };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  let busy = false, settings = null;

  function output(o, kind) {
    if (!o) return ["", "—"];
    if (kind === "cast") {
      if (!o.found) return ["bad", "not found" + (o.why ? ` — ${esc(o.why)}` : "")];
      const name = o.name ? esc(o.name) : "a receiver";
      return o.ready ? ["ok", `${name}, ready`] : ["warn", `${name}, not ready` + (o.why ? ` — ${esc(o.why)}` : "")];
    }
    if (!o.present) return ["bad", "no display" + (o.why ? ` — ${esc(o.why)}` : "")];
    return o.ready ? ["ok", "ready"] : ["warn", "not ready" + (o.why ? ` — ${esc(o.why)}` : "")];
  }

  function render(v) {
    const age = typeof v.checked_at === "number" ? Date.now() / 1000 - v.checked_at
              : typeof v.age_s === "number" ? v.age_s : 0;
    const stale = age > STALE_S;
    const outs = v.outputs || {};
    for (const [kind, id] of [["cast", "pjCast"], ["hdmi", "pjHdmi"]]) {
      const [cls, txt] = stale ? ["", `not checked for ${Math.round(age)} s`] : output(outs[kind], kind);
      $(id).innerHTML = txt; dot(id + "Dot", cls);
    }
    // The control shows the operator's override, or "each show's own" when there is none
    // (`selected` is then the robot's default, named beside it).
    const want = v.override || "";
    if (!busy && $("pjOut").value !== want) $("pjOut").value = want;
    $("pjDefault").textContent = !v.override && v.default ? `now: ${v.default} (the robot's default)` : "";
    // Show audio (choreo-audio.md): where the music is, and the leads the next run will use.
    const mu = v.music || {};
    $("pjMusic").textContent = mu.on === "projector" ? "playing on the projector"
      : mu.on === "tablet" ? "playing on the tablet" + (mu.why ? ` — ${mu.why}` : "")
      : "no show running";
    settings = v.settings || null;
    const ms = (x) => (typeof x === "number" ? `${Math.round(x * 1000)} ms` : "—");
    $("pjAudioLead").textContent = ms(settings && settings.audio_lead_s);
    $("pjVideoLead").textContent = ms(settings && settings.video_lead_s);
    if (!busy && settings && settings.music_as && $("pjMusicAs").value !== settings.music_as) $("pjMusicAs").value = settings.music_as;
    for (const b of card.querySelectorAll("[data-lead]")) b.disabled = !settings;
    $("pjMusicAs").disabled = !settings;
    // A show running WITHOUT its video says why (choreo-video.md §6 `note`).
    $("pjNote").hidden = !v.note; $("pjNote").textContent = v.note || "";
    const sel = outs[v.selected];
    const ready = !stale && v.selected !== "off" && sel && sel.ready;
    $("pjBadge").textContent = stale ? "NOT CHECKED" : v.selected === "off" ? "OFF" : ready ? "READY" : "NOT READY";
    $("pjBadge").className = "badge " + (stale ? "dimbadge" : v.selected === "off" ? "dimbadge" : ready ? "good" : "warn");
    const p = v.playing;
    $("pjPlaying").hidden = !p;
    if (p) {
      const err = p.sync_err_ms;
      const sync = err == null ? "sync —" : `sync ${err > 0 ? "+" : ""}${err} ms`;
      $("pjPlaying").innerHTML = `<span class="pj-k">Playing</span><span>${esc(p.show || "?")} · ${esc(p.file || "?")} on ${esc(p.output || "?")} · ` +
        `${mmss(p.position_s || 0)} · <span class="${err != null && Math.abs(err) > SYNC_WARN_MS ? "pj-bad" : ""}">${sync}</span>` +
        `${p.seeks ? ` · ${p.seeks} correction${p.seeks > 1 ? "s" : ""}` : ""}</span>`;
    }
  }

  async function poll() {
    if (!view.classList.contains("active")) return;
    try {
      const r = await fetch("/api/choreo/video", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status === 404 ? "this robot's dashboard has no video service yet" : `HTTP ${r.status}`);
      render(await r.json());
      $("pjWhy").textContent = "";
    } catch (e) {
      $("pjBadge").textContent = "UNAVAILABLE"; $("pjBadge").className = "badge dimbadge";
      $("pjWhy").textContent = `Projector status unavailable: ${e.message}`;
    }
  }

  $("pjOut").addEventListener("change", async (e) => {
    busy = true;
    try {
      const r = await fetch("/api/choreo/video/output", { method: "POST", headers: { "Content-Type": "application/json" },
                                                        body: JSON.stringify({ output: e.target.value || null }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { $("pjWhy").textContent = `Not changed: ${j.error || j.detail || `HTTP ${r.status}`}`; if (j.code) $("pjWhy").appendChild(helpLink(j.code)); }
      else { $("pjWhy").textContent = ""; if (j.outputs) render(j); }
    } catch (err) { $("pjWhy").textContent = `Not changed: ${err.message}`; }
    finally { busy = false; poll(); }
  });

  // The show-audio settings, stored on the robot and used from the next run.
  async function saveSettings(change) {
    busy = true;
    try {
      const r = await fetch("/api/choreo/video/settings", { method: "POST", headers: { "Content-Type": "application/json" },
                                                          body: JSON.stringify(change) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { $("pjWhy").textContent = `Not saved: ${j.error || j.detail || `HTTP ${r.status}`}`; if (j.code) $("pjWhy").appendChild(helpLink(j.code)); }
      else { $("pjWhy").textContent = ""; if (j.outputs) render(j); }
    } catch (err) { $("pjWhy").textContent = `Not saved: ${err.message}`; }
    finally { busy = false; }
  }
  for (const b of card.querySelectorAll("[data-lead]")) {
    b.addEventListener("click", () => {
      const key = b.dataset.lead, cur = settings && settings[key];
      if (typeof cur !== "number") return;
      const next = Math.round(Math.min(3, Math.max(0, cur + Number(b.dataset.d))) * 1000) / 1000;
      saveSettings({ [key]: next });
    });
  }
  $("pjMusicAs").addEventListener("change", (e) => saveSettings({ music_as: e.target.value }));

  setInterval(poll, POLL_MS);
  poll();
})();
