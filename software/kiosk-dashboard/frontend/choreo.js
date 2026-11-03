/* OpenDubs dashboard — the Shows page (choreography plan 4.4; notes/design/choreo-shows-page.md).
   Three steps and a fold:
     1 BEFORE YOU START  where the video and the sound go, each ready or why not, and the robot. The Video
                         and Sound rows are the Tablet lane's (projector.js, stagebox.js): until a row is
                         claimed (data-owner) this file draws a plain one, so the page works either way.
     2 PICK A SHOW       a tap SELECTS (it never starts); each show says where its video and sound will go
                         and why: your choice, the show's own, or the robot's default (the runner's plays_on).
     3 GO                GO arms the selected show; STOP; the refusal, with its code, right beside GO.
     ADVANCED            start mode, this browser as the tablet, and the Tablet lane's timing, stage box
                         setup and diagnostics.
   One routine at a time; the runner refuses a second. Any browser, plain HTTP or HTTPS (standing rule 12). */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  if (!el("view-shows")) return;
  let routines = [], status = null, robot = null, video = null, sound = null, timer = null;
  let listKey = "", selected = null, runError = null, choiceKey = null;

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(body.error || body.detail || `HTTP ${r.status}`); e.code = body.code; throw e; }
    return body;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const VID = { stage: "stage screen", cast: "projector", hdmi: "HDMI", off: "off" };
  const SND = { stage: "stage box", tablet: "head tablet", speakerphone: "speakerphone" };
  const PART = { music: "music", arms: "arms", grippers: "grippers", head: "head", face: "face", sound: "sounds",
                 light: "light", lift: "lift", device: "head levels", base: "base" };
  const help = (code) => (code && typeof window.helpLink === "function" ? window.helpLink(code).outerHTML : "");

  /* ---- step 1: the Robot line (this file), and plain Video / Sound rows until the Tablet lane's arrive ---- */
  function dotLine(ok, text) { return `<span class="sdot ${ok === true ? "ok" : ok === false ? "bad" : ""}"></span>${text}`; }
  function renderRobot() {
    const r = robot;
    if (!r) { el("showsRobotRow").innerHTML = rowHTML("Robot", "checked again at GO", dotLine(null, "not known")); return; }
    const bits = [
      dotLine(r.ch5 === "AUTO", `mode ${esc(r.ch5 || "?")}${r.ch5 && r.ch5 !== "AUTO" ? " — shows need AUTO" : ""}`),
      dotLine(r.estop === false, r.estop ? "E-STOP pressed" : r.estop === false ? "e-stop clear" : "e-stop unknown"),
      dotLine(!r.fault, r.fault ? `fault: ${esc(r.fault)}` : "no faults"),
    ];
    if (r.head != null) bits.push(dotLine(r.head === true, r.head ? "head up" : "head not showing its face"));
    el("showsRobotRow").innerHTML = rowHTML("Robot", "checked again at GO", bits.join(" · "));
  }
  function rowHTML(name, sub, body, ctl = "") {
    return `<div class="shows-k"><b>${name}</b>${sub}</div><div class="shows-v">${body}</div>${ctl}`;
  }
  // A row the Tablet lane's projector.js has filled (its .pj-main, or a data-owner): this file's plain
  // row, if it drew one first, goes, and is not drawn again.
  const owned = (id) => {
    const row = el(id), theirs = !!(row.dataset.owner || row.querySelector(".pj-main"));
    if (theirs) row.querySelectorAll(":scope > .shows-fb").forEach((n) => n.remove());
    return theirs;
  };
  const sentence = (t) => (/[.!?]$/.test(t) ? t : t + ".");
  function renderFallbackRows() {
    if (!owned("showsVideoRow") && video) {
      const outs = video.outputs || {}, cur = video.override || "";
      const opts = [["stage", "Stage screen"], ["", "Each show decides"], ["cast", "Projector (cast)"]]
        .concat(outs.hdmi && outs.hdmi.present ? [["hdmi", "HDMI"]] : []).concat([["off", "Off: no video"]])
        .filter(([v]) => v !== "stage" || outs.stage);
      const sel = cur ? outs[cur] : null;
      const line = !cur ? "each show's own output (see each show below)"
        : cur === "off" ? "no video for any show"
        : dotLine(!!(sel && sel.ready), sel && sel.ready ? `<b>ready</b>` : `<b>not ready</b> — ${esc((sel && sel.why) || "not set up")}`);
      paintRow("showsVideoRow", "Video plays on", "for every show you start", opts, cur, line, "video");
    }
    if (!owned("showsSoundRow") && sound) {
      const byName = Object.fromEntries((sound.outputs || []).map((o) => [o.name, o]));
      const cur = sound.override || "";
      const opts = [["stage", "Stage box speaker"], ["", "Each show decides"], ["tablet", "Head tablet"], ["speakerphone", "Speakerphone"]];
      const o = cur ? byName[cur] : null;
      const line = !cur ? "each show's own speaker (see each show below)"
        : dotLine(!!(o && o.ready), o && o.ready ? `<b>ready</b>${o.label ? ` · ${esc(o.label)}` : ""}`
          : `<b>not ready</b> — ${esc(sentence((o && o.why) || "not connected"))} A show started now is refused.`);
      paintRow("showsSoundRow", "Sound plays on", "for every show you start", opts, cur, line, "sound",
               cur ? `<button class="sysbtn shows-test" data-test="${esc(cur)}">TEST SOUND</button>` : "");
    }
  }
  function paintRow(id, name, sub, opts, cur, line, kind, extra = "") {
    const row = el(id);
    let fb = row.querySelector(":scope > .shows-fb");
    if (!fb) { fb = document.createElement("div"); fb.className = "shows-fb"; row.appendChild(fb); }
    const sel = fb.querySelector("select");
    if (sel && document.activeElement === sel) return;           // never redraw under a finger
    fb.innerHTML = rowHTML(name, sub, line,
      `<select data-kind="${kind}" aria-label="${name}">${opts.map(([v, t]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${t}</option>`).join("")}</select>${extra}`);
  }
  el("view-shows").addEventListener("change", async (e) => {
    const s = e.target.closest("select[data-kind]"); if (!s) return;
    const url = s.dataset.kind === "video" ? "/api/choreo/video/output" : "/api/choreo/audio/output";
    try { await getJSON(url, { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({ output: s.value || null }) }); runError = null; }
    catch (err) { runError = { text: `Not changed: ${err.message}`, code: err.code }; }
    pollOutputs().then(() => refresh(true));
  });
  el("view-shows").addEventListener("click", async (e) => {
    const b = e.target.closest(".shows-test"); if (!b) return;
    b.disabled = true;
    const out = b.dataset.test;
    try {
      if (out === "stage") await getJSON("/api/stage/box/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: "test" }) });
      else await getJSON("/api/choreo/audio/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ output: out }) });
      b.textContent = "TEST SENT";
    } catch (err) { b.textContent = "TEST FAILED"; b.title = err.message; }
    setTimeout(() => { b.disabled = false; b.textContent = "TEST SOUND"; }, 2500);
  });
  // The Tablet lane's older cards (before they fill the rows): kept, but inside the fold, out of the way.
  function foldOldCards() {
    if (owned("showsVideoRow")) return;
    for (const c of el("view-shows").querySelectorAll(":scope > section.pj-card")) el("showsAdvDiag").appendChild(c);
  }

  /* ---- step 2: the list ---- */
  function playsHTML(p) {
    if (!p) return "";
    const one = (what, val, from, own, names) => {
      if (!val) return `no ${what}`;
      const not = from === "your choice" && own && own !== val ? `, not the show's own: ${names[own] || own}` : "";
      return `${what} <b>${esc(names[val] || val)}</b> <span class="${not ? "shows-over" : "dim"}">(${esc(from)}${esc(not)})</span>`;
    };
    return `${one("video", p.video, p.video_from, p.video_own, VID)} · ${one("sound", p.sound, p.sound_from, p.sound_own, SND)}`;
  }
  function partsHTML(uses) {
    const live = (uses || []).filter((u) => u.live).map((u) => PART[u.part] || u.part);
    const twin = (uses || []).filter((u) => !u.live).map((u) => PART[u.part] || u.part);
    return `<span title="${esc(twin.length ? `in the twin only, not on the robot yet: ${twin.join(", ")}` : "")}">${esc(live.join(" · ") || "twin only")}</span>`;
  }
  function renderList(running) {
    const key = JSON.stringify([routines.map((r) => [r.id, r.valid, r.duration_s, r.uses, r.plays_on]), running, selected]);
    if (key === listKey) return;                  // rebuilding mid-tap would lose the tap
    listKey = key;
    el("showsList").innerHTML = routines.map((r) => `
      <button class="shows-btn ${r.valid ? "" : "invalid"} ${selected === r.id ? "active" : ""} ${running === r.id ? "running" : ""}" data-id="${esc(r.id)}"
        ${running ? "disabled" : ""}
        title="${esc(r.valid ? `${r.duration_s} s${r.warnings ? ` · ${r.warnings} warning(s)` : ""}` : `Does not validate: ${(r.problems || []).join("; ")}`)}">
        <span class="lbl">${esc(r.label)}<span class="dur">${r.valid ? `${Math.floor(r.duration_s / 60)}:${String(Math.round(r.duration_s % 60)).padStart(2, "0")}` : "can't run"}</span></span>
        <span class="plays">${r.valid ? playsHTML(r.plays_on) : `<span class="bad">${esc((r.problems || [])[0] || "does not validate")}</span>`}</span>
        <span class="parts">${partsHTML(r.uses)}</span>
      </button>`).join("") || '<div class="dim">No shows on this robot.</div>';
  }
  el("showsList").addEventListener("click", (e) => {
    const b = e.target.closest(".shows-btn"); if (!b || b.disabled) return;
    selected = b.dataset.id; runError = null;
    render();
  });

  /* ---- step 3: GO, STOP and what happened ---- */
  const labelOf = (id) => (routines.find((r) => r.id === id) || {}).label || id;
  /* READY (the runner's ARMED_WAITING) and STARTING in words: what the operator must do next, and how long
     is left. The words are the runner's (status.phase, PHASE_WORDS), so every screen says the same. */
  function startHTML(s) {
    const si = s.start_info || {}, left = si.left_s != null ? ` · ${Math.ceil(si.left_s)} s left` : "";
    const ph = s.phase || {};
    if (s.status === "ARMED_WAITING" && si.audio_waiting)
      return `<b class="shows-armed">READY</b> — getting the show's audio and video ready (a few seconds); nothing moves until you press and hold the deadman`;
    if (s.status === "ARMED_WAITING")
      return `<b class="shows-armed">READY</b> — nothing moves until you ${si.deadman === true ? "let go of the deadman, then press and hold it"
        : si.deadman === false ? "press and hold the deadman" : "press and hold the deadman (its state is unknown)"}<span class="dim">${esc(left)}</span>`;
    if (s.status === "COUNTDOWN")
      return `<b class="shows-armed">STARTING in ${Math.ceil(si.left_s || 0)} s</b> — the robot will move; keep the deadman held`;
    return `<span class="dim">${esc(ph.word || s.status || "")}${ph.say ? " — " + esc(ph.say) : ""}</span>`;
  }
  function renderGo(running) {
    const last = status && status.last;
    const sel = routines.find((r) => r.id === selected);
    const go = el("showsGo");
    go.disabled = !!running || !sel || !sel.valid;
    go.innerHTML = `GO<small>${esc(sel ? sel.label : "pick a show")}</small>`;
    el("showsStop").disabled = !running;
    el("showsPark").disabled = !!running;            // the runner owns the arms while a routine runs
    const without = (parts) => (parts && parts.length ? ` <span class="bad">without ${esc(parts.join(", ").replace(/_/g, " "))}</span>` : "");
    let html, bad = false;
    if (running) {
      html = `<b class="shows-running">▶ ${esc(labelOf(running))}</b>${without(status.skipped)}${status.live ? ' <b class="bad">LIVE</b>' : ""}<br>${startHTML(status)}`
        + (status.audio_note ? `<br><span class="bad">${esc(status.audio_note)}</span>` : "");
    } else if (runError) {
      bad = true;
      html = `<b>${esc(sel ? labelOf(sel.id) : "")} did not start</b><br>${esc(runError.text)} ${help(runError.code)}`;
    } else if (last && (!selected || last.id === selected)) {
      bad = last.status !== "COMPLETED";
      const offer = last.status === "REFUSED" && (last.offer || []).length
        ? `<br><button class="sysbtn shows-skip" data-id="${esc(last.id)}" data-skip="${esc(JSON.stringify(last.offer))}">RUN WITHOUT ${esc(last.offer.join(", ").replace(/_/g, " ").toUpperCase())}</button>` : "";
      html = `<b>${esc(labelOf(last.id))}${without(last.skipped)} — ${esc((status.last_phase || {}).word || last.status)}</b>`
        + (last.cause ? `<br>${esc(last.cause)}` : "") + (bad && last.code ? ` ${help(last.code)}` : "") + offer;
    } else html = sel ? `<b>${esc(sel.label)}</b> selected: GO makes it READY, and nothing moves until the deadman.` : "Pick a show above.";
    const box = el("showsStatus");
    box.className = "shows-status" + (bad ? " refused" : "");
    box.innerHTML = html;
  }
  function render() {
    const running = status && status.running;
    const badge = el("showsTwin");
    const armsFollow = !!(status && status.live_available), liveRun = !!(status && status.live);
    if (badge) {
      // the runner's words for the state (its PHASE_WORDS): READY, never "armed"
      const st = status && status.status;
      badge.textContent = liveRun && (st === "VALIDATING" || st === "PREFLIGHT") ? "LIVE · CHECKING, NOTHING MOVES YET"
        : liveRun && st === "ARMED_WAITING" ? "LIVE · READY, NOTHING MOVES UNTIL YOU HOLD THE DEADMAN"
        : liveRun && st === "COUNTDOWN" ? "LIVE · STARTING, THE ROBOT WILL MOVE"
        : liveRun ? "LIVE · THE ROBOT IS MOVING" : armsFollow ? "LIVE · THE ROBOT MOVES ON EVERY RUN" : "TWIN ONLY · THE ROBOT DOES NOT MOVE";
      badge.classList.toggle("livebadge", armsFollow);
    }
    renderAudioLine();
    renderRobot();
    renderFallbackRows();
    foldOldCards();
    renderList(running);
    renderGo(running);
  }

  /* ---- Advanced: this browser as the head tablet's speaker ---- */
  function renderAudioLine() {
    const A = window.OpenDubsShowAudio, a = A ? A.status() : null;
    let html;
    if (!a || !a.connected) html = "Show audio: not connected";
    else if (!a.holder) html = "Head tablet sound: no device holds it";
    else if (a.youHold) html = `Head tablet sound: <b>this browser</b> — ${esc(a.state || "standing by")}`;
    else html = `Head tablet sound: <b>${esc(a.holder)}</b>`;
    el("showsAudio").innerHTML = html;
    el("showsAudioClaim").style.display = a && a.connected && !a.youHold ? "" : "none";
  }
  el("showsAudioClaim").addEventListener("click", () => {
    if (window.OpenDubsShowAudio) window.OpenDubsShowAudio.claim();
    setTimeout(renderAudioLine, 300);
  });

  /* ---- polling ---- */
  let busy = false;      // one request at a time: a slow list must not stack up repeats on the runner
  async function refresh(full) {
    if (busy) return;
    busy = true;
    try {
      if (full || !routines.length) routines = await getJSON("/api/choreo/routines");
      status = await getJSON("/api/choreo/palette");
      const d = el("showsStart").options[0], def = status.default_start;
      if (def) d.textContent = `default (${{ deadman: "wait for the deadman", countdown: "count down now", now: "now" }[def] || def})`;
    } catch (e) {
      routines = []; status = null; listKey = "";
      el("showsStatus").className = "shows-status refused";
      el("showsStatus").innerHTML = `<b>The show runner isn't answering</b><br>${esc(e.message)}`;
      el("showsList").innerHTML = ""; el("showsStop").disabled = true; el("showsGo").disabled = true;
      return;
    } finally {
      busy = false;
    }
    render();
  }
  async function pollOutputs() {
    const [v, s, r] = await Promise.all([
      getJSON("/api/choreo/video").catch(() => null), getJSON("/api/choreo/audio/output").catch(() => null),
      getJSON("/api/choreo/robot").catch(() => null)]);
    video = v; sound = s; robot = r;
    // the operator's choices change where every show plays: the list's plays_on is re-read when they do
    const key = JSON.stringify([v && v.override, s && s.override]);
    if (choiceKey !== null && key !== choiceKey) refresh(true);
    choiceKey = key;
  }

  async function runRoutine(id, skip) {
    const body = {}, start = el("showsStart").value, cd = el("showsCountdown").value.trim();
    if (start) body.start = start;
    if (cd !== "") body.countdown_s = Number(cd);
    if (skip) body.skip = skip;
    runError = null;
    try {
      await getJSON(`/api/choreo/run/${encodeURIComponent(id)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (err) { runError = { text: err.message, code: err.code }; }
    refresh(false);
  }
  el("showsGo").addEventListener("click", () => { if (selected) runRoutine(selected); });
  el("showsStatus").addEventListener("click", (e) => {
    const b = e.target.closest(".shows-skip"); if (!b) return;
    b.disabled = true;
    runRoutine(b.dataset.id, JSON.parse(b.dataset.skip));
  });
  // Park: the arms page's PARK BOTH, here too, so a stopped run can be tidied up from this page.
  if (typeof window.confirmButton === "function") {
    const park = el("showsPark");
    window.confirmButton(park, { url: () => "/api/arms/park?arm=both", confirm: "CONFIRM PARK",
                                 pending: "PARKING…", label: () => { park.textContent = "PARK BOTH ARMS"; } });
  }
  el("showsStop").addEventListener("click", async () => {
    await getJSON("/api/choreo/stop", { method: "POST" }).catch(() => {});
    refresh(false);
  });

  /* Poll only while the page is on screen. */
  let otimer = null;
  new MutationObserver(() => {
    const on = el("view-shows").classList.contains("active");
    if (on && !timer) {
      pollOutputs().then(() => refresh(true));
      timer = setInterval(() => refresh(false), 1000);
      otimer = setInterval(() => pollOutputs().then(render), 2000);
    }
    if (!on && timer) { clearInterval(timer); clearInterval(otimer); timer = otimer = null; }
  }).observe(el("view-shows"), { attributes: true, attributeFilter: ["class"] });
})();
