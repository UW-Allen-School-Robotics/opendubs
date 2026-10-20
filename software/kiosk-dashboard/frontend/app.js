/* OpenDubs kiosk dashboard — frontend logic.
   Connects to the backend websocket, renders system health + robot telemetry,
   and handles touch soft-controls (tab switching, acknowledge, power). */
"use strict";

const $ = (id) => document.getElementById(id);
const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));

/* ---------- Touch / tab controls ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setView(btn.dataset.view));
});
function setView(name) {
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.view === name)
  );
  document.querySelectorAll(".view").forEach((v) =>
    v.classList.toggle("active", v.id === "view-" + name)
  );
  try { sessionStorage.setItem("opendubs-view", name); } catch (e) {}
}

/* ---------- Picking up a redeploy by itself ----------
   The kiosk tablet has no reload control, so the page asks the dashboard every 15 s which version
   of the page files it serves, and reloads when that changes. It waits while a show runs (a reload
   would cut the show's audio) and while someone is touching the screen; the view survives it. */
(function followDeploys() {
  let seen = null, touched = 0;
  document.addEventListener("pointerdown", () => { touched = Date.now(); }, true);
  async function busy() {
    if (Date.now() - touched < 20000) return true;
    try {
      const p = await (await fetch("/api/choreo/palette", { cache: "no-store" })).json();
      return !!p.running;
    } catch (e) { return false; }   // no runner: no show can be running
  }
  async function check() {
    try {
      const v = (await (await fetch("/api/frontend-version", { cache: "no-store" })).json()).version;
      if (seen === null) seen = v;
      else if (v !== seen && !(await busy())) { location.reload(); return; }
    } catch (e) { /* dashboard restarting during the deploy: try again next round */ }
    setTimeout(check, 15000);
  }
  check();
})();
(function restoreView() {
  // Read now, applied once every script has run: operate.js opens on Operate when it loads.
  let v = null;
  try { v = sessionStorage.getItem("opendubs-view"); } catch (e) {}
  window.addEventListener("load", () => { if (v && document.getElementById("view-" + v)) setView(v); });
})();

if ($("btnAiToggle")) {
  $("btnAiToggle").addEventListener("click", () => {
    const isOff = $("btnAiToggle").classList.contains("off");
    fetch(`/api/tracker/${isOff ? "on" : "off"}`, { method: "POST" }).catch(() => {});
    $("btnAiToggle").textContent = isOff ? "STARTING..." : "STOPPING...";
  });
}

/* ---------- System power controls (confirm-on-tap to avoid accidental taps) ---------- */
function armButton(id, action, label) {
  const btn = $(id);
  if (!btn) return;
  let armed = false, timer = null;
  btn.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      btn.classList.add("armed");
      btn.textContent = "TAP AGAIN TO " + label;
      $("sysMsg").textContent = "⚠ confirm " + label.toLowerCase() + " — tap again within 6s";
      timer = setTimeout(() => {
        armed = false; btn.classList.remove("armed"); btn.textContent = label;
        $("sysMsg").textContent = " ";
      }, 6000);
    } else {
      clearTimeout(timer); armed = false; btn.classList.remove("armed");
      btn.textContent = label + "…";
      $("sysMsg").textContent = label.toLowerCase() + " sent…";
      fetch("/api/system/" + action, { method: "POST" })
        .then((r) => { $("sysMsg").textContent = r.ok ? label.toLowerCase() + " command sent" : "command failed"; })
        .catch(() => { $("sysMsg").textContent = "request failed"; });
    }
  });
}
armButton("btnReboot", "reboot", "REBOOT");
armButton("btnShutdown", "shutdown", "SHUTDOWN");

/* ---------- PDH switched-channel control (confirm-on-tap) ---------- */
let pdhSwState = null;       // true=on, false=off, null=unknown
const pdhBtn = $("btnPdhSw");
let pdhArmed = false, pdhTimer = null;
function updatePdhBtn() {
  if (!pdhBtn || pdhArmed) return;
  if (pdhSwState == null) { pdhBtn.textContent = "—"; pdhBtn.className = "sysbtn"; }
  else { pdhBtn.textContent = pdhSwState ? "CH23 OFF" : "CH23 ON"; pdhBtn.className = "sysbtn" + (pdhSwState ? " danger" : ""); }
}
if (pdhBtn) {
  pdhBtn.addEventListener("click", () => {
    if (pdhSwState == null) return;
    const target = pdhSwState ? "off" : "on";
    if (!pdhArmed) {
      pdhArmed = true; pdhBtn.classList.add("armed");
      pdhBtn.textContent = "CONFIRM CH23 " + target.toUpperCase();
      pdhTimer = setTimeout(() => { pdhArmed = false; pdhBtn.classList.remove("armed"); updatePdhBtn(); }, 5000);
    } else {
      clearTimeout(pdhTimer); pdhArmed = false; pdhBtn.classList.remove("armed");
      pdhBtn.textContent = target === "on" ? "TURNING ON…" : "TURNING OFF…";
      fetch("/api/pdh/" + target, { method: "POST" }).catch(() => {});
    }
  });
}

/* ---------- Generic confirm-on-tap action button ---------- */
// Arms on first tap, fires the POST on a second tap within `window` ms.
function confirmButton(btn, opts) {
  if (!btn) return;
  let armed = false, timer = null;
  const reset = () => { armed = false; btn.classList.remove("armed"); opts.label(); };
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    if (!armed) {
      armed = true; btn.classList.add("armed");
      btn.textContent = opts.confirm;
      timer = setTimeout(reset, opts.window || 5000);
    } else {
      clearTimeout(timer); armed = false; btn.classList.remove("armed");
      btn.textContent = opts.pending || "…";
      // opts.onResponse (optional) sees the server's answer -- a 409 "busy" refusal looked
      // exactly like success while the reply was discarded. null if the request itself failed.
      const sent = fetch(opts.url(), { method: "POST" });
      if (opts.onResponse) sent.then(opts.onResponse, () => opts.onResponse(null));
      else sent.catch(() => {});
      setTimeout(() => opts.label(), 1200);
    }
  });
}

/* ---------- "?" — the troubleshooting entry for a refusal or a fault ----------
   helpLink("CODE") makes a "?" that opens that code's entry in the troubleshooting docs
   (docs/reference/registry/). It opens in a side sheet, never by navigating: the kiosk app
   opens every link in place of the dashboard, which would drop the websocket, a show's audio
   and a teleop session. The sheet sits below the top bar, so E-STOP stays in reach.
   The docs come from the robot (/docs/, deployed with the dashboard) so they work with no
   internet and match the deployed code; a dashboard run without them falls back to the
   public site. `tools/faults.py --check` fails CI if a code named here is not registered. */
const HELP_PUBLIC = "https://uw-allen-school-robotics.github.io/opendubs/";
let helpBase = null;                       // resolved on first use: "/docs/" or HELP_PUBLIC
async function resolveHelpBase() {
  if (helpBase) return helpBase;
  try {
    const r = await fetch("/docs/reference/troubleshooting/", { method: "HEAD" });
    helpBase = r.ok ? "/docs/" : HELP_PUBLIC;
  } catch (e) { helpBase = HELP_PUBLIC; }
  return helpBase;
}
const helpAnchor = (code) => code.toLowerCase().replace(/_/g, "-");

function helpLink(code) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "help-q"; b.textContent = "?";
  b.dataset.code = code; b.title = "What this means";
  return b;
}
// Keep a "?" beside `el` showing `code`, or hide it when code is null. Safe to call every frame.
function setHelp(el, code) {
  if (!el) return;
  let q = el.nextElementSibling;
  if (!(q && q.classList.contains("help-q"))) {
    if (!code) return;
    q = helpLink(code); el.after(q);
  }
  if (code) { q.dataset.code = code; q.hidden = false; } else q.hidden = true;
}

async function openHelp(code) {
  let sheet = $("helpSheet");
  if (!sheet) {
    sheet = document.createElement("aside");
    sheet.id = "helpSheet"; sheet.setAttribute("aria-label", "Troubleshooting");
    sheet.innerHTML = '<div class="help-bar"><span class="help-code"></span>'
      + '<button type="button" class="help-close">CLOSE</button></div><iframe title="Troubleshooting"></iframe>';
    sheet.querySelector(".help-close").addEventListener("click", () => sheet.classList.remove("open"));
    document.body.appendChild(sheet);
  }
  const bar = $("topbar");
  sheet.style.top = (bar ? Math.max(0, bar.getBoundingClientRect().bottom) : 0) + "px";
  sheet.querySelector(".help-code").textContent = code;
  sheet.querySelector("iframe").src =
    (await resolveHelpBase()) + "reference/troubleshooting/#" + helpAnchor(code);
  sheet.classList.add("open");
}
document.addEventListener("click", (ev) => {
  const q = ev.target.closest && ev.target.closest(".help-q");
  if (q) { ev.preventDefault(); ev.stopPropagation(); openHelp(q.dataset.code); }
});
Object.assign(window, { helpLink, setHelp, openHelp });

// The e-stop sources the backend reports (status.py), by their troubleshooting codes.
const ESTOP_SOURCE_CODE = {
  "SOFTWARE": "ESTOP_SOFTWARE",            // @fault ESTOP_SOFTWARE
  "BUTTON": "ESTOP_BUTTON",                // @fault ESTOP_BUTTON
  "STALE TELEMETRY": "LINK_STAMPLC_LOST",  // @fault LINK_STAMPLC_LOST
};
const estopCode = (source) => {
  const first = (source || "").split(" + ")[0];
  return ESTOP_SOURCE_CODE[first] || null;
};

/* ---------- E-STOP / re-arm (STAMPLC + safety) ---------- */
// E-STOP is the safe direction → single tap fires immediately (no arming delay).
const estopBtn = $("btnEstop");
if (estopBtn) {
  estopBtn.addEventListener("click", () => {
    estopBtn.textContent = "E-STOP SENT";
    fetch("/api/estop", { method: "POST" }).catch(() => {});
    setTimeout(() => (estopBtn.textContent = "E-STOP"), 1500);
  });
}
// RE-ARM re-energizes the contactor → guarded by confirm-on-tap.
confirmButton($("btnEstopClear"), {
  url: () => "/api/estop/clear",
  confirm: "CONFIRM RE-ARM", pending: "RE-ARMING…",
  label: () => { $("btnEstopClear").textContent = "RE-ARM"; },
  // A refused re-arm lists why, each reason with its "?". Cleared by the next accepted one.
  onResponse: async (r) => {
    const btn = $("btnEstopClear");
    let why = $("rearmWhy");
    if (!why && btn) { why = document.createElement("div"); why.id = "rearmWhy"; why.className = "help-why"; btn.after(why); }
    if (!why) return;
    why.replaceChildren();
    if (!r || r.ok) return;
    let j = {};
    try { j = await r.json(); } catch (e) {}
    const lines = j.blockers || [j.error || `refused (HTTP ${r.status})`];
    lines.forEach((txt, i) => {
      const row = document.createElement("div");
      row.textContent = "RE-ARM REFUSED · " + txt;
      why.appendChild(row);
      const code = (j.codes || [])[i];
      if (code) row.appendChild(helpLink(code));
    });
  },
});

/* ---------- I²C full bus scan (on-demand — STAMPLC page) ---------- */
const i2cScanBtn = $("btnI2cScan");
if (i2cScanBtn) {
  i2cScanBtn.addEventListener("click", () => {
    i2cScanBtn.textContent = "SCANNING…"; i2cScanBtn.disabled = true;
    fetch("/api/i2c/scan", { method: "POST" }).catch(() => {});
    // The firmware sweep + next ~1Hz status push refreshes i2cList via renderI2C.
    setTimeout(() => { i2cScanBtn.textContent = "SCAN"; i2cScanBtn.disabled = false; }, 2500);
  });
}

/* ---------- Mast encoder zero/home (confirm-on-tap) ---------- */
confirmButton($("btnEncReset"), {
  url: () => "/api/enc/reset",
  confirm: "CONFIRM ZERO", pending: "ZEROING…",
  label: () => { $("btnEncReset").textContent = "ZERO / HOME"; },
});

/* ---------- Clock ---------- */
setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString("en-GB");
}, 1000);

/* ---------- Helpers ---------- */
function fmtUptime(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return (d ? d + "d " : "") + h + "h " + m + "m";
}
function tempClass(c) {
  if (c == null) return "";
  if (c >= 80) return "hot";
  if (c >= 65) return "warm";
  return "";
}

/* ---------- Renderers ---------- */
let lastSystem = null;   // latest system snapshot — THERMAL borrows its component temps
function renderSystem(s) {
  lastSystem = s;
  // CPU
  $("cpuPct").textContent = Math.round(s.cpu.pct) + "%";
  $("cores-n").textContent = s.cpu.cores + " cores";
  if (s.cpu.load) $("load").textContent = "load " + s.cpu.load.map((x) => x.toFixed(2)).join(" ");
  const coresEl = $("cores");
  const per = s.cpu.per_core || [];
  if (coresEl.children.length !== per.length) {
    coresEl.innerHTML = per.map(() => '<div class="core"><i></i></div>').join("");
  }
  per.forEach((v, i) => {
    const bar = coresEl.children[i].firstChild;
    bar.style.height = clamp(v, 0, 100) + "%";
    bar.style.background = v >= 85 ? "var(--bad)" : v >= 60 ? "var(--warn)" : "var(--accent)";
  });

  // GPU
  const g = s.gpu.pct;
  $("gpuPct").textContent = g == null ? "n/a" : g + "%";
  $("gpuBar").style.height = (g == null ? 0 : clamp(g, 0, 100)) + "%";
  $("emcPct").textContent = s.gpu.emc_pct == null ? "—" : s.gpu.emc_pct + "%";

  // Memory
  $("memBar").style.width = s.mem.pct + "%";
  $("memTxt").textContent = `${s.mem.used_mb} / ${s.mem.total_mb} MB (${s.mem.pct}%)`;
  $("swapBar").style.width = s.swap.pct + "%";
  $("swapTxt").textContent = `swap ${s.swap.used_mb} / ${s.swap.total_mb} MB`;

  // Temps (show up to 6, sorted hottest first)
  const temps = Object.entries(s.temps_c || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  $("tempList").innerHTML = temps
    .map(([k, v]) => `<li><span>${k}</span><span class="${tempClass(v)}">${v.toFixed(1)}°C</span></li>`)
    .join("") || '<li><span>—</span><span>n/a</span></li>';

  // Power
  const rails = s.power_mw || {};
  const totalMw = rails.VIN || rails.VDD_IN || rails.VIN_SYS_5V0 ||
    Object.values(rails).reduce((a, b) => a + b, 0);
  $("pwrTotal").textContent = totalMw ? (totalMw / 1000).toFixed(1) + " W" : "n/a";
  const topRails = Object.entries(rails).sort((a, b) => b[1] - a[1]).slice(0, 4);
  $("pwrList").innerHTML = topRails
    .map(([k, v]) => `<li><span>${k}</span><span>${(v / 1000).toFixed(1)}W</span></li>`)
    .join("");

  // Disk / Net / host
  $("diskBar").style.width = s.disk.pct + "%";
  $("diskTxt").textContent = `${s.disk.used_gb} / ${s.disk.total_gb} GB (${s.disk.pct}%)`;
  $("netUp").textContent = s.net.up_kbps;
  $("netDown").textContent = s.net.down_kbps;
  $("ipTxt").textContent = "ip " + (s.net.ip || "—");
  $("host").textContent = s.host.ip || "thor";
  $("pmode").textContent = s.host.power_mode || "—";
  $("uptime").textContent = fmtUptime(s.host.uptime_s);
  const pm = document.getElementById("pwrMode");
  if (pm) pm.textContent = s.host.power_mode || "—";
}

/* ---------- RC telemetry (ported from rc_serial_probe/tools/telemetry_gui.py) ---------- */
// [channel, role, kind]
const CH_DEFS = [
  ["1", "Rotate  L / R", "axis"],
  ["2", "Mast  Up / Down", "axis"],
  ["3", "FWD / RVS", "axis"],
  ["4", "Strafe  L / R", "axis"],
  ["5", "Authority (OFF/RC/AUTO)", "tri"],
  ["6", "Deadman", "dead"],
  ["7", "Mast Rocker (OFF/ON/RSVD)", "tri"],
  ["8", "Speed control", "uni"],
];
const DEFAULT_RANGE = { min: 1000, max: 2000, center: 1500 };

const rcAxis = (raw, r) => raw <= r.center
  ? Math.max(-1, (raw - r.center) / Math.max(r.center - r.min, 1))
  : Math.min(1, (raw - r.center) / Math.max(r.max - r.center, 1));
const rcUni = (raw, r) => clamp((raw - r.min) / Math.max(r.max - r.min, 1), 0, 1);
// 3-position switch thresholds, relative to the range (works for PWM or SBUS)
const triPos = (raw, r) => {
  const q = (r.max - r.min) * 0.25;
  return raw < r.center - q ? "low" : raw > r.center + q ? "high" : "mid";
};

function rcInterpret(i, raw, r) {
  const kind = CH_DEFS[i][2];
  if (kind === "axis") {
    const v = rcAxis(raw, r);
    let txt;
    if (i === 0) txt = `${v > 0.05 ? "R" : v < -0.05 ? "L" : "C"} ${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
    else if (i === 1) txt = `${v > 0.05 ? "UP" : v < -0.05 ? "DOWN" : "HOLD"} ${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
    else txt = `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
    return { txt, cls: "axis", bar: { left: (v < 0 ? 50 + v * 50 : 50), width: Math.abs(v) * 50 } };
  }
  if (kind === "uni") {
    const v = rcUni(raw, r);
    return { txt: v.toFixed(2), cls: "axis", bar: { left: 0, width: v * 100, uni: true } };
  }
  if (kind === "dead") {
    const armed = raw > r.center;
    return { txt: armed ? "ARMED" : "disarmed", cls: armed ? "good" : "bad" };
  }
  // tri
  const pos = triPos(raw, r);
  if (i === 4) return { low: { txt: "OFF (LOW)", cls: "bad" }, mid: { txt: "RC_MANUAL", cls: "good" }, high: { txt: "AUTONOMOUS", cls: "warn" } }[pos];
  if (i === 6) return { low: { txt: "MAST RC OFF", cls: "bad" }, mid: { txt: "MAST RC ON", cls: "good" }, high: { txt: "RESERVED", cls: "warn" } }[pos];
  return { low: { txt: "LOW [-1]", cls: "axis" }, mid: { txt: "MID [ 0]", cls: "dim" }, high: { txt: "HIGH [+1]", cls: "axis" } }[pos];
}

function renderRC(t) {
  const rc = t.rc || {};
  const ch = rc.channels;
  const r = rc.range || DEFAULT_RANGE;
  // The backend sets link "ok" ONLY when a valid SBUS frame is fresh. A channels array can
  // still be present-but-stale (e.g. all-zero after a reboot with the TX off) — treating that
  // as live shows misleading zero bars. Gate on the actual link, not on channels existing.
  const linkDown = rc.link !== "ok";
  const failsafe = !!rc.failsafe;
  const live = !!ch && !linkDown;

  // Overlay over the RC bars whenever the inputs aren't trustworthy (no link, or receiver
  // failsafe). Makes "controller off" unmistakable instead of rendering dead/zero values.
  const ov = $("rcOverlay");
  if (ov) {
    const big = ov.querySelector(".rc-overlay-txt");
    const sub = ov.querySelector(".rc-overlay-sub");
    if (linkDown) {
      ov.className = "rc-overlay down";
      if (big) big.textContent = "RC DISCONNECTED";
      if (sub) sub.textContent = "controller off · no SBUS link";
    } else if (failsafe) {
      ov.className = "rc-overlay failsafe";
      if (big) big.textContent = "RC FAILSAFE";
      if (sub) sub.textContent = "receiver failsafe · inputs ignored";
    } else {
      ov.className = "rc-overlay hidden";
    }
  }

  // source badge
  const src = $("rcSrc");
  src.textContent = (t.source || "—").toUpperCase();
  src.className = "badge " + (t.source === "sim" ? "warn" : t.source === "pixhawk" ? "good" : "dimbadge");

  // header badges from interpreted channels
  if (live) {
    const auth = rcInterpret(4, ch[4], r);
    $("rcAuth").textContent = "AUTH " + auth.txt;
    $("rcAuth").className = "badge " + auth.cls;
    const dead = rcInterpret(5, ch[5], r);
    $("rcDead").textContent = dead.txt === "ARMED" ? "DEADMAN ARMED" : "DEADMAN OFF";
    $("rcDead").className = "badge " + dead.cls;
    $("rcSpeed").textContent = "SPEED " + rcUni(ch[7], r).toFixed(2);
    $("rcSpeed").className = "badge";
    $("rcFoot").textContent =
      `link:${rc.link || "—"}  failsafe:${rc.failsafe ? "FAILSAFE" : "ok"}  frame:${rc.frame_lost ? "LOST" : "ok"}` +
      (rc.rssi != null ? `  rssi=${rc.rssi}` : "") +
      (rc.frame_count != null ? `  f=${rc.frame_count}` : "") +
      (rc.stats ? `   SBUS ok=${rc.stats.ok} bad=${rc.stats.bad} last_ok=${rc.stats.last_ok_ms}ms` : "");
  } else {
    ["rcAuth", "rcDead", "rcSpeed"].forEach((id) => { $(id).className = "badge dimbadge"; });
    $("rcAuth").textContent = "AUTH —"; $("rcDead").textContent = "DEADMAN —"; $("rcSpeed").textContent = "SPEED —";
    $("rcFoot").textContent = "no RC data";
  }

  // build rows once
  const rows = $("rcRows");
  if (rows.children.length !== CH_DEFS.length) {
    rows.innerHTML = CH_DEFS.map(([c, role, kind], i) => {
      const bar = (kind === "axis" || kind === "uni")
        ? `<div class="rc-track"><div class="center"></div><div class="fill" id="rcfill_${i}"></div></div>`
        : `<span></span>`;
      return `<div class="rc-row"><span class="ch">${c}</span><span class="role">${role}</span>` +
        `<span class="raw" id="rcraw_${i}">--</span>${bar}<span class="val" id="rcval_${i}">--</span></div>`;
    }).join("");
  }
  CH_DEFS.forEach(([c, role, kind], i) => {
    const rawEl = document.getElementById(`rcraw_${i}`);
    const valEl = document.getElementById(`rcval_${i}`);
    if (!live) { rawEl.textContent = "--"; valEl.textContent = "--"; valEl.className = "val dim"; return; }
    const raw = ch[i];
    rawEl.textContent = raw;
    const res = rcInterpret(i, raw, r);
    valEl.textContent = res.txt;
    valEl.className = "val " + res.cls;
    if (res.bar) {
      const fill = document.getElementById(`rcfill_${i}`);
      if (fill) { fill.style.left = res.bar.left + "%"; fill.style.width = res.bar.width + "%"; fill.className = "fill" + (res.bar.uni ? " uni" : ""); }
    }
  });
}

/* ---------- NAV: GPS + optical flow ---------- */
function renderNav(t) {
  const g = t.gps || { present: false };
  const f = t.flow || { present: false };

  const fix = $("gpsFix");
  const gpsIds = ["gpsSats", "gpsHdop", "gpsSpeed", "gpsHdg", "gpsLat", "gpsLon", "gpsAlt", "gpsRel"];
  if (!g.present) {
    fix.textContent = "NO DATA"; fix.className = "badge dimbadge";
    gpsIds.forEach((id) => ($(id).textContent = "—"));
  } else {
    const ft = g.fix_type;
    fix.textContent = g.fix_label || "—";
    fix.className = "badge " + (ft >= 3 ? "good" : ft === 2 ? "warn" : "bad");
    $("gpsSats").textContent = g.sats != null ? g.sats : "—";
    $("gpsHdop").textContent = g.hdop != null ? g.hdop.toFixed(1) : "—";
    $("gpsSpeed").textContent = g.speed_ms != null ? g.speed_ms.toFixed(1) : "—";
    $("gpsHdg").textContent = g.heading_deg != null ? Math.round(g.heading_deg) + "°" : "—";
    $("gpsLat").textContent = g.lat != null ? g.lat.toFixed(6) : "—";
    $("gpsLon").textContent = g.lon != null ? g.lon.toFixed(6) : "—";
    $("gpsAlt").textContent = g.alt_m != null ? g.alt_m.toFixed(1) : "—";
    $("gpsRel").textContent = g.rel_alt_m != null ? g.rel_alt_m.toFixed(1) : "—";
  }

  const fs = $("flowState");
  if (!f.present) {
    fs.textContent = "NO DATA"; fs.className = "badge dimbadge";
    ["flowVx", "flowVy", "flowGnd", "flowQual"].forEach((id) => ($(id).textContent = "—"));
    $("flowRaw").textContent = "flow — , —";
  } else {
    const q = f.quality != null ? f.quality : 0;
    fs.textContent = "Q " + q;
    fs.className = "badge " + (q >= 128 ? "good" : q >= 40 ? "warn" : "bad");
    $("flowVx").textContent = f.vx_ms != null ? f.vx_ms.toFixed(2) + " m/s" : "—";
    $("flowVy").textContent = f.vy_ms != null ? f.vy_ms.toFixed(2) + " m/s" : "—";
    $("flowGnd").textContent = f.ground_distance_m != null ? f.ground_distance_m.toFixed(2) + " m" : "—";
    $("flowQual").textContent = q;
    $("flowRaw").textContent = `flow ${f.flow_x != null ? f.flow_x : "—"} , ${f.flow_y != null ? f.flow_y : "—"}`;
  }
}

/* ---------- MASTER STATUS — robot FSM (docs/state-machines.md) ---------- */
const STATE_COLORS = { white: "#dfe6ee", green: "var(--good)", cyan: "#28d6d6", amber: "var(--warn)", red: "var(--bad)" };
function setChip(id, txt, cls) { const e = $(id); if (e) { e.textContent = txt; e.className = "chip " + (cls || ""); } }
function subStateClass(s) { return s === "ACTIVE" ? "ok" : s === "READY" ? "ready" : s === "FAULT" ? "bad" : "dim"; }

function renderRobotState(rs) {
  if (!rs) return;
  const st = $("rsState");
  if (st) { st.textContent = rs.label || "—"; st.style.color = STATE_COLORS[rs.color] || "var(--txt)"; }
  if ($("rsReason")) $("rsReason").textContent = rs.reason || "";
  if ($("rsLed")) $("rsLed").textContent = rs.color ? `${rs.color} · ${rs.anim}` : "—";

  const inp = rs.inputs || {};
  setChip("inAuth", inp.authority || "—",
    inp.authority === "AUTONOMOUS" ? "cyan" : inp.authority === "RC_MANUAL" ? "ok" : "dim");
  setHelp($("inAuth"), inp.authority === "OFF" ? "RC_AUTHORITY_OFF" : null);
  setChip("inDead", inp.deadman == null ? "—" : inp.deadman ? "ARMED" : "OFF", inp.deadman ? "ok" : "bad");
  setHelp($("inDead"), inp.deadman === false ? "DRIVE_DEADMAN" : null);
  const src = (inp.estop_sources && inp.estop_sources.length) ? inp.estop_sources.join(" + ") : null;
  setChip("inSafe", src ? src : "clear", src ? "bad" : "ok");
  setHelp($("inSafe"), estopCode(src));
  setChip("inLink", inp.link ? "OK" : "NO LINK", inp.link ? "ok" : "bad");
  setHelp($("inLink"), inp.link === false ? "RC_NO_LINK" : null);
  setChip("inContactor", inp.contactor == null ? "—" : inp.contactor ? "ENERGIZED" : "OPEN",
    inp.contactor ? "ok" : "dim");
  setHelp($("inContactor"), inp.contactor === false ? "CONTACTOR_OPEN" : null);
  setChip("inMotion", inp.motion_enabled == null ? "—" : inp.motion_enabled ? "ENABLED" : "INHIBITED",
    inp.motion_enabled ? "ok" : "bad");
  setChip("inCharger", inp.charging == null ? "—" : inp.charging ? "CHARGING" : "none",
    inp.charging ? "warn" : "dim");

  // FSM node strip
  const flow = $("fsmFlow");
  if (flow) {
    if (!flow.dataset.built) {
      flow.innerHTML = (rs.states || []).map((s) =>
        `<span class="fsm-node" data-s="${s}">${s.replace(/_/g, " ")}</span>`).join("");
      flow.dataset.built = "1";
    }
    flow.querySelectorAll(".fsm-node").forEach((n) =>
      n.classList.toggle("active", n.dataset.s === rs.state));
  }

  // Subsystem tiles
  const subs = rs.subsystems || {};
  const order = ["base", "mast", "left_arm", "right_arm"];
  const wrap = $("rsSubs");
  if (wrap) {
    if (!wrap.dataset.built) {
      wrap.innerHTML = order.map((k) =>
        `<div class="sub-tile" id="sub_${k}"><div class="sub-name">${(subs[k] && subs[k].label) || k}</div>` +
        `<div class="sub-state" id="subst_${k}">—</div></div>`).join("");
      wrap.dataset.built = "1";
    }
    order.forEach((k) => {
      const s = (subs[k] && subs[k].state) || "—";
      const el = $("subst_" + k), tile = $("sub_" + k);
      if (el) el.textContent = s;
      if (tile) tile.className = "sub-tile " + subStateClass(s);
    });
  }
}

/* ---------- INFO (system + subsystem status grid) ---------- */
function setDot(id, cls) { const e = $(id); if (e) e.className = "sdot " + (cls || ""); }


/* ---------- Pinpoint odometry (status.odo, from the StamPLC [ODO] line) ---------- */
function renderOdo(o) {
  const set = (id, v) => { const e = $(id); if (e) e.textContent = v; };
  const badge = $("odoState");
  if (!o || o.ok !== true) {
    ["odoX","odoY","odoH","odoHr","odoVx","odoVy","odoEx","odoEy","odoLoop","odoErr"].forEach(i => set(i, "—"));
    if (badge) { badge.textContent = "NO DATA"; badge.className = "badge badbadge"; }
    return;
  }
  // pod flags here are PRESENCE (firmware already inverted the FAULT_*_NOT_DETECTED bits)
  const faults = [];
  if (!o.ready)      faults.push("NOT READY");
  if (o.calibrating) faults.push("CALIBRATING");
  if (!o.x_pod)      faults.push("X POD MISSING");
  if (!o.y_pod)      faults.push("Y POD MISSING");
  if (o.imu_runaway) faults.push("IMU RUNAWAY");
  if (o.bad_read)    faults.push("BAD READ");
  if (badge) {
    badge.textContent = faults.length ? faults.join(" · ") : "READY";
    badge.className = "badge " + (faults.length ? "badbadge" : "okbadge");
  }
  const n1 = (v, u) => (v == null ? "—" : v.toFixed(1) + u);
  set("odoX",  n1(o.x_mm, " mm"));
  set("odoY",  n1(o.y_mm, " mm"));
  // The Pinpoint's heading is continuous — it counts whole turns — and so is the telemetry,
  // which is what anything fusing odometry wants. Wrap only for display, to (-180, 180].
  const wrapDeg = (rad) => { const d = (((rad * 180 / Math.PI) % 360) + 360) % 360; return d > 180 ? d - 360 : d; };
  set("odoH",  o.heading_rad == null ? "—" : wrapDeg(o.heading_rad).toFixed(1) + "°");
  set("odoHr", o.heading_rad == null ? "—" : o.heading_rad.toFixed(4));
  set("odoVx", n1(o.vx, " mm/s"));
  set("odoVy", n1(o.vy, " mm/s"));
  set("odoEx", o.enc_x == null ? "—" : o.enc_x.toLocaleString());
  set("odoEy", o.enc_y == null ? "—" : o.enc_y.toLocaleString());
  set("odoLoop", o.loop_us == null ? "—" : o.loop_us + " µs");
  set("odoErr", (o.fail ?? "—") + " / " + (o.crc_bad ?? "—"));
}

// The robot's internet uplink and this tablet's own Wi-Fi, from the router poller
// (status.net.uplink / net.robot_wifi; notes/design/network-router-api.md §4).
const UPLINK_DOT = { GOOD: "ok", WEAK: "warn", BUSY: "warn", CAPTIVE: "warn", DOWN: "bad", NO_ROUTER: "bad" };
const dbm = (v) => (v < 0 ? "\u2212" + -v : v) + " dBm";
function renderUplink(net, me) {
  if (!$("stUplink")) return;
  const u = net.uplink || {};
  $("stUplink").textContent = u.say || u.state || "—";
  // The signal in the label, unless the say line already gives it.
  $("stUplinkDbm").textContent = u.signal_dbm == null || /dBm/.test(u.say || "") ? "" : "· " + dbm(u.signal_dbm);
  setDot("stUplinkDot", UPLINK_DOT[u.state]);
  const mine = (net.robot_wifi || []).find((c) => me && c.ip === me);
  $("stTabletWifi").textContent = mine && mine.signal_dbm != null
    ? "this tablet " + dbm(mine.signal_dbm) + (mine.ccq_pct == null ? "" : " · CCQ " + mine.ccq_pct + "%")
    : "";
}

function renderStatus(st) {
  try { renderOdo(st && st.odo); } catch (e) {}
  const w = (st.net && st.net.wired) || {};
  const wf = (st.net && st.net.wifi) || {};
  $("stWired").textContent = w.ip ? `${w.ip} (${w.iface || "eth"})` : (w.up ? "up · no IP" : "down");
  setDot("stWiredDot", w.ip ? "ok" : w.up ? "warn" : "bad");

  $("stWifi").textContent = wf.ssid ? `${wf.ssid}${wf.ip ? " · " + wf.ip : ""}` : "not connected";
  setDot("stWifiDot", wf.ssid && wf.ip ? "ok" : wf.ssid ? "warn" : "bad");

  renderUplink(st.net || {}, st.client_ip);

  const rl = st.rc_link || {};
  $("stMav").textContent = rl.link ? ("RC " + (rl.mode || "linked")) : "no RC link";
  setDot("stMavDot", rl.link ? "ok" : "bad");

  const plc = st.stamplc || {};
  $("stPlc").textContent = (plc.detected ? "detected" : "not detected")
    + (plc.port ? " · " + plc.port : "");
  setDot("stPlcDot", plc.detected ? "ok" : "bad");

  const can = st.can || {};
  [["L", "left"], ["R", "right"]].forEach(([s, k]) => {
    const a = can[k] || {};
    $("stArm" + s).textContent = a.up ? "CAN up" : a.present ? "CAN idle" : "not connected";
    setDot("stArm" + s + "Dot", a.up ? "ok" : a.present ? "warn" : "bad");
  });

  const b = (st.power && st.power.pack) || {};
  if (b.present) {
    const parts = [];
    if (b.soc != null) parts.push(Math.round(b.soc) + "%");
    if (b.voltage != null) parts.push(b.voltage.toFixed(1) + "V");
    if (b.current_a != null) parts.push((b.current_a >= 0 ? "+" : "") + b.current_a.toFixed(1) + "A");
    $("stBatt").textContent = parts.join(" · ") || "present";
    setDot("stBattDot", b.soc != null ? (b.soc >= 40 ? "ok" : b.soc >= 15 ? "warn" : "bad") : (b.voltage != null ? "ok" : "warn"));
  } else { $("stBatt").textContent = "no data"; setDot("stBattDot", ""); }

  const cg = (st.power && st.power.charger) || {};
  if ($("stCharger")) {
    // Only charge current is sensed, so report charging/idle — not a connected we can't verify.
    $("stCharger").textContent = cg.charging == null ? "no data" : cg.charging ? "charging" : "idle";
    setDot("stChargerDot", cg.charging == null ? "" : cg.charging ? "ok" : "");
  }

  // POWER SOURCE card (POWER tab)
  const bp = (st.power && st.power.battery) || {};
  if ($("srcBattV")) {
    $("srcBattV").textContent = bp.voltage != null ? bp.voltage.toFixed(1) + " V" : "— V";
    $("srcBattPct").textContent = bp.pct != null ? bp.pct + " %" : "(no gauge)";
  }

  // PDH (POWER tab) — header + switch state + per-channel faults
  const pdh = (st.power && st.power.pdh) || null;
  const flt = (st.power && st.power.pdh_fault) || null;
  const nFault = flt ? flt.filter(Boolean).length : 0;
  if ($("pdhState")) {
    if (!pdh) {
      $("pdhState").textContent = "no data"; $("pdhState").className = "badge dimbadge";
      ["pdhV", "pdhI", "pdhP"].forEach((id) => { if ($(id)) $(id).textContent = "—"; });
      pdhSwState = null;
    } else {
      const f = [];
      if (pdh.brn) f.push("BRN"); if (pdh.can) f.push("CAN"); if (pdh.hw) f.push("HW");
      if (nFault) f.push(nFault + "CH");
      $("pdhState").textContent = f.length ? "FAULT " + f.join("/") : (pdh.sw ? "SW ON" : "SW OFF");
      $("pdhState").className = "badge " + (f.length ? "bad" : pdh.sw ? "good" : "warn");
      $("pdhV").textContent = pdh.v.toFixed(1) + " V";
      $("pdhI").textContent = pdh.i.toFixed(1) + " A";
      $("pdhP").textContent = pdh.p.toFixed(1) + " W";
      pdhSwState = pdh.sw;
    }
    updatePdhBtn();
  }
  // PDH per-channel currents + breaker faults
  const chs = (st.power && st.power.pdh_ch) || null;
  const grid = $("pdhChannels");
  if (grid) {
    if (!grid.dataset.built) {
      const cell = (i) => `<div class="pdh-cell" id="pdhc_${i}"><span class="c">c${i}${i === 23 ? " ⚡SW" : ""}</span><span class="a" id="pdhca_${i}">—</span></div>`;
      const row = (s, n) => Array.from({ length: n }, (_, k) => cell(s + k)).join("");
      grid.innerHTML =
        `<div class="pdh-row hi">${row(0, 10)}</div>` +
        `<div class="pdh-row hi">${row(10, 10)}</div>` +
        `<div class="pdh-lowwrap"><span class="lowlbl">LOW CURRENT</span><div class="pdh-row low">${row(20, 4)}</div></div>`;
      grid.dataset.built = "1";
    }
    for (let i = 0; i < 24; i++) {
      const a = chs ? chs[i] : null;
      const faulted = !!(flt && flt[i]);
      const av = $("pdhca_" + i), cell = $("pdhc_" + i);
      if (av) av.textContent = faulted ? "FLT" : (a != null ? a.toFixed(1) + "A" : "—");
      if (cell) cell.className = "pdh-cell" + (i === 23 ? " sw" : "")
        + (faulted ? " fault" : (a != null && a > 0.1 ? " live" : ""));
    }
  }

  // SAFETY banner (top of MASTER STATUS)
  const sf = st.safety || {};
  const banner = $("safetyBanner"), sbState = $("sbState");
  if (banner && sbState) {
    let cls = "", txt = "NO DATA";
    if (!sf.fresh) { cls = ""; txt = "NO DATA"; }
    else if (sf.estopped) { cls = "estop"; txt = "E-STOPPED · " + (sf.source || "?"); }
    else if (sf.motion_enabled) { cls = "ok"; txt = "MOTION ENABLED"; }
    else { cls = "warn"; txt = "DISARMED"; }
    banner.className = "safety-banner " + cls;
    sbState.textContent = txt;
    setHelp(sbState, sf.fresh && sf.estopped ? estopCode(sf.source) : null);
    $("sbContactor").textContent = sf.contactor == null ? "—" : sf.contactor ? "energized" : "open";
    $("sbBtn").textContent = sf.estop_button == null ? "—" : sf.estop_button ? "PRESSED" : "clear";
    $("sbSw").textContent = sf.software_estop == null ? "—" : sf.software_estop ? "LATCHED" : "clear";
  }

}

/* ---------- STAMPLC: digital inputs, relays, sensors, safety ---------- */
let relayState = [null, null, null, null];   // latest relay states (for toggle target)

function buildRelayRows(labels) {
  const list = $("relayList");
  if (!list || list.dataset.built) return;
  // R1/R2 are the scissor lift's DOWN and UP legs. They are NOT free relay toggles: pressing
  // them raw bypasses the Z-axis driver and its pinch guard. They show state only, and the
  // Z-AXIS row below drives the lift through /api/mast instead. R3 is the only spare.
  list.innerHTML = labels.map((lbl, i) => {
    const ctl = (i === 3)
      ? `<button class="iobtn" id="relayBtn_${i}" data-ch="${i}">—</button>`
      : `<span class="iolock" title="${i === 1 || i === 2
            ? 'lift leg — use the Z-AXIS control so the pinch guard applies'
            : 'not dashboard-controllable'}">🔒</span>`;
    return `<div class="iorow"><span class="iolabel">RELAY${i + 1} ${lbl}</span>` +
      `<span class="sdot" id="relayDot_${i}"></span><span id="relayTxt_${i}">—</span>${ctl}</div>`;
  }).join("") +
    `<div class="iorow"><span class="iolabel">Z-AXIS</span>` +
    `<span id="mastTxt">—</span>` +
    `<button class="iobtn" id="mastUp">HOLD ▲</button>` +
    `<button class="iobtn" id="mastDown">HOLD ▼</button></div>`;
  list.dataset.built = "1";
  confirmButton($("relayBtn_3"), {
    url: () => "/api/relay/3/" + (relayState[3] ? "off" : "on"),
    confirm: "CONFIRM", pending: "…",
    label: () => updateRelayBtn(3),
  });
  holdButton($("mastUp"), "up");
  holdButton($("mastDown"), "down");
}

// HOLD to move, release to stop — the same contract as the RC stick, and for the same
// reason: `MAST CMD` is an intent that the firmware expires after 250 ms, so a single tap
// would start the lift and immediately stop it. Repeating while held is what keeps it going,
// and letting go (or the browser dying, or the kiosk locking) stops it.
function holdButton(btn, dir) {
  if (!btn) return;
  let timer = null;
  const send = (d) => fetch("/api/mast/" + d, { method: "POST" }).catch(() => {});
  const start = (ev) => {
    ev.preventDefault();
    if (timer) return;
    btn.classList.add("on");
    send(dir);
    timer = setInterval(() => send(dir), 100);
  };
  const stop = () => {
    if (!timer) return;
    clearInterval(timer); timer = null;
    btn.classList.remove("on");
    send("stop");
  };
  btn.addEventListener("pointerdown", start);
  // pointerup/cancel/leave all stop it. A finger sliding off a touch target must not leave
  // the lift travelling.
  ["pointerup", "pointercancel", "pointerleave"].forEach((e) => btn.addEventListener(e, stop));
  window.addEventListener("blur", stop);
}
function updateRelayBtn(i) {
  const b = $("relayBtn_" + i);
  if (!b) return;
  if (relayState[i] == null) { b.textContent = "—"; b.className = "iobtn"; }
  else { b.textContent = relayState[i] ? "TURN OFF" : "TURN ON"; b.className = "iobtn" + (relayState[i] ? " on" : ""); }
}

function renderStamPLC(st) {
  const io = st.io || {};
  const sf = st.safety || {};

  // Safety banner
  const ss = $("safState");
  if (ss) {
    if (!io.fresh) { ss.textContent = "NO DATA"; ss.className = "badge dimbadge"; }
    else if (sf.estopped) { ss.textContent = "E-STOPPED · " + (sf.source || "?"); ss.className = "badge bad"; }
    else if (sf.motion_enabled) { ss.textContent = "MOTION ENABLED"; ss.className = "badge good"; }
    else { ss.textContent = "DISARMED"; ss.className = "badge warn"; }
    setHelp(ss, io.fresh && sf.estopped ? estopCode(sf.source) : null);
  }
  const yn = (v) => v == null ? "—" : v ? "yes" : "no";
  if ($("safContactor")) $("safContactor").textContent = sf.contactor == null ? "—" : sf.contactor ? "energized" : "open";
  if ($("safBtn")) $("safBtn").textContent = sf.estop_button == null ? "—" : sf.estop_button ? "PRESSED" : "clear";

  // Digital inputs
  const di = io.di || [];
  const diLabels = io.di_labels || [];
  const diList = $("diList");
  if (diList) {
    if (!diList.dataset.built || diList.children.length !== diLabels.length) {
      diList.innerHTML = diLabels.map((lbl, i) =>
        `<div class="iorow"><span class="iolabel">IN${i + 1} ${lbl}</span>` +
        `<span class="sdot" id="diDot_${i}"></span><span id="diTxt_${i}">—</span></div>`).join("");
      diList.dataset.built = "1";
    }
    diLabels.forEach((_, i) => {
      const v = di[i];
      const t = $("diTxt_" + i);
      if (t) t.textContent = v == null ? "—" : v ? "HIGH" : "low";
      setDot("diDot_" + i, v == null ? "" : v ? "ok" : "");
    });
  }

  // Relays
  const relay = io.relay || [];
  const relayLabels = io.relay_labels || [];
  if (relayLabels.length) buildRelayRows(relayLabels);
  relayLabels.forEach((_, i) => {
    const v = relay[i];
    relayState[i] = v == null ? null : !!v;
    const t = $("relayTxt_" + i);
    if (t) t.textContent = v == null ? "—" : v ? "CLOSED" : "open";
    setDot("relayDot_" + i, v == null ? "" : v ? "ok" : "");
    if (i === 3) updateRelayBtn(i);
  });

  // "Not ready" banner. While the drive gate is shut nothing will move, and an operator
  // holding the deadman for nothing needs to be told it is the robot settling.
  const nr = $("driveNotReady");
  if (nr) {
    const d0 = st.drive || {};
    const waiting = d0.drive_safe === false;
    nr.hidden = !waiting;
    // Text in its own span, so the "?" beside it survives the update.
    let t = nr.querySelector(".nr-txt");
    if (!t) { nr.textContent = ""; t = document.createElement("span"); t.className = "nr-txt"; nr.appendChild(t); }
    t.textContent = waiting ? "ROBOT NOT READY — settling, motion is disabled" : "";
    setHelp(t, waiting ? "DRIVE_NOT_READY" : null);
  }

  // Z-axis readout: measured height and the pinch guard's state. "no sensor" is shown
  // rather than hidden — it means descent is UNGUARDED, which the operator should know.
  const mt = $("mastTxt");
  if (mt) {
    const d = st.drive || {};
    const h = d.mast_h_mm;
    const pinch = d.pinch;
    const moving = (d.mast || "").endsWith(":1") ? " ▲" : (d.mast || "").endsWith(":2") ? " ▼" : "";
    let txt = (h == null || h < 0) ? "height —" : Math.round(h) + " mm";
    if (pinch === "HOLD") txt += " · PINCH HOLD";
    else if (pinch === "nosensor") txt += " · NO SENSOR (unguarded)";
    mt.textContent = txt + moving;
    mt.className = pinch === "HOLD" ? "warn" : (pinch === "nosensor" ? "bad" : "");
  }

  // Sensors
  if ($("stBusV")) $("stBusV").textContent = io.bus_v != null ? io.bus_v.toFixed(1) + " V" : "—";
  if ($("stTemp")) $("stTemp").textContent = io.temp_c != null ? io.temp_c.toFixed(1) + " °C" : "—";
  if ($("stIsock")) $("stIsock").textContent = io.isocket_a != null ? io.isocket_a.toFixed(3) + " A" : "—";

  // I²C bus: prefer the on-demand full sweep, fall back to the periodic probe.
  renderI2C(io.i2c_full && io.i2c_full.length ? io.i2c_full : io.i2c, !!io.i2c_full, io.i2c_bus);
}

// Known devices on Port A (Grove RED, external I2C — G2/G1). The internal bus
// (G13/G15) is separate and its chips should NOT appear in a Port A scan.
const I2C_KNOWN = {
  "0x25": "8Servo (M5)",
  "0x38": "PRISM RGB driver",
  "0x45": "EXT.IO2 (M5)",
  "0x59": "ExtEncoder (M5)",
  "0x31": "Pinpoint (M5)",
  // Internal-bus chips (flag if they appear on Port A — would mean wrong bus):
  "0x32": "RX8130 RTC ⚠ internal bus",
  "0x40": "INA226 ⚠ internal bus",
  "0x43": "PI4IOE5V6408 ⚠ internal bus",
  "0x48": "LM75B ⚠ internal bus",
};
function renderI2C(addrs, isFull, bus) {
  const list = $("i2cList");
  if (!list) return;
  if (!addrs || !addrs.length) { list.innerHTML = '<span class="i2c-empty">no devices</span>'; }
  else {
    list.innerHTML = addrs.map((a) => {
      const lbl = I2C_KNOWN[a] || "unknown";
      const isPrism = a === "0x38";
      const cls = "i2c-chip" + (isPrism ? " prism" : I2C_KNOWN[a] ? "" : " unknown");
      return `<span class="${cls}"><b>${a}</b> ${lbl}</span>`;
    }).join("");
  }
  const note = $("i2cNote");
  if (note) {
    const busTxt = bus ? `Port A (SDA G${bus.sda} · SCL G${bus.scl})` : "Port A";
    const hasPrism = addrs && addrs.includes("0x38");
    note.textContent = isFull
      ? `${busTxt} · ${hasPrism ? "✓ PRISM (0x38) detected" : "PRISM (0x38) NOT detected"} · full sweep`
      : `${busTxt} · periodic probe — tap SCAN for full sweep`;
    note.className = "sub" + (isFull ? (hasPrism ? " ok" : " bad") : "");
  }
}

/* ---------- MAST: ExtEncoder + limit switches ---------- */
function renderMast(st) {
  const enc = st.encoder || {};
  const io = st.io || {};
  const es = $("mastEncState");
  if (es) {
    if (enc.present) { es.textContent = "PRESENT"; es.className = "badge good"; }
    else { es.textContent = "NO ENCODER"; es.className = "badge dimbadge"; }
  }
  if ($("mastCount")) $("mastCount").textContent = enc.count != null ? enc.count.toLocaleString() : "—";

  const di = io.di || [];
  const lim = (idx, txtId, dotId) => {
    const v = di[idx];
    if ($(txtId)) $(txtId).textContent = v == null ? "—" : v ? "TRIGGERED" : "clear";
    setDot(dotId, v == null ? "" : v ? "warn" : "ok");
  };
  lim(1, "mastTop", "mastTopDot");   // DI1 = MAST LIMIT TOP
  lim(2, "mastBot", "mastBotDot");   // DI2 = MAST LIMIT BOT
}

/* ---------- LIDAR (rosbridge, via the dashboard's /rosbridge — live /scan front + /scan_rear rear) ---------- */
(function lidarModule() {
  // rosbridge is ALWAYS reached through the dashboard's own origin, over HTTP and HTTPS alike: an HTTPS
  // page may not open ws://, and the proxy refuses a handshake from another page's origin
  // (origin_guard), where rosbridge's own :9090 accepts any.
  const ROS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host || "localhost:8080"}/rosbridge`;
  let ws = null, wsReady = false;
  let frontScan = null, frontStamp = 0, frontRate = 0, lastMsgT = 0;
  let rearScan = null, rearStamp = 0, rearRate = 0, lastRearT = 0;
  let frontYaw = parseFloat(localStorage.getItem("ly_f2") ?? "180");   // mounting-yaw correction, degrees
  let rearYaw = parseFloat(localStorage.getItem("ly_r2") ?? "180");

  function setRosBadge(state) {
    const b = $("rosConn");
    if (!b) return;
    b.textContent = "ROSBRIDGE " + state;
    b.className = "badge " + (state === "UP" ? "good" : state === "…" ? "warn" : "dimbadge");
  }
  function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
  function callService(service, args) { send({ op: "call_service", service, args: args || {} }); }

  function rosConnect() {
    setRosBadge("…");
    try { ws = new WebSocket(ROS_URL); } catch (e) { setTimeout(rosConnect, 2500); return; }
    ws.onopen = () => {
      wsReady = true; setRosBadge("UP");
      send({ op: "subscribe", topic: "/scan", type: "sensor_msgs/msg/LaserScan",
             throttle_rate: 100, queue_length: 1 });
      send({ op: "subscribe", topic: "/scan_rear", type: "sensor_msgs/msg/LaserScan",
             throttle_rate: 100, queue_length: 1 });
      // /joint_states for the TELEOP page's arm view. Throttled hard on purpose: the arm
      // controllers publish at 400 Hz and a display needs ~10, so the rate is asked for at the
      // SUBSCRIPTION rather than by adding a decimating node — one publisher, many subscribers,
      // each taking what it can use.
      send({ op: "subscribe", topic: "/joint_states", type: "sensor_msgs/msg/JointState",
             throttle_rate: 100, queue_length: 1 });
    };
    ws.onclose = () => { wsReady = false; setRosBadge("DOWN"); setTimeout(rosConnect, 2500); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.op === "publish" && m.topic === "/scan") onScan(m.msg);
      else if (m.op === "publish" && m.topic === "/scan_rear") onRearScan(m.msg);
      // Handed to the teleop page's module, which may not be loaded yet — hence the optional call
      // rather than a hard reference.
      else if (m.op === "publish" && m.topic === "/joint_states") window.onJointStates?.(m.msg);
    };
  }

  function onScan(msg) {
    frontScan = msg;
    const now = performance.now();
    if (lastMsgT) { const dt = (now - lastMsgT) / 1000; if (dt > 0) frontRate = 0.8 * frontRate + 0.2 / dt; }
    lastMsgT = now; frontStamp = now;
  }
  function onRearScan(msg) {
    rearScan = msg;
    const now = performance.now();
    if (lastRearT) { const dt = (now - lastRearT) / 1000; if (dt > 0) rearRate = 0.8 * rearRate + 0.2 / dt; }
    lastRearT = now; rearStamp = now;
  }

  // Each LIDAR sees only its outward 180° (the robot body occludes the inward
  // half). Sensor forward (angle 0) → outward screen direction: front opens LEFT
  // (π), rear opens RIGHT (0). Origin sits on the robot-facing (center) edge.
  // Tweak FACING / the crop when the real mounts are known.
  const FRONT_FACING = Math.PI, REAR_FACING = 0;
  // Scale the disc to the *useful* range, not the A1's 12 m best-case spec (which
  // is unreliable near the top end). Rings/scale reference 6 m; returns beyond 6 m
  // still plot — dimmed — running outside the disc into the panel's outer space.
  const DISC_RANGE = 3;   // disc edge = 3 m (more resolution up close; >3 m plots dimmed outside)

  function fitCanvas(cv) {
    const w = cv.clientWidth | 0, h = cv.clientHeight | 0;
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return [w, h];
  }

  // Half-disc render: origin on the center-facing edge, arc bulging toward `facing`,
  // points cropped to the outward 180° (cos(sensorAngle) > 0).
  function drawHalfScan(cv, ranges, amin, ainc, rmax, facing, color, yawDeg) {
    const yaw = (yawDeg || 0) * Math.PI / 180;   // mounting-yaw correction
    if (!cv) return;
    const [w, h] = fitCanvas(cv);
    const ctx = cv.getContext("2d");
    if (!ctx || w === 0) return;
    ctx.clearRect(0, 0, w, h);
    const pad = 16;
    const ox = w / 2 - Math.cos(facing) * (w / 2 - pad);   // origin on robot-facing edge
    const oy = h / 2;
    const R = Math.max(20, Math.min(w - 2 * pad, h / 2 - pad));
    const scale = R / DISC_RANGE;     // disc edge = 6 m (useful range)
    const a0 = -facing - Math.PI / 2, a1 = -facing + Math.PI / 2;  // canvas y-down → negate

    // half-circle range rings + labels along the heading (1/3/6 m)
    ctx.strokeStyle = "rgba(120,150,180,0.18)"; ctx.fillStyle = "rgba(150,170,190,0.55)";
    ctx.font = "10px monospace"; ctx.textAlign = "center";
    [1, 2, DISC_RANGE].forEach((m) => {
      ctx.beginPath(); ctx.arc(ox, oy, m * scale, a0, a1); ctx.stroke();
      ctx.fillText(m + "m", ox + Math.cos(facing) * (m * scale - 9), oy - Math.sin(facing) * (m * scale) + 4);
    });
    // robot-facing flat edge (diameter) + forward heading line
    ctx.strokeStyle = "rgba(120,150,180,0.28)";
    ctx.beginPath(); ctx.moveTo(ox, oy - R); ctx.lineTo(ox, oy + R); ctx.stroke();
    ctx.strokeStyle = "rgba(80,200,255,0.5)";
    ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + Math.cos(facing) * R, oy - Math.sin(facing) * R); ctx.stroke();

    // points — crop to the outward 180°
    ctx.fillStyle = color;
    if (ranges) {
      for (let i = 0; i < ranges.length; i++) {
        const r = ranges[i];
        if (!(r > 0) || !isFinite(r) || r > rmax) continue;   // null/inf/0 skipped
        const sa = amin + i * ainc + yaw;      // sensor angle + mounting-yaw correction
        if (Math.cos(sa) <= 0) continue;       // skip robot-occluded inward half
        const a = sa + facing;
        ctx.globalAlpha = r > DISC_RANGE ? 0.3 : 1;   // dim marginal (>6 m) returns
        ctx.fillRect(ox + Math.cos(a) * r * scale - 1, oy - Math.sin(a) * r * scale - 1, 2, 2);
      }
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = "#ffd24a"; ctx.beginPath(); ctx.arc(ox, oy, 3.5, 0, Math.PI * 2); ctx.fill();
  }

  function fmtInfo(scan, rate) {
    let valid = 0;
    for (const r of scan.ranges) if (r > 0 && isFinite(r) && r < scan.range_max) valid++;
    return `${rate.toFixed(1)} Hz · ${valid}/${scan.ranges.length} pts · ${scan.range_max.toFixed(0)}m`;
  }
  function lidarActive() { const v = $("view-lidar"); return v && v.classList.contains("active"); }

  // Event-driven, low-CPU: the front canvas repaints ONLY when the scan (or its
  // staleness) actually changes — capped at 5 Hz. The rear is a static "not
  // installed" placeholder (the live mock was a needless CPU hog on this
  // software-rendered kiosk). Near-zero CPU when idle / off-tab.
  let lastDraw = 0, lastDrawnStamp = -1, lastRearDrawnStamp = -1;
  const DRAW_MS = 200;
  // One live half-disc per lidar (front /scan, rear /scan_rear). Repaints only when
  // the scan (or its staleness) changes — capped at 5 Hz — so it's near-zero CPU idle.
  function drawSide(canvasId, stateId, infoId, scan, stamp, rate, facing, prevStamp, yaw) {
    const st = $(stateId), info = $(infoId);
    const fresh = scan && (now - stamp) < 1500;
    const s = fresh ? stamp : 0;
    if (s === prevStamp) return prevStamp;
    if (fresh) {
      drawHalfScan($(canvasId), scan.ranges, scan.angle_min, scan.angle_increment,
                   scan.range_max || 12, facing, "#46c878", yaw);
      if (st) { st.textContent = "LIVE"; st.className = "badge good"; }
      if (info) info.textContent = fmtInfo(scan, rate);
    } else {
      drawHalfScan($(canvasId), null, 0, 0, 12, facing, "#46c878", yaw);
      if (st) { st.textContent = wsReady ? "NO SCAN" : "NO ROSBRIDGE"; st.className = "badge bad"; }
      if (info) info.textContent = wsReady ? "motor stopped?" : "rosbridge down";
    }
    return s;
  }
  let now = 0;
  function render(ts) {
    requestAnimationFrame(render);
    if (!lidarActive()) { lastDrawnStamp = -1; lastRearDrawnStamp = -1; return; }
    if (ts - lastDraw < DRAW_MS) return;
    lastDraw = now = ts;
    lastDrawnStamp = drawSide("lidarFront", "lidarFrontState", "lidarFrontInfo",
                              frontScan, frontStamp, frontRate, FRONT_FACING, lastDrawnStamp, frontYaw);
    lastRearDrawnStamp = drawSide("lidarRear", "lidarRearState", "lidarRearInfo",
                                  rearScan, rearStamp, rearRate, REAR_FACING, lastRearDrawnStamp, rearYaw);
  }

  // Route motor control through the watchdog's /lidar/enable (std_srvs/SetBool) so
  // it tracks desired state and won't "rescue" an intentional STOP back to spinning.
  const sb = $("btnLidarStart"), tb = $("btnLidarStop");
  if (sb) sb.addEventListener("click", () => { callService("/lidar/enable", { data: true }); sb.textContent = "STARTING…"; setTimeout(() => (sb.textContent = "START SPIN"), 1200); });
  if (tb) tb.addEventListener("click", () => { callService("/lidar/enable", { data: false }); tb.textContent = "STOPPING…"; setTimeout(() => (tb.textContent = "STOP SPIN"), 1200); });

  rosConnect();
  requestAnimationFrame(render);
})();

/* ---------- POWER: 52V pack + usage projection + 24V rails + charger ---------- */
function fmtDur(sec) {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}
function renderPower(st) {
  const p = st.power || {};
  const pk = p.pack || {};
  const ps = $("pkState");
  if (ps) {
    if (!pk.present) { ps.textContent = "NO DATA"; ps.className = "badge dimbadge"; }
    else { ps.textContent = pk.charging ? "CHARGING" : pk.discharging ? "DISCHARGING" : "IDLE";
           ps.className = "badge " + (pk.charging ? "good" : "dimbadge"); }
  }
  const ring = $("pkRing");
  if (ring) ring.style.setProperty("--p", pk.soc != null ? clamp(pk.soc, 0, 100) : 0);
  if ($("pkSoc")) $("pkSoc").innerHTML = (pk.soc != null ? Math.round(pk.soc) : "—") + "<small>%</small>";
  if ($("pkV")) $("pkV").textContent = pk.voltage != null ? pk.voltage.toFixed(1) + " V" : "— V";
  if ($("pkI")) {
    const i = pk.current_a;
    $("pkI").textContent = i != null ? (i >= 0 ? "+" : "") + i.toFixed(1) + " A" : "— A";
    $("pkI").className = "num " + (i == null ? "" : i >= 0 ? "good" : "");
  }
  if ($("pkCellTxt")) $("pkCellTxt").textContent =
    (pk.cell_min != null && pk.cell_max != null)
      ? `${pk.cell_min.toFixed(2)}–${pk.cell_max.toFixed(2)}V · Δ${Math.round((pk.cell_delta || 0) * 1000)}mV` : "—";
  const cells = $("pkCells");
  if (cells) {
    const arr = pk.cells || [];
    if (cells.children.length !== 16) cells.innerHTML = Array.from({ length: 16 }, () => "<i></i>").join("");
    // Only flag the low cells amber when the pack is actually IMBALANCED (>50 mV spread).
    // A healthy LiFePO4 pack sits within a few tens of mV, so always-marking the minimum
    // read as a false warning. Below the threshold every in-range cell shows green.
    const CELL_IMBALANCE_V = 0.050;
    const delta = (pk.cell_delta != null) ? pk.cell_delta
                : (pk.cell_max != null && pk.cell_min != null) ? pk.cell_max - pk.cell_min : 0;
    const imbalanced = delta > CELL_IMBALANCE_V;
    for (let i = 0; i < 16; i++) {
      const el = cells.children[i], v = arr[i];
      if (v == null) { el.style.height = "12%"; el.className = "off"; continue; }
      el.style.height = clamp((v - 3.0) / 0.5 * 85 + 15, 12, 100) + "%";
      el.className = (imbalanced && pk.cell_min != null && v <= pk.cell_min + 0.005) ? "lo" : "";
    }
  }
  const badges = $("pkBadges");
  if (badges) {
    const b = [];
    if (pk.present) {
      b.push(pk.flags ? `<span class="badge bad">PROTECT 0x${pk.flags.toString(16)}</span>` : `<span class="badge good">PROTECTION OK</span>`);
      if (pk.charging) b.push(`<span class="badge good">CHARGING</span>`);
      if (pk.temp_max != null) b.push(`<span class="badge dimbadge">${Math.round(pk.temp_max)}°C</span>`);
    } else b.push(`<span class="badge dimbadge">awaiting BMS (RS485)</span>`);
    badges.innerHTML = b.join(" ");
  }
  // usage / projection
  const u = pk.usage || {};
  if ($("usEtaLbl")) $("usEtaLbl").textContent = u.eta === "full" ? "EST. TO FULL" : u.eta === "empty" ? "EST. TO EMPTY" : "EST. REMAINING";
  if ($("usTime")) { $("usTime").textContent = fmtDur(u.time_remaining_s); $("usTime").className = "proj-big " + (u.eta === "empty" ? "warn" : "good"); }
  if ($("usRate")) $("usRate").textContent = u.rate_pct_per_hr != null ? (u.rate_pct_per_hr >= 0 ? "+" : "") + u.rate_pct_per_hr.toFixed(1) + " %/hr" : "—";
  // Live power draw (signed: + into the pack / - out). Prefer the instantaneous pack power.
  const pw = pk.power_w != null ? pk.power_w : u.power_w;
  if ($("usPower")) {
    $("usPower").textContent = pw == null ? "—" : Math.abs(pw) < 1 ? "0 W" : Math.round(Math.abs(pw)) + " W " + (pw >= 0 ? "in" : "out");
    $("usPower").className = "num " + (pw == null ? "" : pw >= 0 ? "good" : "");
  }
  if ($("usFootL")) $("usFootL").textContent = "SoC · last " + fmtDur(u.span_s || 0);
  // Energy over the window: consumed (discharge) vs delivered (charge).
  if ($("usFootR")) $("usFootR").textContent =
    (u.wh_out != null ? u.wh_out.toFixed(1) + " Wh used" : "—") +
    (u.wh_in ? " · " + u.wh_in.toFixed(1) + " Wh in" : "");
  drawSpark($("usSpark"), u.points || []);
  // 24V rails
  const lr = p.logic_rail || {}, dr = p.drive_rail || {};
  if ($("rlLogic")) $("rlLogic").textContent = lr.voltage != null ? lr.voltage.toFixed(1) + " V" + (lr.current_a != null ? " · " + lr.current_a.toFixed(1) + " A" : "") : "— V";
  if ($("rlDriveV")) $("rlDriveV").textContent = dr.voltage != null ? dr.voltage.toFixed(1) + " V" : "— V";
  const badge = (id, on, onTxt, offTxt, onCls) => { const e = $(id); if (!e) return; if (on == null) { e.textContent = "—"; e.className = "badge dimbadge"; } else { e.textContent = on ? onTxt : offTxt; e.className = "badge " + (on ? (onCls || "good") : "dimbadge"); } };
  badge("rlContactor", dr.contactor, "CLOSED", "OPEN", "good");
  badge("rlClamp", dr.clamp_active, "CLAMPING", "clamp idle", "warn");
  const cg = p.charger || {};
  const cc = $("chConn");
  if (cc) {
    if (cg.charging == null) { cc.textContent = "—"; cc.className = "badge dimbadge"; }
    else { cc.textContent = cg.charging ? "CHARGING" : "IDLE"; cc.className = "badge " + (cg.charging ? "good" : "dimbadge"); }
  }
  const cih = $("chInhibit");
  if (cih) cih.textContent = cg.charging == null ? "—" : cg.charging ? "drive inhibited while charging" : "no charge current — drive permitted";
}

function drawSpark(cv, pts) {
  if (!cv) return;
  const w = cv.clientWidth | 0, h = cv.clientHeight | 0;
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  if (!pts || pts.length < 2) return;
  const socs = pts.map((p) => p.soc), ts = pts.map((p) => p.t);
  let lo = Math.min(...socs), hi = Math.max(...socs);
  const pad = Math.max(2, (hi - lo) * 0.2); lo -= pad; hi += pad;
  if (hi - lo < 4) hi = lo + 4;
  const t0 = ts[0], t1 = ts[ts.length - 1] || 1;
  const X = (t) => ((t - t0) / Math.max(t1 - t0, 1)) * w;
  const Y = (s) => h - ((s - lo) / (hi - lo)) * (h - 2) - 1;
  ctx.beginPath(); ctx.moveTo(X(ts[0]), Y(socs[0]));
  for (let i = 1; i < pts.length; i++) ctx.lineTo(X(ts[i]), Y(socs[i]));
  ctx.lineTo(X(ts[ts.length - 1]), h); ctx.lineTo(X(ts[0]), h); ctx.closePath();
  ctx.fillStyle = "rgba(70,214,138,0.16)"; ctx.fill();
  ctx.beginPath(); ctx.moveTo(X(ts[0]), Y(socs[0]));
  for (let i = 1; i < pts.length; i++) ctx.lineTo(X(ts[i]), Y(socs[i]));
  ctx.strokeStyle = "#46d68a"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
}

/* ---------- THERMAL: enclosure zones (ENV+fans) + free component temps ---------- */
function renderThermal(st) {
  const th = st.thermal || {};
  const zones = th.zones || [];
  const ts = $("thState");
  if (ts) {
    if (!th.fresh) { ts.textContent = "NO DATA"; ts.className = "badge dimbadge"; }
    else if (th.overtemp) { ts.textContent = "OVERTEMP"; ts.className = "badge bad"; }
    else { ts.textContent = "OK"; ts.className = "badge good"; }
  }
  const zg = $("thZones");
  if (zg) {
    if (!zones.length) { zg.innerHTML = '<div class="sub dim">awaiting ENV sensors (PaHub)</div>'; }
    else zg.innerHTML = zones.map((z) => {
      const t = z.temp_c, cls = t == null ? "" : t >= 70 ? "bad" : t >= 45 ? "warn" : "good";
      const fan = z.fan_on ? '<span class="fanb on">FAN ON</span>' : '<span class="fanb off">fan off</span>';
      return `<div class="zone ${cls}"><div class="zn-top"><span class="lbl">${z.label || ("ZONE " + z.zone)}</span>${fan}</div>` +
        `<div class="zn-t ${cls}">${t != null ? t.toFixed(1) + "°" : "—"}</div>` +
        `<div class="lbl dim">${z.humidity != null ? Math.round(z.humidity) + "% RH" : ""}</div></div>`;
    }).join("");
  }
  const comp = [];
  const sys = lastSystem || {};
  Object.entries(sys.temps_c || {}).sort((a, b) => b[1] - a[1]).slice(0, 6)
    .forEach(([k, v]) => comp.push([("THOR " + k).toUpperCase(), v]));
  if (th.board_c != null) comp.push(["STAMPLC BD", th.board_c]);
  const pk = (st.power && st.power.pack) || {};
  if (pk.temp_max != null) comp.push(["BMS PACK", pk.temp_max]);
  const cg = $("thComp");
  if (cg) {
    if (!comp.length) { cg.innerHTML = '<div class="sub dim">no component temps yet</div>'; }
    else cg.innerHTML = comp.map(([n, v]) => {
      const cls = v >= 80 ? "c" : v >= 65 ? "w" : "g";
      return `<div class="tchip ${cls}"><span class="cn">${n}</span><span class="cv">${Math.round(v)}°</span></div>`;
    }).join("");
  }
}

/* ---------- DRIVE: mode selector highlight + per-wheel command ---------- */
const DRIVE_MODE_LABEL = { MECANUM: "MECANUM", SKID4: "4WD SKID", DIFF2: "2WD DIFF" };
function renderDrive(st) {
  const d = st.drive || {};
  const mode = d.mode;
  const mec = $("modeMecanum"), fwd = $("mode4wd");
  if (mec) mec.classList.toggle("on", mode === "MECANUM");
  if (fwd) fwd.classList.toggle("on", mode === "SKID4" || mode === "DIFF2");
  const da = $("drArmed");
  if (da) { da.textContent = d.armed == null ? "—" : d.armed ? "ARMED" : "DISARMED"; da.className = "badge " + (d.armed == null ? "dimbadge" : d.armed ? "good" : "warn"); }
  setChip("inArmed", d.armed == null ? "—" : d.armed ? "YES" : "NO", d.armed ? "ok" : "dim");
  setChip("inDriveMode", mode ? (DRIVE_MODE_LABEL[mode] || mode) : "—", mode ? "" : "dim");
  const w = d.wheels || [];
  [["whTL", 0], ["whTR", 1], ["whBL", 2], ["whBR", 3]].forEach(([id, i]) => {
    const el = $(id); if (!el) return;
    const v = w[i], bar = el.querySelector("i"), txt = el.querySelector("span");
    if (v == null) { if (bar) bar.style.height = "0"; if (txt) txt.textContent = "—"; el.classList.remove("fwd", "rev"); return; }
    const mag = Math.min(Math.abs(v), 1);
    if (bar) {   // vertical fill from center: UP = forward, DOWN = reverse
      bar.style.height = (mag * 50) + "%";
      if (v >= 0) { bar.style.bottom = "50%"; bar.style.top = "auto"; }
      else { bar.style.top = "50%"; bar.style.bottom = "auto"; }
    }
    if (txt) txt.textContent = Math.round(v * 100) + "%";
    el.classList.toggle("rev", v < 0); el.classList.toggle("fwd", v >= 0);
  });

  // ---- movement intent viz: translation vector + rotation + turn-signal ----
  const iFwd = d.fwd, iStr = d.str, iRot = d.rot, iTurn = d.turn;
  const iv = $("diVec"), ih = $("diHead");
  if (iv && ih) {
    const cx = 120, cy = 120, sc = 66;
    const ex = cx + (iStr || 0) * sc, ey = cy - (iFwd || 0) * sc;   // fwd = up, strafe-right = +x
    const mag = Math.hypot(iFwd || 0, iStr || 0);
    iv.setAttribute("x2", ex); iv.setAttribute("y2", ey);
    const ang = Math.atan2(ey - cy, ex - cx) * 180 / Math.PI;
    ih.setAttribute("transform", `translate(${ex},${ey}) rotate(${ang})`);
    ih.style.opacity = mag > 0.05 ? 1 : 0;
    iv.style.opacity = mag > 0.05 ? 1 : 0.15;
  }
  const rcw = $("diRotCW"), rccw = $("diRotCCW");   // rot>0 = rotate right (CW)
  if (rcw && rccw) {
    const rr = iRot || 0;
    rcw.style.opacity = rr > 0.05 ? Math.min(1, 0.3 + Math.abs(rr)) : 0.08;
    rccw.style.opacity = rr < -0.05 ? Math.min(1, 0.3 + Math.abs(rr)) : 0.08;
  }
  const tl = $("diTurnL"), tr = $("diTurnR");
  if (tl) tl.classList.toggle("on", iTurn === "L");
  if (tr) tr.classList.toggle("on", iTurn === "R");
  const tb = $("diTurn");
  if (tb) { tb.textContent = iTurn === "L" ? "◀ LEFT" : iTurn === "R" ? "RIGHT ▶" : "—";
    tb.className = "badge " + (iTurn === "L" || iTurn === "R" ? "good" : "dimbadge"); }
  const sp = $("diSpeed");   // annunciate the CH8 speed knob — 0% scales all output to zero
  if (sp) {
    const s = d.spd;
    if (s == null) { sp.textContent = "SPD —"; sp.className = "badge dimbadge"; }
    else { const pct = Math.round(s * 100);
      sp.textContent = "SPD " + pct + "%";
      sp.className = "badge " + (pct === 0 ? "bad" : pct < 15 ? "warn" : "good"); }
  }
  const f2 = (v) => v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(2);
  if ($("diFwd")) $("diFwd").textContent = f2(iFwd);
  if ($("diStr")) $("diStr").textContent = f2(iStr);
  if ($("diRotN")) $("diRotN").textContent = f2(iRot);
}

/* ---------- Websocket with auto-reconnect ---------- */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const sock = new WebSocket(`${proto}://${location.host}/ws`);
  sock.onopen = () => $("conn").className = "conn up";
  sock.onclose = () => {
    $("conn").className = "conn down";
    setTimeout(connect, 1500);
  };
  sock.onerror = () => sock.close();
  const safe = (fn, arg) => { try { fn(arg); } catch (e) {} };
  sock.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.system) { safe(renderRobotName, d.system.host); safe(renderSystem, d.system); }
    if (d.status) {
      const s = d.status;
      safe(renderRobotState, s.robot_state); safe(renderStatus, s); safe(renderStamPLC, s);
      safe(renderMast, s); safe(renderPower, s); safe(renderThermal, s); safe(renderDrive, s);
    }
    if (d.telemetry) { safe(renderRC, d.telemetry); safe(renderNav, d.telemetry); }
    if (d.state) safe(renderServerState, d.state);
  };
}

// Which robot this is — with several robots and tablets, every screen must say which one it drives.
// Its own handler, so the name never waits on the rest of the system metrics rendering cleanly.
function renderRobotName(host) {
  const n = host && host.name;
  if (!n || $("robotName").textContent === n.toUpperCase()) return;
  $("robotName").textContent = n.toUpperCase();
  document.title = `${n} · OpenDubs dashboard`;
}

function renderServerState(st) {
  const btn = $("btnAiToggle");
  if (!btn) return;
  
  if (st.ai_tracking) {
    btn.textContent = "AI TRACKING: ON";
    btn.classList.remove("off");
  } else {
    btn.textContent = "AI TRACKING: OFF";
    btn.classList.add("off");
  }
}

connect();
