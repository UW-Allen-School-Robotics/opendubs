/* OpenDubs dashboard — the PROJECTOR card on the Shows page: is the show-video output set up?

   Reads the show runner's video status through the dashboard (GET /api/choreo/video, the Choreography
   lane's proxy) and sets the output for the next play (POST /api/choreo/video/output). It shows, per
   output (a Chromecast-style cast receiver, the Thor's HDMI): found and ready, or why not; which output
   is selected, with a control to change it; and while a show plays, its video and how far the picture
   is from the show's clock. A status older than STALE_S is shown as "not checked", never as ready.
   And where the show's SOUND plays (#591, GET/POST /api/choreo/audio/output): the head tablet, the
   speakerphone or the stage box, each ready or why not; the operator's choice lasts until changed, as the
   video's does. A run whose chosen output is not ready is refused (SHOW_AUDIO_OUTPUT_MISSING), never
   played silently. The rows stay hidden on a runner that has no such endpoint.

   THE SHOWS PAGE IN THREE STEPS (Choreography's layout, the user's decision 2026-10-28): when index.html
   has its containers, the two choices an operator makes before a show go to step 1 as compact rows,
   "Video plays on" (#showsVideoRow) and "Sound plays on" (#showsSoundRow): a picker, one line saying what
   it does, and whether the selected output is ready and why not; the sound row also TESTs the selected
   speaker. Everything else on this card folds into Advanced: the timing rows into #showsAdvTiming, the
   rest (every output's own row, the stage page, loading and stalls, now playing) into #showsAdvDiag.
   Nothing is removed. With no containers (an older index.html) the card stays as it was.
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
    .pj-row { flex-wrap: nowrap; } .pj-row.pj-wrap { flex-wrap: wrap; }
    .pj-gone { display: none !important; }
    .pj-main { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .pj-main .pj-mk { display: grid; min-width: 150px; } .pj-main .pj-mk span { color: var(--dim); font-size: 13px; }
    .pj-main select { font: inherit; padding: 8px 10px; min-width: 200px; background: transparent; color: var(--txt);
                      border: 1px solid var(--line, #444); border-radius: 6px; }
    .pj-main .pj-mstat { flex: 1 1 280px; min-width: 0; }
    .pj-main .pj-bad, .pj-mwhy .pj-bad, .pj-adv .pj-bad { color: var(--bad); }
    .pj-mwhy { color: var(--dim); font-size: 13px; }
    .pj-main .sdot { display: inline-block; width: 10px; height: 10px; margin-right: 6px; vertical-align: 1px; }
    .pj-main .pj-mctl { display: inline-flex; align-items: center; gap: 8px; }`;
  const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);

  const card = document.createElement("section");
  card.className = "card pj-card";
  card.innerHTML = `
    <h2>PROJECTOR <span id="pjBadge" class="badge dimbadge">—</span></h2>
    <div class="pj-row"><span class="pj-k">Cast</span><span class="sdot" id="pjCastDot"></span><span class="pj-v" id="pjCast">—</span></div>
    <div class="pj-row" id="pjStageRow" hidden><span class="pj-k">Stage page</span><span class="sdot" id="pjStageDot"></span><span class="pj-v" id="pjStage">—</span></div>
    <div class="pj-row pj-wrap" id="pjStageDevRow" hidden><span class="pj-k"></span><span class="pj-v pj-note" id="pjStageDev">—</span></div>
    <div class="pj-row pj-wrap" id="pjStageLoadRow" hidden><span class="pj-k"></span><span class="pj-v pj-note" id="pjStageLoad">—</span></div>
    <div class="pj-row"><span class="pj-k">HDMI</span><span class="sdot" id="pjHdmiDot"></span><span class="pj-v" id="pjHdmi">—</span></div>
    <div class="pj-row pj-wrap pj-picker"><label class="pj-k" for="pjOut">Show video on</label>
      <select id="pjOut"><option value="">each show's own</option><option value="cast">the projector (cast)</option><option value="stage">the stage page (the projector's app)</option><option value="hdmi">HDMI</option><option value="off">nowhere (off)</option></select>
      <span class="pj-note" id="pjDefault"></span></div>
    <div class="pj-row pj-wrap pj-picker" id="pjAudRow" hidden><label class="pj-k" for="pjAudOut">Show sound on</label>
      <select id="pjAudOut"><option value="">each show's own</option><option value="stage">the stage box</option>
        <option value="tablet">the head tablet</option><option value="speakerphone">the speakerphone</option></select>
      <span class="pj-note" id="pjAudDefault"></span></div>
    <div class="pj-row pj-wrap" id="pjAudOutsRow" hidden><span class="pj-k"></span><span class="pj-v" id="pjAudOuts"></span></div>
    <div class="pj-note pj-bad" id="pjNote" hidden></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Music</span><span class="pj-v" id="pjMusic">—</span></div>
    <div class="pj-row pj-wrap pj-timing"><span class="pj-k">Cast audio lead</span>
      <button class="iobtn" data-lead="audio_lead_s" data-d="-0.025">−25 ms</button>
      <span class="pj-val" id="pjAudioLead">—</span>
      <button class="iobtn" data-lead="audio_lead_s" data-d="0.025">+25 ms</button>
      <span class="pj-note">Set by ear: run a show, listen whether the arms land on the beat, nudge, run again. Used from the next run.</span></div>
    <div class="pj-row pj-wrap pj-timing"><span class="pj-k">Cast video lead</span>
      <button class="iobtn" data-lead="video_lead_s" data-d="-0.025">−25 ms</button>
      <span class="pj-val" id="pjVideoLead">—</span>
      <button class="iobtn" data-lead="video_lead_s" data-d="0.025">+25 ms</button></div>
    <div class="pj-row pj-wrap pj-timing"><span class="pj-k">Stage audio lead</span>
      <button class="iobtn" data-stage-audio="-0.025">−25 ms</button>
      <span class="pj-val" id="pjStageAudioLead">—</span>
      <button class="iobtn" data-stage-audio="0.025">+25 ms</button>
      <button class="iobtn" id="pjStageAudioReset" hidden>use the box's estimate</button>
      <span class="pj-note" id="pjStageAudioNote">For the sound output the stage box is using now (each output keeps its own): set by ear the same way.</span></div>
    <div class="pj-row pj-wrap pj-timing"><span class="pj-k">Stage video lead</span>
      <button class="iobtn" data-lead="stage_video_lead_s" data-d="-0.025">−25 ms</button>
      <span class="pj-val" id="pjStageVideoLead">—</span>
      <button class="iobtn" data-lead="stage_video_lead_s" data-d="0.025">+25 ms</button></div>
    <div class="pj-row pj-wrap pj-timing"><span class="pj-k">Stage buffer</span>
      <button class="iobtn" data-lead="stage_buffer_s" data-d="-5" data-min="5" data-max="120">−5 s</button>
      <span class="pj-val" id="pjStageBuffer">—</span>
      <button class="iobtn" data-lead="stage_buffer_s" data-d="5" data-min="5" data-max="120">+5 s</button>
      <span class="pj-note">How much video a stage page loads before a show may start, when it has no local copy.</span></div>
    <div class="pj-row pj-wrap pj-timing"><label class="pj-k" for="pjMusicAs">Music sent as</label>
      <select id="pjMusicAs"><option value="audio">audio</option><option value="video">video</option></select></div>
    <div class="pj-row pj-wrap pj-play" id="pjPlaying" hidden></div>
    <div class="pj-note" id="pjWhy"></div>`;
  const slot = (id) => document.getElementById(id);
  const slots = { video: slot("showsVideoRow"), sound: slot("showsSoundRow"), timing: slot("showsAdvTiming"), diag: slot("showsAdvDiag") };
  const compact = !!(slots.video && slots.sound && slots.timing && slots.diag);
  let timingBox = null;                              // the timing rows, once moved out of the card
  if (compact) {
    slots.video.insertAdjacentHTML("beforeend", `
      <div class="pj-main"><div class="pj-mk"><b>Video plays on</b><span>for every show you start</span></div>
        <select id="pjVidMain" aria-label="Video plays on"><option value="stage">Stage screen</option>
          <option value="">Each show decides</option><option value="cast">Projector (cast)</option>
          <option value="hdmi">HDMI</option><option value="off">Off</option></select>
        <span class="pj-mstat" id="pjVidStat">—</span></div>`);
    slots.sound.insertAdjacentHTML("beforeend", `
      <div class="pj-main"><div class="pj-mk"><b>Sound plays on</b><span>for every show you start</span></div>
        <select id="pjAudMain" aria-label="Sound plays on"><option value="stage">Stage box speaker</option>
          <option value="">Each show decides</option><option value="tablet">Head tablet</option>
          <option value="speakerphone">Speakerphone</option></select>
        <span class="pj-mstat" id="pjAudStat">—</span>
        <span class="pj-mctl"><button class="iobtn" id="pjAudTest">TEST SOUND</button><span class="pj-note">a short tone on the selected speaker</span></span>
        <span class="pj-mctl" id="sbMainVol"></span></div>
      <div class="pj-mwhy" id="pjMainWhy"></div>`);
    // the timing rows to Advanced → Timing; the old pickers stay in the page, hidden (the main rows replace
    // them and render() still sets them); everything else to Advanced → Diagnostics
    const timing = timingBox = document.createElement("div"); timing.className = "pj-card pj-adv";
    for (const r of card.querySelectorAll(".pj-timing")) timing.appendChild(r);
    for (const r of card.querySelectorAll(".pj-picker")) r.classList.add("pj-gone");
    slots.timing.appendChild(timing);
    card.classList.add("pj-adv");
    slots.diag.appendChild(card);
  } else view.appendChild(card);
  const $ = (id) => document.getElementById(id);
  const all = (sel) => [...card.querySelectorAll(sel), ...(timingBox ? timingBox.querySelectorAll(sel) : [])];
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const dot = (id, cls) => { $(id).className = "sdot " + (cls || ""); };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  let busy = false, settings = null, lastStagePage = null;

  function output(o, kind) {
    if (!o) return ["", "—"];
    if (kind === "cast") {
      if (!o.found) return ["bad", "not found" + (o.why ? ` — ${esc(o.why)}` : "")];
      const name = o.name ? esc(o.name) : "a receiver";
      return o.ready ? ["ok", `${name}, ready`] : ["warn", `${name}, not ready` + (o.why ? ` — ${esc(o.why)}` : "")];
    }
    if (kind === "stage") {                      // ready: at least one /stage/ page is connected (#454)
      if (o.ready) return ["ok", `${o.pages || 1} page${(o.pages || 1) > 1 ? "s" : ""} open, ready`];
      return ["warn", "not open" + (o.why ? ` — ${esc(o.why)}` : "")];
    }
    if (!o.present) return ["bad", "no display" + (o.why ? ` — ${esc(o.why)}` : "")];
    return o.ready ? ["ok", "ready"] : ["warn", "not ready" + (o.why ? ` — ${esc(o.why)}` : "")];
  }

  // What the stage page says about the device it runs on (#479; the stage box's agent, software/stage-pi):
  // its screen and sound, and its power. A missing voltage flag means it could not be read, never "fine".
  function stageDevice(p) {
    $("pjStageDevRow").hidden = !p;
    if (!p) return;
    const bits = [];
    bits.push(esc(p.name || p.device || "a page") + (p.model ? ` (${esc(p.model)})` : ""));
    bits.push(p.screen_connected === false ? "no screen, sound only"
      : p.screen_mode ? esc(p.screen_mode).replace("x", "×") : Array.isArray(p.viewport) ? p.viewport.join("×") : "screen ?");
    if (p.audio_output) bits.push(p.audio_output === "none" ? "no sound output" : `sound ${p.audio_output.toUpperCase()}` + (p.audio_name ? ` (${esc(p.audio_name)})` : ""));
    if (typeof p.clock_err_ms === "number") bits.push(`clock ${p.clock_err_ms > 0 ? "+" : ""}${Math.round(p.clock_err_ms)} ms`);
    const warn = [];
    if (p.sound === "muted") warn.push("the browser muted the sound");
    if (p.under_voltage_now === true) warn.push("UNDER-VOLTAGE now: the power supply is too weak");
    else if (p.under_voltage_since_boot === true) warn.push("under-voltage since boot: check the power supply");
    else if (p.device === "stage-pi" && !("under_voltage_now" in p)) bits.push("power: can't read");
    const age = typeof p.at === "number" ? Date.now() / 1000 - p.at : 0;
    if (age > STALE_S) bits.push(`report ${Math.round(age)} s old`);
    $("pjStageDev").innerHTML = bits.join(" · ") + (warn.length ? ` · <span class="pj-bad">⚠ ${warn.join("; ")}</span>` : "");
  }

  function render(v) {
    const age = typeof v.checked_at === "number" ? Date.now() / 1000 - v.checked_at
              : typeof v.age_s === "number" ? v.age_s : 0;
    const stale = age > STALE_S;
    const outs = v.outputs || {};
    $("pjStageRow").hidden = !outs.stage;          // a runner without the stage output (before #454)
    stageDevice(outs.stage && outs.stage.page);
    lastStagePage = outs.stage && outs.stage.page;
    for (const [kind, id] of [["cast", "pjCast"], ["stage", "pjStage"], ["hdmi", "pjHdmi"]]) {
      if (kind === "stage" && !outs.stage) continue;
      const [cls, txt] = stale ? ["", `not checked for ${Math.round(age)} s`] : output(outs[kind], kind);
      $(id).innerHTML = txt; dot(id + "Dot", cls);
    }
    // The control shows the operator's override, or "each show's own" when there is none
    // (`selected` is then the robot's default, named beside it).
    const want = v.override || "";
    if (!busy && $("pjOut").value !== want) $("pjOut").value = want;
    $("pjDefault").textContent = !v.override && v.default ? `now: ${v.default} (the robot's default)` : "";
    if (compact) videoMain(v, outs, stale, age);
    // Show audio (choreo-audio.md): where the music is, and the leads the next run will use.
    const mu = v.music || {};
    $("pjMusic").textContent = mu.on === "projector" ? "playing on the projector"
      : mu.on === "tablet" ? "playing on the tablet" + (mu.why ? ` — ${mu.why}` : "")
      : "no show running";
    settings = v.settings || null;
    const ms = (x) => (typeof x === "number" ? `${Math.round(x * 1000)} ms` : "—");
    $("pjAudioLead").textContent = ms(settings && settings.audio_lead_s);
    $("pjVideoLead").textContent = ms(settings && settings.video_lead_s);
    stageAudio(outs.stage && outs.stage.page, ms);
    $("pjStageVideoLead").textContent = ms(settings && settings.stage_video_lead_s);
    $("pjStageBuffer").textContent = settings && typeof settings.stage_buffer_s === "number" ? `${Math.round(settings.stage_buffer_s)} s` : "—";
    if (!busy && settings && settings.music_as && $("pjMusicAs").value !== settings.music_as) $("pjMusicAs").value = settings.music_as;
    for (const b of all("[data-lead]")) b.disabled = !settings || typeof settings[b.dataset.lead] !== "number";
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

  // The stage pages' show loading and their local videos (GET /api/stage, Choreography #479): a local
  // copy on the box plays with no Wi-Fi; otherwise a page buffers the show before it may start. A page
  // that ran dry mid-show is a stall: the tablet takes the music only if it holds a copy, and music chosen
  // for the stage box has none (#591), so the line does not say who has it.
  function stageStatus(j) {
    const lines = [], loads = Array.isArray(j && j.preload) ? j.preload : [];
    loads.forEach((p, i) => {
      if (!p) return;
      const who = loads.length > 1 ? `page ${i + 1}: ` : "";
      const s = p.error ? `<span class="pj-bad">${esc(p.error)}</span>`
        : p.local ? "local copy"
        : p.ready ? `ready (${Math.round(p.buffered_s || 0)} s buffered)`
        : `buffering ${Math.round(p.buffered_s || 0)} of ${Math.round(p.need_s || 0)} s`;
      lines.push(`${who}${p.id ? esc(p.id) + ": " : ""}${s}`);
    });
    for (const st of (j && j.stalls) || []) {
      lines.push(`<span class="pj-bad">stalled at ${Number(st.at_s || 0).toFixed(1)} s: the page ran out of data</span>`);
    }
    if (j && j.show && j.show.muted) lines.push(`<span class="pj-bad">stage pages silent for this show</span>`);
    const info = Array.isArray(j && j.pages_info) ? j.pages_info.find((x) => x && "media_items" in x) : null;
    if (info) {
      const gb = (b) => (typeof b === "number" ? (b / 1e9).toFixed(1) + " GB" : "?");
      lines.push(`local videos: ${info.media_items} held (${gb(info.media_bytes)}, ${gb(info.media_free)} free)` +
                 (info.media_syncing ? " · syncing" : ""));
    }
    $("pjStageLoadRow").hidden = !lines.length;
    $("pjStageLoad").innerHTML = lines.join(" · ");
  }

  // Step 1's "Video plays on": the operator's choice, or "Each show decides" (each show's own output, else
  // the robot's default, said in words), and whether the chosen output is ready, with its own reason.
  const VID_NAMES = { stage: "Stage screen", cast: "Projector (cast)", hdmi: "HDMI", off: "Off" };
  let mainBusy = false;
  const sentence = (t) => (/[.?!]$/.test(t) ? t : t + ".");
  const stat = (ok, head, rest) => `<span class="sdot ${ok ? "ok" : "bad"}"></span><b>${head}</b>${rest ? " · " + rest : ""}`;
  function videoMain(v, outs, stale, age) {
    const sel = $("pjVidMain");
    const hdmi = sel.querySelector('option[value="hdmi"]');
    hdmi.hidden = hdmi.disabled = !(outs.hdmi && outs.hdmi.present) && v.override !== "hdmi";   // HDMI only where there is one
    if (!mainBusy && document.activeElement !== sel && sel.value !== (v.override || "")) sel.value = v.override || "";
    const o = v.override;
    let html;
    if (!o) html = `Each show uses its own video output; a show that names none uses the robot's default`
      + (v.default ? ` (${esc(VID_NAMES[v.default] || v.default)})` : "") + ".";
    else if (o === "off") html = "No video: shows play without their video.";
    else if (stale) html = stat(false, "Not checked", `no status for ${Math.round(age)} s`);
    else {
      const out = outs[o], [cls, txt] = output(out, o);
      html = cls === "ok" ? stat(true, "Ready", txt.replace(/, ready$/, ""))
        : stat(false, "Not ready", sentence(txt) + " A show started now plays without its video.");
    }
    $("pjVidStat").innerHTML = html;
  }
  async function choose(path, value, what) {
    mainBusy = true;
    try {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ output: value || null }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { mainSay(`${what} not changed: ${j.error || j.detail || `HTTP ${r.status}`}`, j.code); return null; }
      mainSay("");
      return j;
    } catch (err) { mainSay(`${what} not changed: ${err.message}`); return null; }
    finally { mainBusy = false; }
  }
  function mainSay(text, code) {
    const el = compact ? $("pjMainWhy") : $("pjWhy");
    el.textContent = text || "";
    if (code && window.helpLink) el.appendChild(helpLink(code));
  }

  // Where the show's sound plays (#591): each output, ready or why not, and the choice for the next run.
  const AUD_NAMES = { stage: "Stage box", tablet: "Head tablet", speakerphone: "Speakerphone" };
  let audBusy = false;
  function renderAudio(a) {
    const has = !!(a && Array.isArray(a.outputs));
    $("pjAudRow").hidden = $("pjAudOutsRow").hidden = !has;
    if (!has) return;
    const want = a.override || "";
    if (!audBusy && $("pjAudOut").value !== want) $("pjAudOut").value = want;
    const byName = Object.fromEntries(a.outputs.map((o) => [o.name, o]));
    for (const opt of $("pjAudOut").options) {
      if (!opt.value) continue;
      const o = byName[opt.value];
      opt.textContent = opt.textContent.replace(/ — .*$/, "") + (!o ? " — not connected" : o.ready ? "" : " — not ready");
    }
    $("pjAudDefault").textContent = !a.override && a.default ? `now: ${a.default}` : "";
    const bits = a.outputs.map((o) => `<span class="sdot ${o.ready ? "ok" : "warn"}"></span>${esc(AUD_NAMES[o.name] || o.name)}`
      + (o.label ? ` (${esc(o.label)})` : "") + ": " + (o.ready ? "ready" : `<span class="pj-bad">${esc(o.why || "not ready")}</span>`));
    const sel = a.override && byName[a.override];
    if (a.override && !(sel && sel.ready)) bits.push(`<span class="pj-bad">the next run is refused until ${esc(AUD_NAMES[a.override] || a.override)} is ready</span>`);
    if (a.playing) bits.push(`playing: ${esc(a.playing.show || "?")} on ${esc(AUD_NAMES[a.playing.output] || a.playing.output || "?")}`);
    $("pjAudOuts").innerHTML = bits.join(" · ");
    $("pjAudOut").disabled = !!a.playing;
    if (compact) soundMain(a, byName);
  }
  // Step 1's "Sound plays on": the stage box by its speaker's own name ("soundcore Boom 2 Plus, Bluetooth"),
  // and when the chosen device can't play, that a show started now is refused (SHOW_AUDIO_OUTPUT_MISSING).
  function speakerName(o) {
    const page = lastStagePage, kind = page && STAGE_OUTS[page.audio_output];
    return [o && o.label, kind].filter(Boolean).join(", ");
  }
  function soundMain(a, byName) {
    const sel = $("pjAudMain");
    if (!mainBusy && document.activeElement !== sel && sel.value !== (a.override || "")) sel.value = a.override || "";
    sel.disabled = !!a.playing;
    const o = a.override, out = o && byName[o];
    let html;
    if (a.playing) html = `Playing: ${esc(a.playing.show || "a show")} on ${esc(AUD_NAMES[a.playing.output] || a.playing.output || "?")}.`;
    else if (!o) html = a.default ? `Each show decides: ${esc(sentence(a.default))}` : "Each show plays its music where it says.";
    else if (out && out.ready) html = stat(true, "Ready", esc(o === "stage" ? speakerName(out) || "the stage box" : out.label || AUD_NAMES[o]));
    else html = stat(false, "Not ready", `${esc(sentence((out && out.why) || "not connected"))} <span class="pj-bad">A show started now is refused.</span>`);
    $("pjAudStat").innerHTML = html;
    const t = $("pjAudTest");
    t.disabled = !o || !!a.playing;
    t.title = !o ? "pick a speaker above to test it" : "";
  }
  async function pollAudio() {
    try {
      const r = await fetch("/api/choreo/audio/output", { cache: "no-store" });
      renderAudio(r.ok ? await r.json() : null);
    } catch (e) { renderAudio(null); }
  }
  $("pjAudOut").addEventListener("change", async (e) => {
    audBusy = true;
    try {
      const r = await fetch("/api/choreo/audio/output", { method: "POST", headers: { "Content-Type": "application/json" },
                                                        body: JSON.stringify({ output: e.target.value || null }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { $("pjWhy").textContent = `Sound output not changed: ${j.error || j.detail || `HTTP ${r.status}`}`; if (j.code && window.helpLink) $("pjWhy").appendChild(helpLink(j.code)); }
      else { $("pjWhy").textContent = ""; renderAudio(j); }
    } catch (err) { $("pjWhy").textContent = `Sound output not changed: ${err.message}`; }
    finally { audBusy = false; pollAudio(); }
  });

  async function poll() {
    if (!view.classList.contains("active")) return;
    pollAudio();
    try {
      const r = await fetch("/api/choreo/video", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status === 404 ? "this robot's dashboard has no video service yet" : `HTTP ${r.status}`);
      render(await r.json());
      $("pjWhy").textContent = "";
      try { const t = await fetch("/api/stage", { cache: "no-store" }); stageStatus(t.ok ? await t.json() : null); }
      catch (e) { stageStatus(null); }               // a runner without the stage page: nothing to show
    } catch (e) {
      $("pjBadge").textContent = "UNAVAILABLE"; $("pjBadge").className = "badge dimbadge";
      $("pjWhy").textContent = `Projector status unavailable: ${e.message}`;
      if (compact) $("pjVidStat").innerHTML = stat(false, "Unavailable", esc(e.message));
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

  // The stage's audio lead is kept PER SOUND OUTPUT (stage_audio_leads_s: hdmi, jack, usb, bt; choreo-stage.md):
  // the row shows and nudges the one for the output the stage page's box is using now. An output with no lead
  // of its own uses the box's estimate of its delay plus stage_audio_lead_s (the general trim); with no box
  // reporting, the buttons nudge that trim.
  const STAGE_OUTS = { hdmi: "HDMI", jack: "3.5 mm jack", usb: "USB", bt: "Bluetooth" };
  let stageLead = null;                              // {out, cur} the buttons act on
  function stageAudio(page, ms) {
    const out = page && STAGE_OUTS[page.audio_output] ? page.audio_output : null;
    const trim = settings && typeof settings.stage_audio_lead_s === "number" ? settings.stage_audio_lead_s : null;
    const own = out && settings && settings.stage_audio_leads_s ? settings.stage_audio_leads_s[out] : undefined;
    const est = page && typeof page.audio_latency_ms === "number" ? page.audio_latency_ms / 1000 : null;
    let txt, cur;
    if (!out) { cur = trim; txt = ms(trim) + " (every output)"; }
    else if (typeof own === "number") { cur = own; txt = `${ms(own)} for ${STAGE_OUTS[out]}, set by ear`; }
    else { cur = (est || 0) + (trim || 0); txt = `${ms(cur)} for ${STAGE_OUTS[out]}`
                                             + (est != null ? `: the box's estimate ${ms(est)}` + (trim ? ` + ${ms(trim)}` : "") : ""); }
    stageLead = trim == null ? null : { out, cur };
    $("pjStageAudioLead").textContent = txt;
    $("pjStageAudioReset").hidden = !(out && typeof own === "number");
    for (const b of all("[data-stage-audio]")) b.disabled = !stageLead;
  }
  for (const b of all("[data-stage-audio]")) {
    b.addEventListener("click", () => {
      if (!stageLead) return;
      const next = Math.round(Math.min(3, Math.max(0, stageLead.cur + Number(b.dataset.stageAudio))) * 1000) / 1000;
      saveSettings(stageLead.out ? { stage_audio_leads_s: { [stageLead.out]: next } } : { stage_audio_lead_s: next });
    });
  }
  $("pjStageAudioReset").addEventListener("click", () => {
    if (stageLead && stageLead.out) saveSettings({ stage_audio_leads_s: { [stageLead.out]: null } });
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
  for (const b of all("[data-lead]")) {
    b.addEventListener("click", () => {
      const key = b.dataset.lead, cur = settings && settings[key];
      if (typeof cur !== "number") return;
      const lo = Number(b.dataset.min || 0), hi = Number(b.dataset.max || 3);
      const next = Math.round(Math.min(hi, Math.max(lo, cur + Number(b.dataset.d))) * 1000) / 1000;
      saveSettings({ [key]: next });
    });
  }
  $("pjMusicAs").addEventListener("change", (e) => saveSettings({ music_as: e.target.value }));

  if (compact) {
    $("pjVidMain").addEventListener("change", async (e) => {
      const j = await choose("/api/choreo/video/output", e.target.value, "Video output");
      if (j && j.outputs) render(j);
      poll();
    });
    $("pjAudMain").addEventListener("change", async (e) => {
      const j = await choose("/api/choreo/audio/output", e.target.value, "Sound output");
      if (j) renderAudio(j);
      pollAudio();
    });
    // TEST SOUND on the SELECTED device: the stage box's own tone (stagebox.js, which waits for the box to run
    // it), or a short tone on a show-audio output of the robot's (Choreography's /api/choreo/audio/test).
    $("pjAudTest").addEventListener("click", async () => {
      const o = $("pjAudMain").value;
      if (!o) return;
      if (o === "stage") {
        if (window.OpenDubsStageBox) return window.OpenDubsStageBox.test((text, code) => mainSay(text, code));
        return mainSay("Test sound: the stage box card is not loaded");
      }
      mainSay(`Test sound on the ${AUD_NAMES[o].toLowerCase()}…`);
      try {
        const r = await fetch("/api/choreo/audio/test", { method: "POST", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ output: o }) });
        const j = await r.json().catch(() => ({}));
        mainSay(r.ok && j.ok !== false ? `Test sound sent to the ${AUD_NAMES[o].toLowerCase()}: did you hear it?`
          : `Test sound: ${j.error || j.detail || `HTTP ${r.status}`}`, r.ok ? null : j.code);
      } catch (err) { mainSay(`Test sound: ${err.message}`); }
    });
  }

  setInterval(poll, POLL_MS);
  poll();
})();
