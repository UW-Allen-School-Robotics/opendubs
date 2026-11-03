/* OpenDubs dashboard — the STAGE BOX SOUND card on the Shows page: where the stage box plays the show's music.

   The stage box (software/stage-pi) plays the show's music through ONE output: the screen's speaker (HDMI),
   the Pi's 3.5 mm jack (a Pi 4 only), a USB audio adapter, or a Bluetooth speaker paired to it; or auto.
   The robot's dialogue stays on the head tablet. This card shows what is ACTUALLY playing where, and sets
   the output, a volume per output, the Bluetooth speaker (pair: scan, then choose it) and a test tone.
   Through GET /api/stage/box and POST /api/stage/box/command (stage_box.py): the box pulls the commands
   every 2 s and reports each one's result. Everything but the volume is refused while a show is loaded or
   playing. A card of its own, beside the projector card (projector.js).

   On the Shows page in three steps (Choreography's layout, 2026-10-28) this card is the stage box's SETUP and
   goes under Advanced (#showsAdvStageBox); step 1's "Sound plays on" row (projector.js) gets the volume of
   the speaker the box plays through now (#sbMainVol), and its TEST SOUND for the stage box comes here
   (window.OpenDubsStageBox.test). With no such container the card stays where it was. */
"use strict";
(() => {
  const view = document.getElementById("view-shows");
  if (!view) return;
  const POLL_MS = 2000, STEP = 0.05;
  const NAMES = { hdmi: "HDMI (the screen's speaker)", jack: "3.5 mm jack", usb: "USB adapter", bt: "Bluetooth speaker" };
  const SHORT = { hdmi: "HDMI", jack: "jack", usb: "USB", bt: "Bluetooth", auto: "auto" };

  const card = document.createElement("section");
  card.className = "card pj-card";
  card.innerHTML = `
    <h2>STAGE BOX SOUND <span id="sbBadge" class="badge dimbadge">—</span></h2>
    <div class="pj-row pj-wrap"><span class="pj-k">Playing on</span><span class="sdot" id="sbDot"></span><span class="pj-v" id="sbNow">—</span></div>
    <div class="pj-row pj-wrap"><span class="pj-k"></span><span class="pj-v pj-note" id="sbStreams"></span></div>
    <div class="pj-row pj-wrap"><label class="pj-k" for="sbOut">Output</label>
      <select id="sbOut"><option value="auto">auto</option><option value="hdmi">HDMI (the screen)</option>
        <option value="usb">USB adapter</option><option value="bt">Bluetooth speaker</option><option value="jack">3.5 mm jack (Pi 4)</option></select>
      <button class="iobtn" id="sbTest">TEST SOUND</button>
      <span class="pj-note">auto: the screen if it has a speaker, else USB, else Bluetooth, else the jack.</span></div>
    <div id="sbVols"></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Bluetooth</span><span class="pj-v" id="sbBt">—</span>
      <button class="iobtn" id="sbScan">PAIR SPEAKER</button><button class="iobtn" id="sbForget" hidden>FORGET</button></div>
    <div class="pj-row pj-wrap" id="sbFoundRow" hidden><span class="pj-k"></span><span class="pj-v" id="sbFound"></span></div>
    <div class="pj-row pj-wrap"><span class="pj-k">Box software</span><span class="pj-v pj-note" id="sbSoft">—</span></div>
    <div class="pj-note" id="sbWhy"></div>`;
  const adv = document.getElementById("showsAdvStageBox");
  if (adv) { card.className = "pj-card pj-adv"; adv.appendChild(card); } else view.appendChild(card);
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  let last = null, waiting = null, choosing = false;      // waiting: the id of the command whose result is shown next

  function say(text, code) {
    $("sbWhy").textContent = text || "";
    if (code && window.helpLink) $("sbWhy").appendChild(helpLink(code));
  }

  // tell: also given each line said about this command (step 1's TEST SOUND shows them by its own row)
  async function send(cmd, label, tell) {
    const out = (text, code) => { say(text, code); if (tell) tell(text, code); };
    out(label + "…");
    try {
      const r = await fetch("/api/stage/box/command", { method: "POST", headers: { "Content-Type": "application/json" },
                                                       body: JSON.stringify(cmd) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { out(`${label}: ${j.error || `HTTP ${r.status}`}`, j.code); return; }
      waiting = { id: j.id, label, tell };
    } catch (e) { out(`${label}: ${e.message}`); }
  }
  window.OpenDubsStageBox = { test: (tell) => send({ op: "test" }, "Test sound on the stage box", tell) };

  function render(b) {
    last = b;
    const s = b.sound || {};
    const on = !!b.online;
    $("sbBadge").textContent = !on ? (b.age_s == null ? "NOT SEEN" : "OFFLINE") : s.actual ? "PLAYING ON " + SHORT[s.actual].toUpperCase() : "NO OUTPUT";
    $("sbBadge").className = "badge " + (!on ? "dimbadge" : s.actual ? "good" : "warn");
    $("sbDot").className = "sdot " + (!on ? "" : s.actual ? "ok" : "bad");
    if (!on) {
      $("sbNow").textContent = b.age_s == null ? "the stage box has not reported (is it on, on the robot's Wi-Fi?)"
        : `no report for ${Math.round(b.age_s)} s`;
    } else {
      // a named output plays as chosen or not at all (#591): nothing, and why, refuses a show meant for here
      const bits = [s.actual ? `${NAMES[s.actual]}${s.name ? ` (${esc(s.name)})` : ""}`
        : `<span class="pj-bad">nothing: ${esc(s.why || s.error || "no sound output found")}</span>`];
      if (s.setting === "auto") bits.push("chosen: auto");
      else if (!s.actual && s.setting) bits.push(`chosen: ${SHORT[s.setting]}`);
      if (typeof s.latency_ms === "number") bits.push(`about ${s.latency_ms} ms late`);
      if (s.error && s.actual) bits.push(`<span class="pj-bad">${esc(s.error)}</span>`);
      $("sbNow").innerHTML = bits.join(" · ");
    }
    const streams = (s.playing || []).map((p) => `${esc(p.app)} → ${SHORT[p.kind] || p.kind}${p.running ? " (playing)" : " (idle)"}`);
    $("sbStreams").innerHTML = on ? (streams.length ? streams.join(" · ") : "nothing is sending sound") +
      (s.kept === false ? ` · <span class="pj-bad">settings not kept after a reboot: no media stick</span>` : "") : "";
    if (!choosing && s.setting && $("sbOut").value !== s.setting) $("sbOut").value = s.setting;
    for (const o of $("sbOut").options) {
      if (o.value === "auto") continue;
      o.textContent = o.textContent.replace(/ — not found$/, "") + (on && !(s.available || []).includes(o.value) ? " — not found" : "");
    }
    const busy = !!b.show_running;
    for (const id of ["sbOut", "sbTest", "sbScan", "sbForget"]) $(id).disabled = !on || busy || !!s.busy;
    // A volume per output that is there now.
    const vols = s.volumes || {};
    $("sbVols").innerHTML = (on ? (s.available || []) : []).map((k) => `
      <div class="pj-row pj-wrap"><span class="pj-k">${SHORT[k]} volume</span>
        <button class="iobtn" data-vol="${k}" data-d="-${STEP}">−</button>
        <span class="pj-val">${typeof vols[k] === "number" ? Math.round(vols[k] * 100) + " %" : "—"}</span>
        <button class="iobtn" data-vol="${k}" data-d="${STEP}">+</button></div>`).join("");
    // Step 1's "Sound plays on" row: the volume of the speaker the box plays through now, while the stage box
    // is the chosen sound device.
    const mv = $("sbMainVol"), act = on && s.actual, pick = $("pjAudMain");
    if (mv) {
      const show = act && pick && pick.value === "stage" && typeof vols[act] === "number";
      mv.innerHTML = !show ? "" : `<span class="pj-note">${SHORT[act]} volume</span>`
        + `<button class="iobtn" data-vol="${act}" data-d="-${STEP}">−</button>`
        + `<span class="pj-val">${Math.round(vols[act] * 100)} %</span>`
        + `<button class="iobtn" data-vol="${act}" data-d="${STEP}">+</button>`;
    }
    // The speaker.
    const bt = s.bt;
    const ADAPTER = { blocked: "the box's Bluetooth is blocked (rfkill): it lifts it itself within seconds",
                      off: "the box's Bluetooth is off: turning it on", none: "the box has no Bluetooth adapter" };
    $("sbBt").textContent = !on ? "—" : ADAPTER[s.adapter] ? ADAPTER[s.adapter]
      : s.busy === "scanning" ? "looking for speakers…" : s.busy === "pairing" ? "pairing…"
      : bt ? `${bt.name || bt.addr}: ${bt.connected ? "connected" : bt.paired ? "paired, not connected (is it on?)" : "not paired"}`
      : "no speaker paired";
    $("sbForget").hidden = !bt;
    const found = (on && s.found) || [];
    $("sbFoundRow").hidden = !found.length || !!(bt && found.some((f) => f.addr === bt.addr && bt.connected));
    $("sbFound").innerHTML = found.map((f) => `<button class="iobtn" data-pair="${esc(f.addr)}">PAIR ${esc(f.name)}</button>`).join(" ");
    // The box's own software (stage_update.py): what it runs, what the robot offers, and why not yet.
    const sw = b.software;
    $("sbSoft").textContent = !on || !sw ? "—" : `${sw.installed || "set up by hand"}` +
      (sw.offered && sw.offered !== sw.installed ? ` · the robot offers ${sw.offered}` : "") + ` · ${sw.state}`;
    // The result of the command sent last, once the box has run it.
    const res = waiting && (b.results || {})[waiting.id];
    if (res) {
      const code = res.ok ? null : busy ? "SHOW_STAGE_SOUND_BUSY" : null;
      say(`${waiting.label}: ${res.text}`, code);
      if (waiting.tell) waiting.tell(`${waiting.label}: ${res.text}`, code);
      waiting = null;
    }
    else if (busy && !waiting) say("A show is loaded or playing: only the volume changes until it ends.");
  }

  async function poll() {
    if (!view.classList.contains("active")) return;
    try {
      const r = await fetch("/api/stage/box", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status === 404 ? "this robot's dashboard has no stage box support yet" : `HTTP ${r.status}`);
      render(await r.json());
    } catch (e) { $("sbBadge").textContent = "UNAVAILABLE"; $("sbBadge").className = "badge dimbadge"; say(e.message); }
  }

  $("sbOut").addEventListener("focus", () => { choosing = true; });
  $("sbOut").addEventListener("blur", () => { choosing = false; });
  $("sbOut").addEventListener("change", (e) => { choosing = false; send({ op: "output", value: e.target.value }, `Output ${SHORT[e.target.value]}`); });
  $("sbTest").addEventListener("click", () => send({ op: "test" }, "Test sound"));
  $("sbScan").addEventListener("click", () => send({ op: "scan" }, "Looking for speakers (put it in pairing mode)"));
  $("sbForget").addEventListener("click", () => send({ op: "forget" }, "Forget the speaker"));
  function onClick(e) {
    const p = e.target.closest && e.target.closest("[data-pair]");
    if (p) return send({ op: "pair", addr: p.dataset.pair }, "Pairing");
    const v = e.target.closest && e.target.closest("[data-vol]");
    if (v && last && last.sound) {
      const k = v.dataset.vol, cur = (last.sound.volumes || {})[k];
      if (typeof cur !== "number") return;
      const next = Math.round(Math.min(1, Math.max(0, cur + Number(v.dataset.d))) * 100) / 100;
      last.sound.volumes[k] = next;                       // shown at once; the box confirms on its next report
      render(last);
      send({ op: "volume", output: k, value: next }, `${SHORT[k]} volume ${Math.round(next * 100)} %`);
    }
  }
  card.addEventListener("click", onClick);
  if ($("sbMainVol")) $("sbMainVol").addEventListener("click", onClick);

  setInterval(poll, POLL_MS);
  poll();
})();
