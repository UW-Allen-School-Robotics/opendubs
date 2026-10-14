/* OpenDubs — Operate screen (the driving front door).
   Adds the Operate view + diagnostics drawer on top of the existing dashboard.
   NON-INVASIVE: app.js and all existing views are untouched. This file hooks the
   global setView() + wraps renderRobotState() to feed real telemetry into the
   Operate screen. Drive actuation is intentionally NOT wired yet (needs the
   firmware drive-over-network + control-authority work); sticks are live UI only. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  // A battery whose fill shows the level, so the two at the top read at a glance.
  const BATT = (pct) => '<svg width="24" height="13" viewBox="0 0 24 13"><rect x="1" y="1" width="19" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="21" y="4" width="2.4" height="5" rx="1" fill="currentColor"/>' +
    (pct == null ? "" : `<rect x="3" y="3" width="${(15 * Math.max(0, Math.min(100, pct)) / 100).toFixed(1)}" height="7" rx="1.2" fill="currentColor"/>`) + '</svg>';
  // WHOSE battery: the robot's pack, or this device's. Easy to confuse, so each carries an icon.
  const ROBOT = '<svg class="who" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="8" width="14" height="10" rx="2"/><path d="M12 8V4"/><circle cx="12" cy="3.3" r="1.1" fill="currentColor"/><circle cx="9.5" cy="13" r="1.2" fill="currentColor"/><circle cx="14.5" cy="13" r="1.2" fill="currentColor"/><path d="M8 18v2.5M16 18v2.5"/></svg>';
  const TABLET = '<svg class="who" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="18.6" cy="12" r=".9" fill="currentColor"/></svg>';
  const KIOSK = window.OpenDubsKiosk;       // the tablet's kiosk app, when the page runs inside it
  const WARN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l10 17H2z"/><path d="M12 10v4"/><circle cx="12" cy="17.3" r=".5" fill="currentColor"/></svg>';
  const BAD  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 8l8 8"/></svg>';

  /* ---------- diagnostics drawer (reuses app.js setView) ---------- */
  function drawer(o){ const d=$("opDrawer"), s=$("opScrim"); if(d)d.classList.toggle("open",o); if(s)s.classList.toggle("open",o); }
  $("opHam") && $("opHam").addEventListener("click", () => drawer(true));
  // Back to the robot's face (software/face-web, served at /face/): one tablet is both the face and
  // this dashboard (tablet plan 5.4, the minimum). On the face, a long-press brings you back here.
  // A show takes the face. On the KIOSK tablet only (a laptop's dashboard never jumps), when a run
  // arms or starts, the tablet shows the robot's face, where the runner's ARMED sign and countdown
  // appear, so the people near the robot can see a show is about to start. Only on the CHANGE into a
  // show: an operator who comes back here during a show (long-press on the face) is not dragged away.
  if (window.OpenDubsKiosk) {
    const SHOW = new Set(["ARMED_WAITING", "COUNTDOWN", "PREPPING", "WAITING_FOR_START", "RUNNING"]);
    // At once, not on the next poll: the moment a run the operator started HERE is accepted, show the
    // face, so the runner's ARMED sign has a face to land on. After the request succeeds, never on the
    // tap itself — leaving the page mid-request would cancel the run it is starting.
    const fetch0 = window.fetch.bind(window);
    window.fetch = async (url, opts) => {
      const r = await fetch0(url, opts);
      try {
        const u = typeof url === "string" ? url : url.url;
        if (r.ok && /\/api\/choreo\/run\//.test(u) && (opts && String(opts.method).toUpperCase() === "POST")) {
          setTimeout(() => { location.href = "/face/"; }, 50);
        }
      } catch (e) {}
      return r;
    };
    let last;                                       // undefined until the first answer: no jump on load
    setInterval(async () => {
      try {
        const st = (await (await fetch("/api/choreo/palette", { cache: "no-store" })).json()).status || null;
        if (last !== undefined && SHOW.has(st) && !SHOW.has(last)) location.href = "/face/";
        last = st;
      } catch (e) { /* no runner: no show to follow */ }
    }, 1000);

    // Back to the face when nobody is using the dashboard: after `face_return_min` minutes untouched
    // (the Head & face page; 0 = never). Never while a show runs, a menu or dialog is open, or a
    // control is held — so it can't pull the screen from under an operator.
    let returnMin = 2, lastTouch = Date.now(), held = 0;
    const touched = () => { lastTouch = Date.now(); };
    for (const ev of ["pointerdown", "keydown", "wheel", "input"]) addEventListener(ev, touched, true);
    addEventListener("pointerdown", () => { held++; }, true);
    for (const ev of ["pointerup", "pointercancel"]) addEventListener(ev, () => { held = Math.max(0, held - 1); touched(); }, true);
    const loadReturn = async () => {
      try { const m = (await (await fetch("/api/head/settings", { cache: "no-store" })).json()).face_return_min;
            if (typeof m === "number") returnMin = m; } catch (e) {}
    };
    loadReturn(); setInterval(loadReturn, 60000);
    const modalOpen = () => !!document.querySelector("#opDrawer.open, dialog[open], [role=dialog]:not([hidden]), .modal.open");
    setInterval(() => {
      if (!returnMin || held || modalOpen() || SHOW.has(last)) return;
      if (Date.now() - lastTouch >= returnMin * 60000) location.href = "/face/";
    }, 5000);
  }
  // The FACE control: labelled, on the LEFT next to the brand, so it is never mistaken for or tapped
  // instead of E-STOP, which stays at the far right with clear space beside it.
  const brand = document.querySelector("#topbar .brand");
  if (brand && !$("opFace")) {
    const b = document.createElement("button");
    b.id = "opFace"; b.className = "op-face"; b.title = "Show the robot's face";
    b.innerHTML = '<span aria-hidden="true" style="font-size:17px;line-height:0">☺</span> FACE';
    b.addEventListener("click", () => { location.href = "/face/"; });
    brand.after(b);
  }
  $("opScrim") && $("opScrim").addEventListener("click", () => drawer(false));
  $("opDrawerClose") && $("opDrawerClose").addEventListener("click", () => drawer(false));
  document.querySelectorAll("#opDrawer .ditem").forEach((it) =>
    it.addEventListener("click", () => { if (it.dataset.view && typeof setView === "function") setView(it.dataset.view); drawer(false); }));

  /* ---------- E-STOP (same endpoint app.js uses) ---------- */
  $("opEstop") && $("opEstop").addEventListener("click", () => {
    fetch("/api/estop", { method: "POST" }).catch(() => {});
    $("opEstop").classList.add("latched");
    setTimeout(() => $("opEstop").classList.remove("latched"), 1500);
  });

  /* ---------- E-STOP RE-ARM (hold to confirm) ----------
     /api/estop/clear is guarded server-side (button still pressed, stale telemetry, welded
     contactor, CH5 not OFF) and answers 409 with the blocking reasons -- surface them rather
     than silently failing. */
  (function () {
    const btn = $("opRearm"); if (!btn) return;
    const fill = btn.querySelector(".op-rearmfill"), txt = btn.querySelector(".op-rearmtxt");
    const HOLD = 1000; let t0 = 0, raf = 0, done = false;
    const reset = () => { cancelAnimationFrame(raf); fill.style.width = "0"; done = false; };
    function tick() {
      const f = Math.min(1, (performance.now() - t0) / HOLD);
      fill.style.width = (f * 100) + "%";
      if (f >= 1 && !done) { done = true; fire(); return; }
      if (!done) raf = requestAnimationFrame(tick);
    }
    async function fire() {
      txt.textContent = "RE-ARMING…";
      try {
        const r = await fetch("/api/estop/clear", { method: "POST" });
        if (r.ok) { txt.textContent = "HOLD TO RE-ARM"; btn.classList.remove("blocked"); }
        else {
          const j = await r.json().catch(() => ({}));
          const b = (j.blockers || ["refused"]).join(" · ");
          btn.classList.add("blocked"); txt.textContent = "BLOCKED";
          setNotifs([{ sev: "bad", txt: "RE-ARM BLOCKED · " + b }]);
          setTimeout(() => { txt.textContent = "HOLD TO RE-ARM"; btn.classList.remove("blocked"); }, 3000);
        }
      } catch (e) { txt.textContent = "HOLD TO RE-ARM"; }
      reset();
    }
    btn.addEventListener("pointerdown", (e) => { btn.setPointerCapture(e.pointerId); t0 = performance.now(); raf = requestAnimationFrame(tick); });
    ["pointerup", "pointercancel", "pointerleave"].forEach((ev) =>
      btn.addEventListener(ev, () => { if (!done) reset(); }));
  })();

  /* ---------- rear PiP expand ---------- */
  /* Guarded: only a click that ORIGINATED on the PiP toggles it. Without this, any click that
     bubbles up from (or lands on an element overlapping) the control zone expands the rear
     camera -- which is what made the right stick appear to light up the rear feed. */
  $("opPip") && $("opPip").addEventListener("click", (e) => {
    if (!e.target.closest || !e.target.closest("#opPip")) return;
    $("opPip").classList.toggle("big");
  });

  /* ---------- drive status (read-only): what the robot reports, and why it will not move ---------- */
  // CH5 in the names the team uses (DEC-9), from the authority the robot-state rollup reports.
  const AUTH = { OFF: "STANDBY", RC_MANUAL: "MANUAL", AUTONOMOUS: "AUTO" };
  let lastRS = null, lastDrive = null, lastArms = null;
  function whyNot(inp, d) {
    // In the order the StamPLC's own gate checks them; the first one is what to fix.
    if (inp.estopped || (inp.estop_sources && inp.estop_sources.length)) return "e-stop";
    if (inp.contactor === false) return "contactor open — no drive power";
    if (inp.link === false) return "no RC link";
    if (inp.authority === "OFF") return "CH5 in STANDBY";
    if (d.drive_safe === false) return "robot settling";
    if (!inp.deadman) return "deadman released";
    if (!d.armed) return "arming — sticks neutral for 2 s";
    if (inp.authority === "AUTONOMOUS") return "moves only on the Thor's drive commands";
    return "";
  }
  // The arms, from /api/arms/status (energized = motors powered and holding; arms plan DEC-24).
  function armsState(a, inp) {
    if (!a) return ["—", "dim", ""];
    if (a.op && a.op.running) return ["BUSY", "warn", `${a.op.name} (${a.op.elapsed ?? 0} s)`];
    const m = a.motors;
    if (!m) return ["—", "dim", a.motors_error ? "status unavailable" : ""];
    if (m.faults) return ["FAULT", "bad", `${m.faults} motor${m.faults > 1 ? "s" : ""} in fault`];
    if (!a.energized) return ["OFF", "dim", "not energized"];
    if (m.missing || m.disabled) return ["PARTIAL", "warn", `${m.enabled} enabled, ${(m.missing || 0) + (m.disabled || 0)} not`];
    if (a.teleop && a.teleop.running) return ["ENERGIZED", "ok", "teleop running"];
    if (inp.authority === "OFF") return ["ENERGIZED", "ok", "holding — move only in MANUAL"];
    return ["ENERGIZED", "ok", "holding"];
  }
  window.addEventListener("opendubs-arms", (e) => { lastArms = e.detail; try { renderOpStatus(); } catch (err) {} });

  function renderOpStatus() {
    const inp = (lastRS && lastRS.inputs) || {}, d = lastDrive || {};
    const set = (id, txt, cls) => { const e = $(id); if (e) { e.textContent = txt; e.className = cls || ""; } };
    const auth = AUTH[inp.authority];
    set("osAuth", auth || "—", auth === "STANDBY" ? "dim" : auth ? "ok" : "dim");
    set("osDead", inp.deadman == null ? "—" : inp.deadman ? "HELD" : "RELEASED", inp.deadman ? "ok" : "warn");
    set("opDead", "DEADMAN " + (inp.deadman == null ? "—" : inp.deadman ? "HELD" : "RELEASED"),
        "op-state " + (inp.deadman == null ? "dim" : inp.deadman ? "" : "warn"));
    const why = lastRS ? whyNot(inp, d) : "";
    set("osDrive", d.armed == null ? "—" : d.armed ? "ARMED" : "DISARMED", d.armed ? "ok" : "warn");
    set("osWhy", why ? "· " + why : "", "op-why");
    const [at, ac, aw] = armsState(lastArms, inp);
    set("osArms", at, ac); set("osArmsWhy", aw ? "· " + aw : "", "op-why");
    set("osSpeed", d.spd == null ? "—" : Math.round(d.spd * 100) + "%", d.spd === 0 ? "bad" : d.spd == null ? "dim" : "");
  }
  if (typeof window.renderDrive === "function") {
    const _rd = window.renderDrive;
    // try/finally: the strip must not depend on the rest of the drive page drawing cleanly.
    window.renderDrive = function (st) {
      try { _rd(st); } finally { lastDrive = (st && st.drive) || null; try { renderOpStatus(); } catch (e) {} }
    };
  }

  /* ---------- fullscreen + landscape lock ---------- */
  function goFS() { const el = document.documentElement;
    if (!document.fullscreenElement) { (el.requestFullscreen ? el.requestFullscreen() : Promise.reject())
      .then(() => { try { screen.orientation.lock("landscape"); } catch (e) {} }).catch(() => {}); }
    else if (document.exitFullscreen) document.exitFullscreen(); }
  // Inside the kiosk app the page is already full screen, so the control means nothing there. In any
  // other browser it still does something (standing rule 12).
  if (KIOSK) { if ($("opFs")) $("opFs").hidden = true; }
  else {
    $("opFs") && $("opFs").addEventListener("click", (e) => { e.stopPropagation(); goFS(); });
    if (window.matchMedia && matchMedia("(pointer:coarse)").matches)
      document.addEventListener("pointerdown", () => goFS(), { once: true });
  }

  /* ---------- notifications (dark & quiet: empty unless out of nominal) ---------- */
  function setNotifs(list) {
    const box = $("opNotif"); if (!box) return; box.innerHTML = "";
    list.forEach((n) => { const d = document.createElement("div"); d.className = "op-nt " + n.sev;
      d.innerHTML = (n.sev === "bad" ? BAD : WARN) + "<span>" + n.txt + "</span>"; box.appendChild(d); });
  }
  const STATE_SEV = { red: "bad", amber: "warn", yellow: "warn", orange: "warn", green: "ok", cyan: "ok" };
  function setOpState(label, color) {
    const el = $("opState"); if (!el) return;
    el.textContent = label || "—";
    el.className = "op-state " + (STATE_SEV[color] === "bad" ? "bad" : STATE_SEV[color] === "warn" ? "warn" : "");
  }

  /* battery: mirror the value app.js already renders into #stBatt ("82% · 53.4V · …") */
  let lastSoc = null;
  setInterval(() => { const t = ($("stBatt") && $("stBatt").textContent) || ""; const m = t.match(/(\d+)\s*%/);
    lastSoc = m ? +m[1] : null; const b = $("opBatt");
    if (b) { b.innerHTML = ROBOT + BATT(lastSoc) + " " + (lastSoc != null ? lastSoc + "%" : "—");
      b.style.color = lastSoc == null ? "var(--dim)" : lastSoc < 15 ? "var(--bad)" : lastSoc < 25 ? "var(--warn)" : "var(--dim)"; }
  }, 1000);

  /* this device's battery: the kiosk app's, or the browser's own where it reports one; else hidden */
  let devBatt = null;                                   // {level 0..100, charging}
  if (!KIOSK && navigator.getBattery) navigator.getBattery().then((bm) => {
    const f = () => { devBatt = { level: Math.round(bm.level * 100), charging: bm.charging }; };
    f(); bm.addEventListener("levelchange", f); bm.addEventListener("chargingchange", f);
  }).catch(() => {});
  setInterval(() => {
    if (KIOSK && KIOSK.battery) { try { devBatt = JSON.parse(KIOSK.battery()); } catch (e) { devBatt = null; } }
    const d = $("devBatt"); if (!d) return;
    if (!devBatt || devBatt.level == null || devBatt.level < 0) { d.hidden = true; return; }
    d.hidden = false;
    d.innerHTML = TABLET + BATT(devBatt.level) + " " + devBatt.level + "%" + (devBatt.charging ? " ⚡" : "");
    d.style.color = devBatt.charging ? "var(--dim)" : devBatt.level < 15 ? "var(--bad)" : devBatt.level < 25 ? "var(--warn)" : "var(--dim)";
  }, 5000);

  /* derive Operate state + notifications from the same rs app.js renders */
  function opFromRS(rs) {
    if (!rs) return; setOpState(rs.label, rs.color); lastRS = rs; renderOpStatus();
    const n = [], inp = rs.inputs || {};
    const latched = !!(inp.estopped || (inp.estop_sources && inp.estop_sources.length));
    document.body.classList.toggle("op-estopped", latched);   // reveals the RE-ARM control
    if (inp.estop_sources && inp.estop_sources.length) n.push({ sev: "bad", txt: "E-STOP · " + inp.estop_sources.join(" + ") });
    if (inp.link === false) n.push({ sev: "bad", txt: "RC LINK LOST" });
    if (lastSoc != null) { if (lastSoc < 15) n.push({ sev: "bad", txt: "PACK " + lastSoc + "% — RETURN" });
      else if (lastSoc < 25) n.push({ sev: "warn", txt: "PACK " + lastSoc + "% — low" }); }
    setNotifs(n);
    const ds = $("opDrawerSafety"); if (ds) ds.textContent = n.length ? (n.length + " active " + (n.length > 1 ? "issues" : "issue")) : "system nominal — no active faults";
  }
  // wrap the global renderRobotState so the Operate screen updates on every telemetry frame
  if (typeof window.renderRobotState === "function") {
    const _rrs = window.renderRobotState;
    window.renderRobotState = function (rs) { try { _rrs(rs); } finally { try { opFromRS(rs); } catch (e) {} } };
  }

  // start on the Operate front door
  if (typeof setView === "function") setView("operate");
})();
