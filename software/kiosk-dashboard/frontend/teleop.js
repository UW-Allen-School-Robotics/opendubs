/* TELEOP page glue: arm view, controls, and the session/server readouts.
 *
 * A module because armview.js is one; app.js stays a classic script, so the two meet through a
 * couple of globals (window.onJointStates in, window.confirmButton out) rather than a rewrite.
 */
import { ArmView } from "./armview.js?v=39c54689";

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

let lastRosMs = 0;             // when /joint_states (rosbridge) last delivered: while it does, it is the source and the arm service's own report is not used

function applyJoints(msg, source) {
  const angles = {};
  for (let i = 0; i < msg.name.length; i++) angles[msg.name[i]] = msg.position?.[i];
  view.setJoints(angles);
  lastJointMs = Date.now();
  const b = $("tpPoseSrc");
  if (b) { b.textContent = source === "ros" ? "live" : "live · arm service"; b.className = "badge good"; }
  paintStow();
}

window.onJointStates = (msg) => {
  if (!view || !msg || !msg.name) return;
  lastRosMs = Date.now();
  applyJoints(msg, "ros");
};

/* The arm service reports its joints in its status text (`joints (deg) +1.0 ...`, the arm's seven in order), which this page polls anyway. When /joint_states is not arriving (no ROS
 * on this machine, a different DDS domain, a service started --no-ros) the pose view would say "no joint data" about an arm that is answering, so it is drawn from that report and the
 * badge says where it came from. /joint_states wins while it arrives. A report older than the poll's own failure shows as STALE exactly as before: lastJointMs is only set here. */
function feedFromService() {
  if (!view || !lastService || Date.now() - lastRosMs < 1500) return;
  const name = [], position = [];
  for (const side of ["left", "right"]) {
    const m = (lastService[side] || "").match(/^joints \(deg\)\s+(.+)$/m);
    const deg = m ? m[1].trim().split(/\s+/).map(Number) : [];
    if (deg.length !== 7 || deg.some(Number.isNaN)) continue;
    deg.forEach((d, i) => { name.push(`openarm_${side}_joint${i + 1}`); position.push(d * Math.PI / 180); });
  }
  if (name.length) applyJoints({ name, position }, "service");
}

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
  // ONE CONTROLLER FOR BOTH ARMS (backend arm_handover.py). `act` is the action the backend's `stack.unavailable` names: a button whose controller cannot do it is disabled
  // and its card says why, in words (a tablet has no hover). Every label says what the ARMS do.
  // Choosing a controller when none has the arms (after a boot): the arms stiffen where they are and do not move.
  { id: "btnUseRos2",  url: "/api/arms/use-ros2",  op: "use-ros2",  act: "use-ros2",  card: "Ctl", confirm: "ARMS STIFFEN WHERE THEY ARE", pending: "STARTING ROS 2…", label: "USE ROS 2 · arms stiffen where they are" },
  { id: "btnUseGafro", url: "/api/arms/use-gafro", op: "use-gafro", act: "use-gafro", card: "Ctl", confirm: "ARMS STIFFEN WHERE THEY ARE", pending: "STARTING GAFRO…", label: "USE GAFRO · arms stiffen where they are" },
  // The handover: the arms move to stow under the old controller (hold the deadman), then rest there while it stops and the new one takes them. Its steps show below as it goes.
  { id: "btnToRos2",  url: "/api/arms/to-ros2",  op: "to-ros2",  act: "to-ros2",  card: "Ctl", confirm: "ARMS MOVE TO STOW · HOLD THE DEADMAN", pending: "HANDING OVER…", label: "HAND ARMS TO ROS 2 · park, then rest at stow" },
  { id: "btnToGafro", url: "/api/arms/to-gafro", op: "to-gafro", act: "to-gafro", card: "Ctl", confirm: "ARMS MOVE TO STOW · HOLD THE DEADMAN", pending: "HANDING OVER…", label: "HAND ARMS TO GAFRO · park, then rest at stow" },
  // To no controller: the arms go limp. Refused unless both read at stow.
  { id: "btnArmRelease", url: "/api/arms/release", op: "release", act: "release", card: "Ctl", confirm: "ARMS GO LIMP", pending: "RELEASING…", label: "RELEASE · arms go limp at stow" },
  { id: "btnArmPark", url: "/api/arms/park?arm=both", op: "park both", act: "park", card: "Both", confirm: "ARMS MOVE TO STOW", pending: "PARKING…", label: "PARK · arms move to stow" },
  // Stiff where they are: under gafro its hold; under ROS 2 an active arm is held at its pose and an inactive one activated in place. Never kept waiting by a running move.
  { id: "btnArmHold", url: "/api/arms/hold?arm=both", op: "hold both", act: "hold", card: "Both", confirm: "CONFIRM HOLD", pending: "HOLDING…", label: "HOLD · stiff where they are", instant: true },
  // gafro's alone. PREPARE: the person's word that they are at the e-stop, then each arm to ready; the confirmation names both. Nothing runs while a show does.
  { id: "btnArmPrepBoth", url: "/api/arms/prepare?arm=both", op: "prepare both", act: "prepare", card: "Gafro", confirm: "I AM AT THE E-STOP · ARMS MOVE TO READY", pending: "PREPARING…", label: "PREPARE FOR A SHOW · I am at the e-stop, arms move to ready" },
  // The attestation the arm service wants before any moving command: a person is at the e-stop, head tracking is off and the neck centred. Shown only while the
  // service answers, and only the one that applies. Ending it only takes capability away, so it never waits for a running move.
  { id: "btnArmSupL",   url: "/api/arms/supervise?arm=left",    op: "supervise left",    act: "supervise", card: "Gafro", confirm: "I AM AT THE E-STOP", pending: "…", label: "SUPERVISE L",       sup: { side: "left",  on: true } },
  { id: "btnArmSupR",   url: "/api/arms/supervise?arm=right",   op: "supervise right",   act: "supervise", card: "Gafro", confirm: "I AM AT THE E-STOP", pending: "…", label: "SUPERVISE R",       sup: { side: "right", on: true } },
  { id: "btnArmUnsupL", url: "/api/arms/unsupervise?arm=left",  op: "unsupervise left",  act: "supervise", card: "Gafro", confirm: "CONFIRM",            pending: "…", label: "END SUPERVISION L", sup: { side: "left",  on: false }, instant: true },
  { id: "btnArmUnsupR", url: "/api/arms/unsupervise?arm=right", op: "unsupervise right", act: "supervise", card: "Gafro", confirm: "CONFIRM",            pending: "…", label: "END SUPERVISION R", sup: { side: "right", on: false }, instant: true },
  { id: "btnArmActL",  url: "/api/arms/ready?arm=left",  op: "activate left",  act: "ready", card: "Gafro", confirm: "ARM MOVES TO READY", pending: "MOVING…", label: "READY L · moves to ready" },
  { id: "btnArmActR",  url: "/api/arms/ready?arm=right", op: "activate right", act: "ready", card: "Gafro", confirm: "ARM MOVES TO READY", pending: "MOVING…", label: "READY R · moves to ready" },
  // The hand follows the pose target the viewer (or the ROS bridge) publishes. The service refuses it until SUPERVISE, and `hold`, CH6 or the e-stop ends it.
  { id: "btnArmFollowL", url: "/api/arms/follow?arm=left",  op: "follow left",  act: "follow", card: "Gafro", confirm: "HAND FOLLOWS TARGET", pending: "STARTING…", label: "FOLLOW L · hand follows target" },
  { id: "btnArmFollowR", url: "/api/arms/follow?arm=right", op: "follow right", act: "follow", card: "Gafro", confirm: "HAND FOLLOWS TARGET", pending: "STARTING…", label: "FOLLOW R · hand follows target" },
  // Reads only (`arm doctor`): is the service up, is /joint_states publishing, is a viewer on the graph. It finds things, so its words are shown whatever its status was.
  { id: "btnArmDoctorL", url: "/api/arms/doctor?arm=left",  op: "doctor left",  confirm: "RUN CHECK", pending: "CHECKING…", label: "CHECK L", report: true },
  { id: "btnArmDoctorR", url: "/api/arms/doctor?arm=right", op: "doctor right", confirm: "RUN CHECK", pending: "CHECKING…", label: "CHECK R", report: true },
  // Shown only while that arm's CAN link is missing a restart can fix; the backend and openarm-up
  // refuse it during a show and under an energized arm.
  { id: "btnArmRelinkL", url: "/api/arms/relink?arm=left",  op: "relink left",  confirm: "CONFIRM RESTART", pending: "RESTARTING…", label: "RESTART LINK L", side: "left" },
  { id: "btnArmRelinkR", url: "/api/arms/relink?arm=right", op: "relink right", confirm: "CONFIRM RESTART", pending: "RESTARTING…", label: "RESTART LINK R", side: "right" },
];
let lastService = null;        // /api/arms/status "service": per arm, the arm service's status text (null if it does not answer)
let lastStack = null;          // /api/arms/status "stack": {name, why, controller: ros2|gafro|none|conflict, label, unavailable: {action: reason|null}}
let lastLinks = null;          // /api/arms/status "links": per arm, its CAN link and any message
let lastOp = null;             // the backend's op, from the last status poll
let lastViewers = null;        // /api/arms/status "viewers": per arm, {state: stopped|starting|running|failed, port, error}
let viewerNote = "";           // a refused or failed viewer, in words
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
    if (b.side) {                                                  // RESTART LINK: only while it helps
      const l = lastLinks && lastLinks[b.side];
      const show = !!(l && l.restartable) || (running && lastOp.name === b.op);
      el.hidden = !show;
      if (!show) continue;
    }
    if (b.sup) {                                                   // SUPERVISE / END SUPERVISION: the one that applies, while the service answers
      const st = lastService && lastService[b.sup.side];
      const supervised = !!(st && /^supervised: yes$/m.test(st));
      el.hidden = !(st && (b.sup.on ? !supervised : supervised));
      if (el.hidden) continue;
    }
    const f = flash[b.id];
    const mine = running ? lastOp.name === b.op : !!(requested && requested.id === b.id);
    const off = offReason(b);
    el.classList.toggle("busy", mine);
    el.disabled = !mine && (!!off || (!b.instant && (running || !!requested)));   // one arm action at a time, except the ones that only stop
    if (mine) {
      el.textContent = running ? `${b.pending} ${Math.round(lastOp.elapsed || 0)} s` : b.pending;
      el.title = "";
    } else if (f && f.until > now) {
      el.textContent = f.text;
    } else {
      el.textContent = b.label;
      el.title = off || (running ? `waits for: ${lastOp.name} (${Math.round(lastOp.elapsed || 0)} s)` : "");
    }
  }
  paintOffReasons();
}

/* Why a button is off: the controller that has the arms cannot do it (stack.unavailable), in the backend's words. Unknown until the first poll: everything is off then. */
function offReason(b) {
  if (!b.act) return null;
  if (!lastStack || !lastStack.unavailable) return "waiting for the arm status";
  return lastStack.unavailable[b.act] || null;
}

/* Each card says, in words, why its buttons are off: a tablet has no hover. One line when they share a reason, else one per button. */
function paintOffReasons() {
  const cards = {};
  for (const b of ARM_BUTTONS) {
    const el = $(b.id);
    if (!b.card || !el || el.hidden) continue;
    const off = offReason(b);
    if (off) (cards[b.card] = cards[b.card] || []).push([b.label.split(" · ")[0], off]);
  }
  const teleopOff = lastStack && lastStack.unavailable ? lastStack.unavailable.teleop : null;
  const start = $("btnTeleopStart");
  if (start && !start.classList.contains("armed")) start.disabled = !!teleopOff && !teleopUp;
  if (teleopOff && !teleopUp) cards.Teleop = [["START", teleopOff]];
  const conflict = !!(lastStack && lastStack.controller === "conflict");
  for (const card of ["Ctl", "Both", "Teleop", "Gafro"]) {
    const box = $("tpOff" + card);
    if (!box) continue;
    const rows = cards[card] || [];
    const same = rows.length > 0 && rows.every((r) => r[1] === rows[0][1]);
    // A CONFLICT is said once, on the CONTROLLER card; the others point to it.
    box.textContent = conflict && card !== "Ctl" && rows.length ? "off: CONFLICT (see CONTROLLER)"
      : same && rows.length > 1 ? rows[0][1] : rows.map(([label, why]) => `${label}: ${why}`).join("\n");
    box.className = "sub tp-off" + (conflict && card === "Ctl" ? " badx" : "");
  }
}

/* The controller, as a badge, with what it means; and while a change of controller runs (or after it ended), its steps, each with what the arms do. */
function paintController() {
  const c = lastStack;
  const el = $("tpCtl"), why = $("tpCtlWhy");
  if (el) {
    el.textContent = c ? c.label : "—";
    el.className = "badge " + (!c ? "dimbadge" : c.controller === "conflict" ? "bad" : c.controller === "none" ? "dimbadge" : "good");
  }
  if (why) why.textContent = c ? (c.controller === "none" ? "no controller has the arms: they are limp. Choose one." : c.why) : "—";
  const box = $("tpSteps");
  if (!box) return;
  const steps = (lastOp && lastOp.steps) || [];
  box.hidden = steps.length === 0;
  box.innerHTML = "";
  const MARK = { pending: "·", running: "▶", done: "✓", skipped: "–", failed: "✗" };
  const CLS = { running: "warnx", done: "ok", failed: "badx" };
  steps.forEach((s, i) => {
    const d = document.createElement("div");
    d.className = CLS[s.state] || "sub";
    d.style.whiteSpace = "pre-line";
    d.textContent = `${MARK[s.state] || "·"} ${i + 1}. ${s.title}\n    ${s.arms}` + (s.note ? `\n    ${s.note}` : "");
    box.append(d);
  });
}
/* The viewer (viser) is its own thing and not an arm action: it moves nothing, so it takes no confirmation and does not wait for a running move. One button per arm: it starts
 * the viewer, and once that runs it opens it (this host's address, the viewer's port) in a new tab; STOP VIEWER shows only while this dashboard's own viewer runs. */
function paintViewers() {
  for (const [side, L] of [["left", "L"], ["right", "R"]]) {
    const open = $("btnViewer" + L), stop = $("btnViewerStop" + L);
    if (!open) continue;
    const v = lastViewers && lastViewers[side];
    const state = v ? v.state : "stopped";
    open.textContent = state === "running" ? `OPEN VIEWER ${L}` : state === "starting" ? `STARTING VIEWER ${L}…` : `VIEWER ${L}`;
    open.disabled = state === "starting";
    open.title = state === "failed" && v.error ? `failed: ${v.error}` : "";
    if (stop) stop.hidden = state !== "running";
  }
  paintReport();
}

function paintReport() {
  const el = $("tpDoctor");
  if (!el) return;
  const parts = [];
  if (lastOp && !lastOp.running && lastOp.name && lastOp.name.startsWith("doctor") && lastOp.out) parts.push(`${lastOp.name}:\n${lastOp.out.trim()}`);
  for (const side of ["left", "right"]) {
    const v = lastViewers && lastViewers[side];
    if (v && v.error) parts.push(`viewer ${side}: ${v.state}: ${v.error}`);
  }
  if (viewerNote) parts.push(viewerNote);
  el.textContent = parts.join("\n\n");
  el.hidden = parts.length === 0;
}

async function viewerPress(op, side) {
  const v = lastViewers && lastViewers[side];
  if (op === "viewer" && v && v.state === "running") { window.open(`http://${location.hostname}:${v.port}/`, "_blank"); return; }
  viewerNote = "";
  try {
    const r = await fetch(`/api/arms/${op}?arm=${side}`, { method: "POST" });
    if (!r.ok) { let why = `HTTP ${r.status}`; try { why = (await r.json()).error || why; } catch (e) { /* keep the status */ } viewerNote = `${op} ${side} refused: ${why}`; }
  } catch (e) { viewerNote = `${op} ${side}: the dashboard did not answer`; }
  paintReport(); pollStatus();
}

function opOf(id) { const b = ARM_BUTTONS.find((x) => x.id === id); return b ? b.op : null; }

function noteArm(text, cls) {
  let n = $("tpArmNote");
  const anchor = $("btnArmHold");
  if (!n && anchor && anchor.parentElement) {
    n = document.createElement("div");
    n.id = "tpArmNote";
    n.className = "sub";
    anchor.parentElement.after(n);
  }
  if (n) { n.textContent = text || ""; n.className = cls || "sub"; }
}

async function onArmResponse(b, r) {
  if (r && r.ok && b.instant) {                                     // answered already: nothing to wait for
    flash[b.id] = { text: "DONE", until: Date.now() + 2500 };
    paintArmButtons(); setTimeout(paintArmButtons, 2600); pollStatus(); return;
  }
  if (r && r.ok) { requested = { id: b.id, at: Date.now() }; paintArmButtons(); pollStatus(); return; }
  let why = "the dashboard did not answer";
  if (r) { try { why = (await r.json()).error || `HTTP ${r.status}`; } catch (e) { why = `HTTP ${r.status}`; } }
  flash[b.id] = { text: r && r.status === 409 ? "BUSY — WAIT" : "REFUSED", until: Date.now() + 4000 };
  noteArm(`${b.label} refused: ${why}`, "badx");
  paintArmButtons();
  setTimeout(paintArmButtons, 4100);
}

function ensureRelinkButtons() {
  const anchor = $("btnArmHold");
  if (!anchor) return;
  for (const b of ARM_BUTTONS) {
    if (!b.side || $(b.id)) continue;
    const el = document.createElement("button");
    el.id = b.id;
    el.className = "sysbtn";
    el.textContent = b.label;
    el.hidden = true;
    anchor.after(el);
  }
}

function wire() {
  ensureRelinkButtons();
  const cb = window.confirmButton;
  if (typeof cb !== "function") return;
  for (const b of ARM_BUTTONS) {
    const el = $(b.id);
    if (el) cb(el, { url: () => b.url, confirm: b.confirm, pending: b.pending,
                     label: paintArmButtons, onResponse: (r) => onArmResponse(b, r) });
  }
  for (const [side, L] of [["left", "L"], ["right", "R"]]) {
    const open = $("btnViewer" + L), stop = $("btnViewerStop" + L);
    if (open) open.addEventListener("click", () => viewerPress("viewer", side));
    if (stop) stop.addEventListener("click", () => viewerPress("viewer-stop", side));
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
    // A CAN link in error is named per arm, in plain words. Unarmed motors that read disabled or
    // unheard are normal and are not shown as a fault; only a link that is missing, down or in error.
    for (const side of ["left", "right"]) {
      const l = s.links && s.links[side];
      if (!l || !l.problem || !l.message) continue;
      const d = document.createElement("div");
      d.className = "badx";
      d.textContent = l.message;
      r.append(d);
      if (l.code && typeof window.setHelp === "function") window.setHelp(d, l.code);
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
  teleopUp = up;
  lastExit = t.last_exit || null;
}

async function pollStatus() {
  try {
    const r = await fetch("/api/arms/status", { cache: "no-store" });
    if (r.ok) {
      const s = await r.json();
      const prev = lastOp;
      lastOp = s.op || null;
      lastLinks = s.links || null;
      lastService = s.service || null;
      lastStack = s.stack || null;
      lastViewers = s.viewers || null;
      paintViewers();
      feedFromService();
      // An op just finished: say how, on its own button, for a few seconds.
      if (prev && prev.running && lastOp && !lastOp.running && prev.name === lastOp.name) {
        const b = ARM_BUTTONS.find((x) => x.op === lastOp.name);
        if (b) {
          const ok = (lastOp.rc === 0 || b.report) && !lastOp.error;
          flash[b.id] = { text: ok ? "DONE" : "FAILED — SEE BELOW", until: Date.now() + (ok ? 2500 : 6000) };
          noteArm(ok ? `${b.label}: done in ${Math.round(lastOp.elapsed || 0)} s` : "", ok ? "ok" : "sub");
          setTimeout(paintArmButtons, ok ? 2600 : 6100);
        }
      }
      if (lastOp && lastOp.running) noteArm(`${lastOp.name} running — ${Math.round(lastOp.elapsed || 0)} s. Other arm buttons wait until it finishes.`, "warnx");
      paintStatus(s);
      paintArmButtons();
      paintController();
      // Shared, so the Operate page's ARMS tile needs no second poller: every read of this endpoint
      // also queries the motors over the arms' own CAN bus.
      window.dispatchEvent(new CustomEvent("opendubs-arms", { detail: s }));
    }
  } catch (e) { /* dashboard backend down; the rest of the page already says so */ }
}

/* ---- the bridge's own per-hand report: what is holding each hand back -------------------------
 * A grip refused because CH5 is not MANUAL, a hand whose pose stopped arriving, a refused home and
 * an unreachable target were only in the bridge's journal, so a grip that "did nothing" gave no
 * reason. The bridge now reports each hand's current condition with its troubleshooting code; this
 * shows it, fast, from an endpoint that costs no CAN traffic. */
// The codes the bridge sends (its status.py), and the two this page adds:
// @fault ARM_TELEOP_TRACKING_LOST @fault ARM_TELEOP_GRIP_REFUSED_MODE @fault ARM_TELEOP_STOPPED_MODE
// @fault ARM_TELEOP_HOME_REFUSED @fault ARM_TELEOP_UNREACHABLE
// @fault ARM_TELEOP_CERT_NOT_ACCEPTED @fault ARM_TELEOP_GRIPPER_TIMEOUT
let teleopUp = false;          // from the status poll
let lastExit = null;           // why the last bridge run ended, when stopped and known
let notesKey = "";

const SIDE_LABEL = { left: "L", right: "R" };

function paintHands(h) {
  const live = h && h.available;
  for (const side of ["left", "right"]) {
    const tile = $(side === "left" ? "tpHandL" : "tpHandR");
    if (!tile) continue;
    const s = live ? h.hands[side] : null;
    tile.classList.toggle("tracked", !!(s && s.tracked));
    tile.classList.toggle("engaged", !!(s && s.engaged));
    tile.classList.toggle("held", !!(s && s.notice && s.notice.code));
    tile.querySelector(".dot").textContent = s && s.tracked ? "●" : "○";
    tile.querySelector(".sub").textContent = `${SIDE_LABEL[side]} hand` + (
      !s ? "" : s.engaged ? " — GRIPPING" : s.homing ? " — HOMING" : s.tracked ? " — ready" : " — no data");
  }

  // [text, cls, code] rows; rebuilt only when they change, so a "?" stays tappable.
  const rows = [];
  const MODE_CODES = ["ARM_TELEOP_GRIP_REFUSED_MODE", "ARM_TELEOP_STOPPED_MODE"];
  if (live) {
    const handSaysMode = ["left", "right"].some((k) => MODE_CODES.includes(h.hands[k]?.notice?.code));
    if (h.mode !== "manual" && !handSaysMode) {
      rows.push([`CH5 is ${String(h.mode).toUpperCase()} — the arms follow only in MANUAL`,
                 "warnx", "ARM_TELEOP_GRIP_REFUSED_MODE"]);
    }
    for (const side of ["left", "right"]) {
      const s = h.hands[side];
      if (!s) continue;
      if (s.notice) {
        // No code is "no pose data yet": the page is not open, or its certificate was never accepted.
        rows.push([`${SIDE_LABEL[side]}: ${s.notice.text}`, s.notice.code ? "badx" : "warnx",
                   s.notice.code || "ARM_TELEOP_CERT_NOT_ACCEPTED", s.notice.detail]);
      } else if (s.last) {
        rows.push([`${SIDE_LABEL[side]}: cleared ${Math.round(s.last.cleared_s)} s ago — ${s.last.text}`,
                   "sub", s.last.code]);
      }
    }
  } else if (teleopUp) {
    rows.push([(h && h.reason) || "the bridge is not reporting", "sub", null]);
  } else if (lastExit) {
    rows.push([`last run ended: ${lastExit.text}`, "badx", lastExit.code]);
  }

  const key = JSON.stringify(rows);
  if (key === notesKey) return;
  notesKey = key;
  const box = $("tpNotices");
  if (!box) return;
  box.innerHTML = "";
  for (const [text, cls, code, detail] of rows) {
    const d = document.createElement("div");
    const t = document.createElement("span");
    d.className = cls;
    t.textContent = text.replace(/ -- /g, " — ");
    d.append(t);
    box.append(d);
    if (code) setHelp(t, code);
    if (detail) {                // a tablet has no hover, so the diagnosis is a line, not a tooltip
      const x = document.createElement("div");
      x.className = "sub tp-detail";
      x.textContent = detail;
      box.append(x);
    }
  }
}

async function pollHands() {
  try {
    const r = await fetch("/api/arms/teleop", { cache: "no-store" });
    paintHands(r.ok ? await r.json() : null);
  } catch (e) { paintHands(null); }
}

boot();
wire();
// Every second while an arm action runs, so its elapsed time moves; every two otherwise.
(function loop() {
  pollStatus().finally(() => setTimeout(loop, lastOp && lastOp.running ? 1000 : 2000));
})();
// The hands' report: well inside a second while the bridge runs, so a refused grip is on screen
// by the time the operator looks up.
(function handLoop() {
  pollHands().finally(() => setTimeout(handLoop, teleopUp ? 400 : 2000));
})();
