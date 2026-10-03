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

boot();
wire();
