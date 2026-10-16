/* OpenDubs dashboard — the HEAD & FACE page: what the robot's face does when no show is running.

   The settings live on the robot (/api/head/settings, backend/head_settings.py) and reach the face
   at once over its door; a show overrides them with its own cues and returns to them when it ends.
   Ambient music is the show-audio lane's (/api/choreo/ambient); this page only drives it.
   Works in any browser on the robot's network, like every dashboard page. */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const view = el("view-headface");
  if (!view) return;

  const CSS = `
    .hf-card { display: grid; gap: 14px; align-content: start; }
    .hf-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .hf-row > label:first-child { min-width: 190px; color: var(--dim); }
    .hf-row input[type=range] { width: 220px; }
    .hf-row select { font: inherit; padding: 6px 8px; background: transparent; color: var(--txt);
                     border: 1px solid var(--line, #444); border-radius: 6px; }
    .hf-note { color: var(--dim); font-size: 13px; }
    .hf-val { min-width: 44px; font-variant-numeric: tabular-nums; }
    .hf-songs { display: grid; gap: 4px; max-height: 220px; overflow: auto; }
    .hf-status { font-size: 13px; }`;
  const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);

  view.innerHTML = `
    <section class="card hf-card">
      <h2>HEAD &amp; FACE <span id="hfSaved" class="badge dimbadge">—</span></h2>
      <div class="hf-note">What the face does when no show is running. A show overrides these with its own cues and comes back to them when it ends. Saved on the robot.</div>

      <h2 class="mt">SCREEN</h2>
      <div class="hf-row"><label for="hfScreen">Keep the screen on</label><input type="checkbox" id="hfScreen"></div>
      <div class="hf-row"><label for="hfReturn">Back to the face after</label>
        <select id="hfReturn"><option value="0">never</option><option value="1">1 minute</option><option value="2">2 minutes</option><option value="5">5 minutes</option><option value="10">10 minutes</option></select>
        <span class="hf-note">untouched on the dashboard (the robot's own tablet; never during a show)</span></div>
      <div class="hf-row"><label for="hfBright">Brightness</label><input type="range" id="hfBright" min="0.05" max="1" step="0.05"><span class="hf-val" id="hfBrightV"></span></div>
      <div class="hf-row"><label for="hfBrightMin">Never dimmer than</label><input type="range" id="hfBrightMin" min="0.05" max="1" step="0.05"><span class="hf-val" id="hfBrightMinV"></span>
        <span class="hf-note">A show can't go below this, so the face never goes dark by mistake.</span></div>

      <h2 class="mt">EYES</h2>
      <div class="hf-row"><label for="hfIdle">Expressions change by themselves</label><input type="checkbox" id="hfIdle"></div>
      <div class="hf-row"><label for="hfSkin">Look (skin)</label><select id="hfSkin"></select></div>
      <div class="hf-row"><label for="hfTheme">Colours (theme)</label><select id="hfTheme"><option value="plain">plain</option><option value="halloween">halloween</option></select></div>
      <div class="hf-row"><label for="hfMouth">Mouth</label><input type="checkbox" id="hfMouth">
        <span class="hf-note">Off by default. Nothing drives it yet (no voice or music moves it), so it shows a still mouth.</span></div>

      <h2 class="mt">LOOKING AT PEOPLE</h2>
      <div class="hf-row"><label for="hfTrack">Follow people with</label>
        <select id="hfTrack">
          <option value="off">off</option>
          <option value="tablet">the face's own camera</option>
          <option value="d415">the RealSense camera (on the robot)</option>
        </select></div>
      <div class="hf-row"><label for="hfReticle">Show tracking reticle</label><input type="checkbox" id="hfReticle">
        <span class="hf-note">A box on the face where it thinks the person is: amber for motion, cyan for a person or face. For checking the tracking; off by default.</span></div>
      <div class="hf-note" id="hfTrackNote"></div>
      <div class="hf-note" id="hfTrackLive"></div>
      <div class="hf-note" id="hfNeckNote"></div>
      <div class="hf-note">Turning toward a sound: not available. The tablet can't tell which direction a sound comes from.</div>

      <h2 class="mt">TABLET</h2>
      <div class="hf-row"><label>Kiosk app</label><span id="hfApp">…</span></div>
      <div class="hf-note" id="hfAppNote">The tablet updates its app from the robot by itself, over Wi-Fi, when no show is loaded.</div>

      <h2 class="mt">SOUND</h2>
      <div class="hf-row"><label for="hfAirplay">AirPlay</label><input type="checkbox" id="hfAirplay">
        <span class="hf-note" id="hfAirplayNote"></span></div>
      <div class="hf-row"><label for="hfVol">Volume</label><input type="checkbox" id="hfVolSet" title="Set it here, or leave the tablet's own volume alone"><input type="range" id="hfVol" min="0" max="1" step="0.05"><span class="hf-val" id="hfVolV"></span></div>
      <div class="hf-row"><label for="hfVolMax">Never louder than</label><input type="range" id="hfVolMax" min="0" max="1" step="0.05"><span class="hf-val" id="hfVolMaxV"></span></div>

      <div class="hf-note">Anyone on the robot's Wi-Fi can send it a sound from their phone: open <b id="hfPlayUrl"></b> in the phone's browser.</div>

      <h2 class="mt">AMBIENT MUSIC</h2>
      <div id="hfAmbient" class="hf-status dim">—</div>
      <div id="hfSongs" class="hf-songs"></div>
      <div class="hf-row">
        <button class="iobtn" id="hfPlay">PLAY</button><button class="iobtn" id="hfNext">NEXT</button><button class="iobtn" id="hfStop">STOP</button>
        <label for="hfShuffle" class="hf-note">shuffle</label><input type="checkbox" id="hfShuffle">
      </div>
      <div class="hf-note">The ticked songs and shuffle take effect when you press PLAY.</div>
    </section>`;

  let s = null, saveTimer = null;
  el("hfPlayUrl").textContent = `${location.protocol}//${location.host}/play/`;
  const pct = (v) => `${Math.round(v * 100)}%`;
  const say = (text, cls) => { const b = el("hfSaved"); b.textContent = text; b.className = `badge ${cls || "dimbadge"}`; };

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || body.detail || `HTTP ${r.status}`);
    return body;
  }

  function render() {
    el("hfScreen").checked = s.screen_on;
    el("hfAirplay").checked = s.airplay === "on"; el("hfAirplay").disabled = s.airplay === "absent";
    el("hfAirplayNote").textContent = s.airplay === "absent" ? "No AirPlay receiver is installed on this robot."
      : s.airplay === "on" ? "On: iPhones and Macs on the robot's Wi-Fi can play to it. A show always comes first."
      : "Off: the robot doesn't appear in AirPlay lists.";
    el("hfReturn").value = String(s.face_return_min);
    el("hfIdle").checked = s.idle_eyes;
    el("hfMouth").checked = s.mouth;
    el("hfReticle").checked = !!s.show_reticle;
    el("hfTheme").value = s.theme;
    el("hfTrack").value = s.tracking;
    el("hfBright").value = s.bright_idle; el("hfBrightV").textContent = pct(s.bright_idle);
    el("hfBrightMin").value = s.bright_min; el("hfBrightMinV").textContent = pct(s.bright_min);
    el("hfVolMax").value = s.vol_max; el("hfVolMaxV").textContent = pct(s.vol_max);
    const own = s.volume_idle === null;
    el("hfVolSet").checked = !own; el("hfVol").disabled = own;
    el("hfVol").value = own ? 0.5 : s.volume_idle;
    el("hfVolV").textContent = own ? "the tablet's own" : pct(s.volume_idle);
    if (el("hfSkin").value !== s.skin) el("hfSkin").value = s.skin;
    el("hfNeckNote").textContent = s.neck === "none"
      ? "No neck is fitted on this robot (OPENDUBS_HEAD_NECK_HOST=none): the head doesn't turn, so the eyes follow people but the head stays still."
      : (s.neck ? `Neck: ${s.neck}` : "");
    const rs = el("hfTrack").querySelector('option[value="d415"]');
    if (rs) {
      rs.disabled = !s.realsense && s.tracking !== "d415";
      rs.textContent = s.realsense ? "the RealSense camera (on the robot)" : "the RealSense camera (not connected)";
    }
    el("hfTrackNote").textContent = {
      off: "",
      tablet: "The face's own camera: the eyes follow people, and the head turns toward them when a neck is fitted.",
      d415: "The robot's RealSense tracker (the same as AI TRACKING on Operate). It aims the neck only; the face's eyes don't follow it.",
    }[s.tracking];
  }

  async function save(change, now) {
    clearTimeout(saveTimer);
    const go = async () => {
      try { s = await getJSON("/api/head/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change) });
            render(); say("saved", "good"); }
      catch (e) { say(`not saved: ${e.message}`, "bad"); load(); }
    };
    if (now) go(); else saveTimer = setTimeout(go, 250);     // sliders: once they settle
  }

  async function loadSkins() {
    // The code skins, read from the face engine itself so this list can't drift from it.
    let code = ["default", "ring"];
    try {
      const js = await (await fetch("/face/engine/skins/index.js", { cache: "no-store" })).text();
      const line = js.split("\n").find((l) => l.startsWith("export const CODE_SKINS"));
      if (line) code = [...line.split("=")[1].matchAll(/(\w+)\s*(?::\s*[\w.]+)?\s*[,}]/g)].map((m) => m[1]);
    } catch (e) {}
    let lib = [];
    try { lib = (await getJSON("/api/library/index")).filter((e) => e.kind === "skin").map((e) => e.id); } catch (e) {}
    el("hfSkin").innerHTML = code.map((n) => `<option value="${n}">${n}</option>`).join("") +
      (lib.length ? `<optgroup label="library">${lib.map((n) => `<option value="${n}">${n}</option>`).join("")}</optgroup>` : "");
  }

  // What the face's tracker is doing right now (the face reports it every 2 s; /api/head/follow).
  async function loadTracker() {
    let f = null;
    try { f = await getJSON("/api/head/follow"); } catch (e) { /* an older robot */ }
    const t = f && f.tracker;
    const line = !t ? "Tracker: no report from the face yet."
      : t.age_s > 10 ? `Tracker: no report for ${Math.round(t.age_s)} s (is the face showing?).`
      : t.state === "running"
        ? `Tracker: running, ${t.hz} Hz, ${t.found_pct}% of frames find someone` +
          (t.kind && t.kind !== "none" ? `, now following ${t.kind === "motion" ? "MOTION" : t.kind === "person" ? "a PERSON" : "a FACE"} (${Math.round(100 * (t.confidence || 0))}%)` : "") +
          (t.person_model ? ` [person detector: ${t.person_model}]` : "") +
          (t.frame ? `, camera ${t.frame[0]}×${t.frame[1]}` : "") +
          (t.last_found_s != null ? `, last seen ${t.last_found_s} s ago` : ", nobody seen yet")
        : `Tracker: ${t.state}.`;
    el("hfTrackLive").textContent = line + (f && f.why ? ` Head: ${f.why}.` : "");
  }
  setInterval(() => { if (view.classList.contains("active")) loadTracker(); }, 3000);

  async function load() {
    loadTracker();
    try { s = await getJSON("/api/head/settings"); render(); say("saved on the robot"); }
    catch (e) { say(`unavailable: ${e.message}`, "bad"); }
    loadApp();
  }

  // The kiosk app: what the tablet runs, what the robot serves, and what the updater last decided.
  async function loadApp() {
    let t = null, served = null;
    try { t = await getJSON("/api/tablet/sensors"); } catch (e) { /* no relay */ }
    try { served = await getJSON("/api/kiosk/latest"); } catch (e) { /* this robot serves no build */ }
    const a = t && t.app;
    const runs = a ? `the tablet runs ${a.version}` : "the tablet hasn't reported (an app older than 0.3.0, or not connected)";
    const serves = served ? `; the robot serves ${served.versionName || served.versionCode}` : "; the robot serves no build";
    el("hfApp").textContent = runs + serves;
    const u = a && a.update;
    if (u && u.state) el("hfAppNote").textContent = `Updates: ${u.state}${u.detail ? ` (${u.detail})` : ""}` +
      (u.at ? `, ${Math.max(0, Math.round((Date.now() - u.at) / 60000))} min ago` : "");
  }

  // ---- ambient music (the show-audio lane's API) ------------------------------------------------
  let amb = null;
  async function ambient(op) {
    try {
      amb = await getJSON("/api/choreo/ambient", op ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(op) } : undefined);
    } catch (e) {
      el("hfAmbient").textContent = `Ambient music isn't available on this robot yet (${e.message}).`;
      ["hfPlay", "hfNext", "hfStop"].forEach((id) => { el(id).disabled = true; });
      return;
    }
    const st = amb.state || {};
    el("hfAmbient").textContent = amb.show_active ? "A show is running: ambient music waits for it to end."
      : st.playing ? `Playing: ${st.now_playing || "—"}` : "Stopped.";
    el("hfShuffle").checked = !!st.shuffle;
    if (!op) {
      const chosen = new Set(st.songs && st.songs.length ? st.songs : (amb.songs || []).filter((x) => x.ambient).map((x) => x.id));
      el("hfSongs").innerHTML = (amb.songs || []).map((x) =>
        `<label class="hf-row"><input type="checkbox" value="${x.id}" ${chosen.has(x.id) ? "checked" : ""}> ${x.title || x.id}${x.ambient ? ' <span class="hf-note">ambient</span>' : ""}</label>`).join("") ||
        '<span class="hf-note">No songs in the library.</span>';
    }
  }
  const pickedSongs = () => [...el("hfSongs").querySelectorAll("input:checked")].map((i) => i.value);
  el("hfPlay").addEventListener("click", () => ambient({ op: "play", songs: pickedSongs(), shuffle: el("hfShuffle").checked }));
  el("hfNext").addEventListener("click", () => ambient({ op: "next" }));
  el("hfStop").addEventListener("click", () => ambient({ op: "stop" }));

  // ---- controls -----------------------------------------------------------------------------------
  const onChange = (id, fn) => el(id).addEventListener("change", fn);
  onChange("hfScreen", (e) => save({ screen_on: e.target.checked }, true));
  onChange("hfAirplay", (e) => { el("hfAirplayNote").textContent = e.target.checked ? "Turning AirPlay on…" : "Turning AirPlay off…";
                                 save({ airplay: e.target.checked }, true); });
  onChange("hfReturn", (e) => save({ face_return_min: Number(e.target.value) }, true));
  onChange("hfIdle", (e) => save({ idle_eyes: e.target.checked }, true));
  onChange("hfMouth", (e) => save({ mouth: e.target.checked }, true));
  onChange("hfReticle", (e) => save({ show_reticle: e.target.checked }, true));
  onChange("hfTheme", (e) => save({ theme: e.target.value }, true));
  onChange("hfSkin", (e) => save({ skin: e.target.value }, true));
  onChange("hfTrack", async (e) => {
    // The robot starts or stops the RealSense tracker's service with the setting itself.
    save({ tracking: e.target.value }, true);
  });
  onChange("hfVolSet", (e) => save({ volume_idle: e.target.checked ? Number(el("hfVol").value) : null }, true));
  for (const [id, key, label] of [["hfBright", "bright_idle", "hfBrightV"], ["hfBrightMin", "bright_min", "hfBrightMinV"],
                                  ["hfVolMax", "vol_max", "hfVolMaxV"], ["hfVol", "volume_idle", "hfVolV"]]) {
    el(id).addEventListener("input", (e) => { el(label).textContent = pct(Number(e.target.value)); save({ [key]: Number(e.target.value) }); });
  }

  // Load when the page is opened, so it is always current.
  let wasOn = false;
  setInterval(() => {
    const on = view.classList.contains("active");
    if (on && !wasOn) { loadSkins().then(load); ambient(); }
    wasOn = on;
  }, 500);
})();
