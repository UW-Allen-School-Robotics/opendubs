/* OpenDubs dashboard — the Shows page: the routine palette (choreography plan 4.4).
   Lists the show runner's routines (through the dashboard's own /api/choreo/), runs the one the
   operator taps — starting it the way the Start picker says — and offers STOP. One routine at a
   time; the runner refuses a second.
   Works in any browser on plain HTTP or HTTPS (standing rule 12): nothing here needs the tablet. */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  if (!el("view-shows")) return;
  let routines = [], status = null, timer = null, listKey = "";

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || body.detail || `HTTP ${r.status}`);
    return body;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* What a routine will do, as icons on its button (from the runner's `uses`). A faded icon plays in
     the twin only: the robot has no output for that part yet. */
  const PART = {
    music:    ["MUSIC", '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'],
    arms:     ["ARMS", '<path d="M3 21h9M7.5 21v-3.5"/><circle cx="7.5" cy="15.5" r="2"/><path d="M9 14l5-6.5"/><circle cx="15" cy="6.5" r="1.6"/><path d="M16.5 7.5l3.5 2.5M20 10l1.5-2M20 10l1.5 2"/>'],
    grippers: ["GRIP", '<path d="M8 3v6l-3 4v8M16 3v6l3 4v8M8 9h8"/>'],
    head:     ["HEAD", '<rect x="7" y="6" width="10" height="9" rx="2"/><path d="M12 15v4M9 19h6M2.5 10.5l2-2v4zM21.5 10.5l-2-2v4z"/>'],
    face:     ["FACE", '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0"/><path d="M9 9.5h.01M15 9.5h.01" stroke-width="2.6"/>'],
    sound:    ["SOUND", '<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11"/>'],
    light:    ["LIGHT", '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9V16h7v-2.1A6 6 0 0 0 12 3z"/>'],
    lift:     ["LIFT", '<path d="M12 3v18M7 8l5-5 5 5M7 16l5 5 5-5"/>'],
    device:   ["LEVELS", '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'],
    base:     ["BASE", '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.2"/><path d="M12 3v6.8M12 14.2V21M3 12h6.8M14.2 12H21"/>'],
  };
  function usesHTML(uses) {
    return (uses || []).map((u) => {
      const p = PART[u.part]; if (!p) return "";
      const why = u.live ? `${p[0].toLowerCase()}: on the robot` : `${p[0].toLowerCase()}: in the twin only, not on the robot yet`;
      return `<span class="use ${u.live ? "" : "twin"}" title="${esc(why)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${p[1]}</svg>${p[0]}</span>`;
    }).join("");
  }

  function renderAudio() {
    const A = window.OpenDubsShowAudio, a = A ? A.status() : null;
    let html;
    if (!a || !a.connected) html = '<span class="dim">Show audio: not connected</span>';
    else if (!a.holder) html = '<span class="bad">Show audio: no device — routines run silently</span>';
    else if (a.youHold) {
      const clk = a.clock ? ` · clock ±${Math.max(1, Math.round(a.clock.error_ms))} ms` : " · matching clocks…";
      const sy = a.sync || {};
      const playingTxt = sy.err_ms == null ? "playing"
        : `playing · ${Math.abs(sy.err_ms) <= 5 ? "in step" : (sy.err_ms > 0 ? "catching up " : "easing back ") + Math.abs(sy.err_ms) + " ms"}`;
      const what = { loading: "loading…", ready: "ready", playing: playingTxt, error: `error: ${a.error}` }[a.state] || "standing by";
      html = `Show audio: <b>this device</b> — ${esc(what)}<span class="dim">${clk}</span>`;
    } else html = `Show audio: <b>${esc(a.holder)}</b>`;
    if (status && status.audio_note && status.running) html += ` <span class="bad">· ${esc(status.audio_note)}</span>`;
    el("showsAudio").innerHTML = html;
    el("showsAudioClaim").style.display = a && a.connected && !a.youHold ? "" : "none";
  }

  function render() {
    renderAudio();
    const running = status && status.running;
    // A runner with the arms drives them on every run (CH5 AUTO + the deadman are the go); one
    // without them drives only the twins. The badge says which this robot is.
    const badge = el("showsTwin");
    const armsFollow = !!(status && status.live_available), liveRun = !!(status && status.live);
    if (badge) {
      const waiting = liveRun && ["VALIDATING", "PREFLIGHT", "ARMED_WAITING", "COUNTDOWN"].includes(status.status);
      badge.textContent = waiting ? "LIVE · ARMED, NOTHING MOVES YET" : liveRun ? "LIVE · THE ARMS ARE MOVING" : armsFollow ? "LIVE · THE ARMS FOLLOW EVERY RUN" : "TWIN ONLY · THE ROBOT DOES NOT MOVE";
      badge.classList.toggle("livebadge", armsFollow);
    }
    el("showsStop").disabled = !running;
    el("showsPark").disabled = !!running;   // the runner owns the arms while a routine runs
    const last = status && status.last;
    const without = (parts) => (parts && parts.length ? ` <span class="bad">without ${esc(parts.join(", ").replace(/_/g, " "))}</span>` : "");
    // A part the show needs is unavailable, but the show can do without it: the operator approves
    // the variant here, and only here (the runner never plays one on its own).
    const offer = !running && last && last.status === "REFUSED" && (last.offer || []).length
      ? ` <button class="sysbtn shows-skip" data-id="${esc(last.id)}" data-skip="${esc(JSON.stringify(last.offer))}">RUN WITHOUT ${esc(last.offer.join(", ").replace(/_/g, " ").toUpperCase())}</button>` : "";
    el("showsStatus").innerHTML = running
      ? `<b class="shows-running">▶ ${esc(labelOf(running))}</b>${without(status.skipped)}${status.live ? ' <b class="bad">LIVE</b>' : ""} ${startHTML(status)}`
      : last ? `Last: <b>${esc(labelOf(last.id))}</b>${without(last.skipped)} — <span class="${last.status === "COMPLETED" ? "ok" : "bad"}">${esc(last.status)}</span> <span class="dim">${esc(last.cause || "")}</span>${offer}`
      : "Pick a routine.";
    /* Rebuild the buttons only when something about them changed: rebuilding every poll would
       replace a button under a finger mid-tap, and the tap would be lost. */
    const key = JSON.stringify([routines.map((r) => [r.id, r.valid, r.duration_s, r.warnings, r.uses]), running]);
    if (key === listKey) return;
    listKey = key;
    el("showsList").innerHTML = routines.map((r) => `
      <button class="shows-btn ${r.valid ? "" : "invalid"} ${running === r.id ? "active" : ""}" data-id="${esc(r.id)}"
        ${!r.valid || running ? "disabled" : ""}
        title="${esc(r.valid ? `${r.duration_s} s${r.warnings ? ` · ${r.warnings} warning(s)` : ""}` : `Does not validate: ${(r.problems || []).join("; ")}`)}">
        <span class="lbl">${esc(r.label)}</span><span class="dur">${r.valid ? `${Math.round(r.duration_s)} s` : "invalid"}</span>
        <span class="uses">${usesHTML(r.uses)}</span>
      </button>`).join("") || '<div class="dim">No routines.</div>';
  }
  /* ARMED_WAITING and COUNTDOWN in words: what the operator must do next, and how long is left. */
  function startHTML(s) {
    const si = s.start_info || {}, left = si.left_s != null ? ` · ${Math.ceil(si.left_s)} s left` : "";
    if (s.status === "ARMED_WAITING" && si.audio_waiting)
      return `<b class="shows-armed">ARMED</b> — getting the show's audio and video ready (a few seconds); then press the deadman`;
    if (s.status === "ARMED_WAITING")
      return `<b class="shows-armed">ARMED</b> — ${si.deadman === true ? "let go of the deadman, then press it to start"
        : si.deadman === false ? "press the deadman to start" : "the deadman is unknown"}<span class="dim">${esc(left)}</span>`;
    if (s.status === "COUNTDOWN")
      return `<b class="shows-armed">STARTING in ${Math.ceil(si.left_s || 0)} s</b> — keep the deadman held`;
    return `<span class="dim">${esc(s.status || "")}</span>`;
  }
  const labelOf = (id) => (routines.find((r) => r.id === id) || {}).label || id;

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
      el("showsStatus").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
      el("showsList").innerHTML = ""; el("showsStop").disabled = true;
      return;
    } finally {
      busy = false;
    }
    render();
  }

  async function runRoutine(id, skip) {
    const body = {}, start = el("showsStart").value, cd = el("showsCountdown").value.trim();
    if (start) body.start = start;
    if (cd !== "") body.countdown_s = Number(cd);
    if (skip) body.skip = skip;
    try {
      await getJSON(`/api/choreo/run/${encodeURIComponent(id)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    }
    catch (err) { el("showsStatus").innerHTML = `<span class="bad">${esc(err.message)}</span>`; }
    refresh(false);
  }
  el("showsList").addEventListener("click", (e) => {
    const b = e.target.closest(".shows-btn"); if (!b || b.disabled) return;
    runRoutine(b.dataset.id);
  });
  el("showsStatus").addEventListener("click", (e) => {
    const b = e.target.closest(".shows-skip"); if (!b) return;
    b.disabled = true;
    runRoutine(b.dataset.id, JSON.parse(b.dataset.skip));
  });
  el("showsAudioClaim").addEventListener("click", () => {
    if (window.OpenDubsShowAudio) window.OpenDubsShowAudio.claim();
    setTimeout(renderAudio, 300);
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
  new MutationObserver(() => {
    const on = el("view-shows").classList.contains("active");
    if (on && !timer) { refresh(true); timer = setInterval(() => refresh(false), 1000); }
    if (!on && timer) { clearInterval(timer); timer = null; }
  }).observe(el("view-shows"), { attributes: true, attributeFilter: ["class"] });
})();
