/* TELEOP page glue: arm view, controls, and the session/server readouts.
 *
 * A module because armview.js is one; app.js stays a classic script, so the two meet through a
 * couple of globals (window.onJointStates in, window.confirmButton out) rather than a rewrite.
 */
import { ArmView } from "./armview.js?v=005dc331";

const $ = (id) => document.getElementById(id);
const canvas = $("armCanvas");
let view = null;
let lastJointMs = 0;

/* Deviation from the stow pose, across ALL joints — including the rolls, which the render shows
 * but which are easy to misjudge by eye. This is what gates PARK: it is the difference between a
 * no-op and an 87 deg sweep, and the operator should see which before tapping. */
function stowDeviationDeg(prefix) {
  if (!view) return null;
  let worst = 0;
  for (const [name, j] of Object.entries(view.joints)) {
    if (!name.startsWith(prefix) || name.includes("finger")) continue;
    worst = Math.max(worst, Math.abs(j.value) * 180 / Math.PI);
  }
  return worst;
}

function paintStow() {
  for (const [side, el] of [["left", $("tpStowL")], ["right", $("tpStowR")]]) {
    if (!el) continue;
    const d = stowDeviationDeg(`openarm_${side}_`);
    if (d === null || !lastJointMs) { el.textContent = `${side} —`; el.className = "sub"; return; }
    el.textContent = d < 3 ? `${side} at stow` : `${side} ${Math.round(d)}° from stow`;
    el.style.color = d < 3 ? "var(--good)" : "var(--warn)";
  }
}

window.onJointStates = (msg) => {
  if (!view || !msg || !msg.name) return;
  const angles = {};
  for (let i = 0; i < msg.name.length; i++) angles[msg.name[i]] = msg.position?.[i];
  view.setJoints(angles);
  lastJointMs = Date.now();
  const b = $("tpPoseSrc");
  if (b) { b.textContent = "live"; b.className = "badge good"; }
  paintStow();
};

/* Stale joint data must read as STALE, never as a pose. A frozen render showing a plausible arm is
 * the failure this whole panel exists to prevent — the same reason the view is driven from
 * /joint_states and not from the planning model, which shows commanded pose and is most wrong
 * right after an abort. */
setInterval(() => {
  const b = $("tpPoseSrc");
  if (!b) return;
  if (!lastJointMs) { b.textContent = "no joint data"; b.className = "badge dimbadge"; return; }
  const age = (Date.now() - lastJointMs) / 1000;
  if (age > 2) { b.textContent = `STALE ${age.toFixed(0)}s`; b.className = "badge warn"; }
}, 1000);

async function boot() {
  if (!canvas) return;
  try {
    view = await new ArmView(canvas).load();
  } catch (e) {
    const b = $("tpPoseSrc");
    if (b) { b.textContent = "render failed"; b.className = "badge dimbadge"; }
    console.error("arm view failed to load", e);
    return;
  }
  for (const btn of document.querySelectorAll("[data-armview]")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-armview]").forEach((b) => b.classList.remove("on"));
      btn.classList.add("on");
      view.setView(btn.dataset.armview);
    });
  }
  // The canvas is zero-sized until its view is shown, so re-frame when teleop becomes visible.
  const main = $("view-teleop");
  if (main && window.MutationObserver) {
    new MutationObserver(() => { if (main.classList.contains("active")) { view.frame(); view.render(); } })
      .observe(main, { attributes: true, attributeFilter: ["class"] });
  }
  window.addEventListener("resize", () => { view.frame(); });
  paintStow();
}

/* Every one of these moves an arm or stops a session, so all are confirm-on-tap via the same
 * helper the e-stop RE-ARM uses — a tablet is exactly where a single stray touch should not
 * sweep an elbow through 87 degrees. */
function wire() {
  const cb = window.confirmButton;
  if (typeof cb !== "function") return;
  const mk = (id, url, confirmText, pending, label) => {
    const el = $(id);
    if (el) cb(el, { url: () => url, confirm: confirmText, pending, label: () => { el.textContent = label; } });
  };
  mk("btnArmPark",  "/api/arms/park?arm=both",       "CONFIRM PARK",  "PARKING…",   "PARK BOTH");
  mk("btnArmHoldL", "/api/arms/hold?arm=left",       "CONFIRM HOLD",  "…",          "HOLD L");
  mk("btnArmHoldR", "/api/arms/hold?arm=right",      "CONFIRM HOLD",  "…",          "HOLD R");
  mk("btnArmActL",  "/api/arms/activate?arm=left",   "MOVES TO ZERO", "ACTIVATING…", "ACTIVATE L");
  mk("btnArmActR",  "/api/arms/activate?arm=right",  "MOVES TO ZERO", "ACTIVATING…", "ACTIVATE R");
  mk("btnArmDown",  "/api/arms/down",                "CONFIRM DROP",  "STOPPING…",  "DE-ENERGIZE");
  mk("btnTeleopStart", "/api/teleop/start", "CONFIRM START", "STARTING…", "START");
  mk("btnTeleopStop",  "/api/teleop/stop",  "CONFIRM STOP",  "STOPPING…", "STOP");
}

/* ---- status poll: server readiness, session, loop health ---------------------------------- */

function badge(el, text, cls) {
  if (!el) return;
  el.textContent = text;
  el.className = "badge " + (cls || "dimbadge");
}

function paintStatus(s) {
  const t = s.teleop || {};
  const up = t.running === true;
  const serving = t.page && t.poses;
  badge($("tpServerState"), up && serving ? "READY" : up ? "STARTING…" : "STOPPED",
        up && serving ? "good" : up ? "warn" : "dimbadge");

  // The address the Quest must open. Derived from whatever host reached this dashboard, so it
  // follows a DHCP change instead of being a number someone has to remember and re-edit.
  const u = $("tpUrl");
  if (u) u.textContent = `https://${location.hostname}:8444/`;

  // Readiness, next to the controls rather than in a log: the real failure was a 30-second
  // "Timed out waiting for left gripper action server" AFTER the headset was already on.
  const rows = [
    [t.page, "HTTPS page :8444"],
    [t.poses, "pose channel :8766"],
    [s.energized, "arms energized"],
  ];
  const r = $("tpReady");
  if (r) {
    r.innerHTML = "";
    for (const [ok, label] of rows) {
      const d = document.createElement("div");
      d.className = ok === true ? "ok" : ok === false ? "badx" : "warnx";
      d.textContent = label + (ok === null || ok === undefined ? " (unknown)" : "");
      r.append(d);
    }
    if (s.motors) {
      const d = document.createElement("div");
      d.className = s.motors.faults ? "badx" : "ok";
      d.textContent = `motors ${s.motors.enabled ?? "?"} enabled, ${s.motors.faults ?? 0} faults`;
      r.append(d);
    }
    if (s.op && s.op.running) {
      const d = document.createElement("div");
      d.className = "warnx";
      d.textContent = `${s.op.name} running (${s.op.elapsed ?? 0}s)`;
      r.append(d);
    } else if (s.op && s.op.error) {
      const d = document.createElement("div");
      d.className = "badx";
      d.style.whiteSpace = "pre-line";  // the error names the fix on its own line
      d.textContent = `${s.op.name}: ${s.op.error}`;
      r.append(d);
    }
  }

  // Loop health. Absent warnings is the GOOD case -- the loop only complains when it is late.
  const L = s.loop;
  const hz = $("tpLoopHz"), over = $("tpLoopOver"), worst = $("tpLoopWorst");
  if (!L || L.available === false) {
    if (hz) hz.textContent = "—";
    if (over) { over.textContent = "—"; over.className = ""; }
    if (worst) worst.textContent = "—";
    $("tpLoopNote").textContent = L ? (L.reason || "unavailable") : "teleop not running";
  } else {
    if (hz) hz.textContent = L.target_hz ? `${L.target_hz}` : "—";
    if (over) {
      over.textContent = `${L.over_pct}%`;
      over.className = L.over_pct > 5 ? "bad" : "good";
    }
    if (worst) worst.textContent = L.worst_ms ? `+${L.worst_ms}ms` : "—";
    $("tpLoopNote").textContent = L.over_pct > 5
      ? "the loop is missing deadlines — tracking will feel coarse"
      : "the loop reports this itself; no warnings is the good case";
  }

  badge($("tpSessionState"), up ? (lastJointMs ? "ARMS LIVE" : "NO JOINT DATA") : "NOT RUNNING",
        up && lastJointMs ? "good" : "dimbadge");
}

async function pollStatus() {
  try {
    const r = await fetch("/api/arms/status", { cache: "no-store" });
    if (r.ok) {
      const s = await r.json();
      paintStatus(s);
      // Shared, so the Operate page's ARMS tile needs no second poller: every read of this endpoint
      // also queries the motors over the arms' own CAN bus.
      window.dispatchEvent(new CustomEvent("opendubs-arms", { detail: s }));
    }
  } catch (e) { /* dashboard backend down; the rest of the page already says so */ }
}

boot();
wire();
pollStatus();
setInterval(pollStatus, 2000);
