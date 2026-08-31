/* OpenDubs — Operate screen (the driving front door).
   Adds the Operate view + diagnostics drawer on top of the existing dashboard.
   NON-INVASIVE: app.js and all existing views are untouched. This file hooks the
   global setView() + wraps renderRobotState() to feed real telemetry into the
   Operate screen. Drive actuation is intentionally NOT wired yet (needs the
   firmware drive-over-network + control-authority work); sticks are live UI only. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const BATT = '<svg width="24" height="13" viewBox="0 0 24 13"><rect x="1" y="1" width="19" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="21" y="4" width="2.4" height="5" rx="1" fill="currentColor"/><rect x="3" y="3" width="13.5" height="7" rx="1.2" fill="currentColor"/></svg>';
  const WARN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l10 17H2z"/><path d="M12 10v4"/><circle cx="12" cy="17.3" r=".5" fill="currentColor"/></svg>';
  const BAD  = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M8 8l8 8"/></svg>';

  /* ---------- diagnostics drawer (reuses app.js setView) ---------- */
  function drawer(o){ const d=$("opDrawer"), s=$("opScrim"); if(d)d.classList.toggle("open",o); if(s)s.classList.toggle("open",o); }
  $("opHam") && $("opHam").addEventListener("click", () => drawer(true));
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

  /* ---------- rear PiP expand ---------- */
  $("opPip") && $("opPip").addEventListener("click", () => $("opPip").classList.toggle("big"));

  /* ---------- on-screen joysticks (visual; drive not actuated yet) ---------- */
  function stick(el, horizOnly) {
    if (!el) return; const nub = el.querySelector(".nub");
    const set = (x, y) => { const b = el.getBoundingClientRect(); const R = Math.min(b.width, b.height) / 2 - 14;
      let dx = x - (b.left + b.width / 2), dy = y - (b.top + b.height / 2); if (horizOnly) dy = 0;
      const m = Math.hypot(dx, dy); if (m > R) { dx *= R / m; dy *= R / m; }
      nub.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`; el.classList.add("armed"); };
    const end = () => { nub.style.transform = "translate(-50%,-50%)"; el.classList.remove("armed"); };
    el.addEventListener("pointerdown", (e) => { el.setPointerCapture(e.pointerId); set(e.clientX, e.clientY); });
    el.addEventListener("pointermove", (e) => { if (e.buttons) set(e.clientX, e.clientY); });
    el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
  }
  stick($("opStTrans"), false); stick($("opStRot"), true);

  /* ---------- two-thumb ARM → sticks live; touch-is-deadman; auto-disarm on idle ---------- */
  let padL = false, padR = false, armTimer = null, idleTimer = null; const ARM_MS = 1200, IDLE_MS = 8000;
  const armed = () => document.body.classList.contains("op-armed");
  function checkArm() {
    const both = padL && padR, prog = $("opArmProg");
    if (both && !armTimer && !armed()) { const t0 = performance.now();
      armTimer = setInterval(() => { const p = Math.min(1, (performance.now() - t0) / ARM_MS); if (prog) prog.style.width = (p * 100) + "%";
        if (p >= 1) { clearInterval(armTimer); armTimer = null; doArm(); } }, 40);
    } else if (!both && armTimer) { clearInterval(armTimer); armTimer = null; if (prog) prog.style.width = "0"; }
  }
  function doArm() { document.body.classList.add("op-armed"); const s = $("opArmStatus"); if (s) s.textContent = "● ARMED · tap to disarm"; resetIdle(); }
  function disarm() { document.body.classList.remove("op-armed"); const s = $("opArmStatus"); if (s) s.textContent = "DRIVE DISARMED";
    const p = $("opArmProg"); if (p) p.style.width = "0"; padL = padR = false;
    $("opArmL") && $("opArmL").classList.remove("on"); $("opArmR") && $("opArmR").classList.remove("on"); clearTimeout(idleTimer); }
  function resetIdle() { clearTimeout(idleTimer); idleTimer = setTimeout(disarm, IDLE_MS); }
  function padBind(el, set) { if (!el) return;
    el.addEventListener("pointerdown", (e) => { el.setPointerCapture(e.pointerId); set(true); el.classList.add("on"); checkArm(); });
    const up = () => { set(false); el.classList.remove("on"); checkArm(); };
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up); }
  padBind($("opArmL"), (v) => padL = v); padBind($("opArmR"), (v) => padR = v);
  $("opArmStatus") && $("opArmStatus").addEventListener("click", () => { if (armed()) disarm(); });
  [$("opStTrans"), $("opStRot")].forEach((s) => s && s.addEventListener("pointerdown", () => { if (armed()) resetIdle(); }));

  /* ---------- gamepad auto-fallback: touch sticks hide when a joystick is present ---------- */
  function updateGamepad() { let real = false; try { real = [...navigator.getGamepads()].some(Boolean); } catch (e) {}
    document.body.classList.toggle("op-gamepad", real); if (real) disarm(); }
  window.addEventListener("gamepadconnected", updateGamepad);
  window.addEventListener("gamepaddisconnected", updateGamepad);
  setInterval(updateGamepad, 1500); updateGamepad();

  /* ---------- fullscreen + landscape lock ---------- */
  function goFS() { const el = document.documentElement;
    if (!document.fullscreenElement) { (el.requestFullscreen ? el.requestFullscreen() : Promise.reject())
      .then(() => { try { screen.orientation.lock("landscape"); } catch (e) {} }).catch(() => {}); }
    else if (document.exitFullscreen) document.exitFullscreen(); }
  $("opFs") && $("opFs").addEventListener("click", (e) => { e.stopPropagation(); goFS(); });
  if (window.matchMedia && matchMedia("(pointer:coarse)").matches)
    document.addEventListener("pointerdown", () => goFS(), { once: true });

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
    if (b) { b.innerHTML = BATT + " " + (lastSoc != null ? lastSoc + "%" : "—");
      b.style.color = lastSoc == null ? "var(--dim)" : lastSoc < 15 ? "var(--bad)" : lastSoc < 25 ? "var(--warn)" : "var(--dim)"; }
  }, 1000);

  /* derive Operate state + notifications from the same rs app.js renders */
  function opFromRS(rs) {
    if (!rs) return; setOpState(rs.label, rs.color);
    const n = [], inp = rs.inputs || {};
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
    window.renderRobotState = function (rs) { _rrs(rs); try { opFromRS(rs); } catch (e) {} };
  }

  // start on the Operate front door
  if (typeof setView === "function") setView("operate");
})();
