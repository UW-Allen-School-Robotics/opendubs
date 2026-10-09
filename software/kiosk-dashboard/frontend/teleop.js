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
/* Arm actions run in the BACKGROUND -- the POST returns at once, and the arm work (activate,
 * park) takes tens of seconds. So a button's state must come from the backend's op, not from its
 * request: the pressed one reads "ACTIVATING… 12 s" until the op really ends, the others are
 * disabled meanwhile (one arm action at a time -- a second would be refused with 409), and a press
 * that is refused anyway says so instead of looking like success. */
const ARM_BUTTONS = [
  { id: "btnArmPark",  url: "/api/arms/park?arm=both",      op: "park both",      confirm: "CONFIRM PARK",  pending: "PARKING…",    label: "PARK BOTH" },
  { id: "btnArmHoldL", url: "/api/arms/hold?arm=left",      op: "hold left",      confirm: "CONFIRM HOLD",  pending: "HOLDING…",    label: "HOLD L" },
  { id: "btnArmHoldR", url: "/api/arms/hold?arm=right",     op: "hold right",     confirm: "CONFIRM HOLD",  pending: "HOLDING…",    label: "HOLD R" },
  { id: "btnArmActL",  url: "/api/arms/activate?arm=left",  op: "activate left",  confirm: "MOVES TO ZERO", pending: "ACTIVATING…", label: "ACTIVATE L" },
  { id: "btnArmActR",  url: "/api/arms/activate?arm=right", op: "activate right", confirm: "MOVES TO ZERO", pending: "ACTIVATING…", label: "ACTIVATE R" },
  { id: "btnArmDown",  url: "/api/arms/down",               op: "down both",      confirm: "CONFIRM DROP",  pending: "STOPPING…",   label: "DE-ENERGIZE" },
];
let lastOp = null;             // the backend's op, from the last status poll
let requested = null;          // { id, at } -- pressed, and the poll has not shown it running yet
const flash = {};              // id -> { text, until } -- REFUSED / DONE / FAILED, shown briefly

function paintArmButtons() {
  const now = Date.now();
  const running = !!(lastOp && lastOp.running);
  if (requested) {
    if (running && lastOp.name === opOf(requested.id)) requested = null;   // the poll shows it running
    else if (now - requested.at > 4000) requested = null;                   // it never showed up
  }
  for (const b of ARM_BUTTONS) {
    const el = $(b.id);
    if (!el || el.classList.contains("armed")) continue;          // mid-confirm: leave it alone
    const f = flash[b.id];
    const mine = running ? lastOp.name === b.op : !!(requested && requested.id === b.id);
    el.classList.toggle("busy", mine);
    el.disabled = running || !!requested;                          // one arm action at a time
    if (mine) {
      el.textContent = running ? `${b.pending} ${Math.round(lastOp.elapsed || 0)} s` : b.pending;
      el.title = "";
    } else if (f && f.until > now) {
      el.textContent = f.text;
    } else {
      el.textContent = b.label;
      el.title = running ? `waits for: ${lastOp.name} (${Math.round(lastOp.elapsed || 0)} s)` : "";
    }
  }
}
function opOf(id) { const b = ARM_BUTTONS.find((x) => x.id === id); return b ? b.op : null; }

function noteArm(text, cls) {
  let n = $("tpArmNote");
  const anchor = $("btnArmDown");
  if (!n && anchor && anchor.parentElement) {
    n = document.createElement("div");
    n.id = "tpArmNote";
    n.className = "sub";
    anchor.parentElement.after(n);
  }
  if (n) { n.textContent = text || ""; n.className = cls || "sub"; }
}

async function onArmResponse(b, r) {
  if (r && r.ok) { requested = { id: b.id, at: Date.now() }; paintArmButtons(); pollStatus(); return; }
  let why = "the dashboard did not answer";
  if (r) { try { why = (await r.json()).error || `HTTP ${r.status}`; } catch (e) { why = `HTTP ${r.status}`; } }
  flash[b.id] = { text: r && r.status === 409 ? "BUSY — WAIT" : "REFUSED", until: Date.now() + 4000 };
  noteArm(`${b.label} refused: ${why}`, "badx");
  paintArmButtons();
  setTimeout(paintArmButtons, 4100);
}

function wire() {
  const cb = window.confirmButton;
  if (typeof cb !== "function") return;
  for (const b of ARM_BUTTONS) {
    const el = $(b.id);
    if (el) cb(el, { url: () => b.url, confirm: b.confirm, pending: b.pending,
                     label: paintArmButtons, onResponse: (r) => onArmResponse(b, r) });
  }
  const mk = (id, url, confirmText, pending, label) => {
    const el = $(id);
    if (el) cb(el, { url: () => url, confirm: confirmText, pending, label: () => { el.textContent = label; } });
  };
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
      const prev = lastOp;
      lastOp = s.op || null;
      // An op just finished: say how, on its own button, for a few seconds.
      if (prev && prev.running && lastOp && !lastOp.running && prev.name === lastOp.name) {
        const b = ARM_BUTTONS.find((x) => x.op === lastOp.name);
        if (b) {
          const ok = lastOp.rc === 0 && !lastOp.error;
          flash[b.id] = { text: ok ? "DONE" : "FAILED — SEE BELOW", until: Date.now() + (ok ? 2500 : 6000) };
          noteArm(ok ? `${b.label}: done in ${Math.round(lastOp.elapsed || 0)} s` : "", ok ? "ok" : "sub");
          setTimeout(paintArmButtons, ok ? 2600 : 6100);
        }
      }
      if (lastOp && lastOp.running) noteArm(`${lastOp.name} running — ${Math.round(lastOp.elapsed || 0)} s. Other arm buttons wait until it finishes.`, "warnx");
      paintArmButtons();
      paintStatus(s);
      // Shared, so the Operate page's ARMS tile needs no second poller: every read of this endpoint
      // also queries the motors over the arms' own CAN bus.
      window.dispatchEvent(new CustomEvent("opendubs-arms", { detail: s }));
    }
  } catch (e) { /* dashboard backend down; the rest of the page already says so */ }
}

boot();
wire();
// Every second while an arm action runs, so its elapsed time moves; every two otherwise.
(function loop() {
  pollStatus().finally(() => setTimeout(loop, lastOp && lastOp.running ? 1000 : 2000));
})();
