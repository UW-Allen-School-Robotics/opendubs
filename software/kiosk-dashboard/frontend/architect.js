/* ARCHITECT tab: the demo day's state, the program ARCHITECT wrote, what the Jabra listener hears, and the Orbbec's image.
   Read-only, from /api/architect/ (backend/architect_proxy.py), and only while the tab is on screen:
   nothing is fetched while another view is up or the page is hidden, and the camera feed itself
   subscribes only while frames are asked for. The program is shown as ssa's CLI shows it (a numbered,
   highlighted panel), with the changes from the previous program when a correction replaces it. */
import { ArmView } from "./armview.js?v=39c54689";   // the teleop tab's arm view (the Arms lane's), reused as is

(function architectView() {
  const $ = (id) => document.getElementById(id);
  const shown = () => { const v = $("view-architect"); return !!v && v.classList.contains("active") && !document.hidden; };

  /* ---------- camera: one frame at a time, about two a second ---------- */
  let camBusy = false, camUrl = null;
  async function camTick() {
    if (!shown() || camBusy) return;
    camBusy = true;
    try {
      const r = await fetch("/api/architect/frame.jpg?t=" + Date.now(), { cache: "no-store" });
      if (r.ok) {
        const url = URL.createObjectURL(await r.blob());
        $("archCam").src = url;
        if (camUrl) URL.revokeObjectURL(camUrl);
        camUrl = url;
        badge("archCamBadge", "LIVE", "good");
        $("archCamNote").textContent = "Orbbec colour camera, 640 wide, about 2 frames a second.";
      } else {
        const why = (await r.json().catch(() => ({}))).error || "no camera";
        badge("archCamBadge", why.startsWith("waiting") ? "STARTING" : "OFF", why.startsWith("waiting") ? "warn" : "dimbadge");
        $("archCamNote").textContent = why.startsWith("waiting") ? "Waiting for the camera's first frame." :
          "The camera feed is not running (software/architect/tools/dev.sh camera).";
      }
    } catch (e) {
      badge("archCamBadge", "OFF", "dimbadge");
    } finally { camBusy = false; }
  }

  /* ---------- program ---------- */
  const KW = new Set("False None True and as assert break class continue def del elif else except finally for from global if import in is lambda not or pass raise return try while with yield".split(" "));
  function highlight(line) {
    // comments, strings, numbers, keywords and calls, as spans; everything else plain text
    const out = [];
    const re = /(#.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*\b)(?=\s*\()|(\b[A-Za-z_]\w*\b)/g;
    let last = 0, m;
    while ((m = re.exec(line))) {
      if (m.index > last) out.push([null, line.slice(last, m.index)]);
      const cls = m[1] ? "c" : m[2] ? "s" : m[3] ? "n" : m[4] ? (KW.has(m[4]) ? "k" : "f") : (KW.has(m[5]) ? "k" : null);
      out.push([cls, m[0]]);
      last = re.lastIndex;
    }
    if (last < line.length) out.push([null, line.slice(last)]);
    return out;
  }
  function renderCode(el, code) {
    el.replaceChildren();
    code.replace(/\n$/, "").split("\n").forEach((line, i) => {
      const row = document.createElement("div"); row.className = "arch-line";
      const no = document.createElement("span"); no.className = "arch-no"; no.textContent = String(i + 1);
      const tx = document.createElement("span"); tx.className = "arch-tx";
      for (const [cls, text] of highlight(line)) {
        const s = document.createElement("span"); if (cls) s.className = "arch-" + cls; s.textContent = text; tx.append(s);
      }
      row.append(no, tx); el.append(row);
    });
  }
  function lineDiff(a, b) {
    // the longest common subsequence of lines; "-" removed, "+" added, " " kept
    const A = a.split("\n"), B = b.split("\n"), n = A.length, m = B.length;
    const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = []; let i = 0, j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) { out.push([" ", A[i]]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push(["-", A[i++]]);
      else out.push(["+", B[j++]]);
    }
    while (i < n) out.push(["-", A[i++]]);
    while (j < m) out.push(["+", B[j++]]);
    return out;
  }
  let progSha = null, progCode = null;
  function showProgram(p, from) {
    if (!p || !p.code) {
      if (from === "none") badge("archProgBadge", "NO PROGRAM", "dimbadge");   // else the runner's state stays
      $("archSummary").textContent = from === "none" ?
        "No ARCHITECT program: neither the runner nor a show's program is up." : "No program yet.";
      $("archMeta").textContent = "";
      if (progSha !== null) { $("archCode").replaceChildren(); progSha = null; }
      return;
    }
    $("archSummary").textContent = p.summary || "";
    $("archMeta").textContent = p.meta || "";
    if (p.sha === progSha) return;
    if (progCode !== null && progCode !== p.code) {
      const d = lineDiff(progCode, p.code), el = $("archDiff");
      el.replaceChildren();
      for (const [k, line] of d) {
        if (k === " ") continue;
        const row = document.createElement("div"); row.className = k === "+" ? "arch-add" : "arch-del";
        row.textContent = k + " " + line; el.append(row);
      }
      $("archDiffBox").hidden = false;
      $("archDiffNote").textContent = "Changes from " + (progSha || "the previous program") + " to " + p.sha;
    }
    renderCode($("archCode"), p.code);
    progSha = p.sha; progCode = p.code;
  }
  let progBusy = false;
  async function progTick() {
    if (!shown() || progBusy) return;
    progBusy = true;
    try {
      const r = await fetch("/api/architect/runner", { cache: "no-store" });
      if (r.ok) {
        const s = await r.json();
        badge("archProgBadge", (s.state || "?").replace("_", " "), s.approved ? "good" : s.state === "CHECK_FAILED" ? "bad" : "warn");
        showProgram(s.code ? { code: s.code, summary: s.summary, sha: s.sha,
          meta: (s.approved ? "approved, frozen as " : "awaiting a spoken yes: ") + s.sha + (s.runs ? " · " + s.runs + " runs" : "") +
                (s.last ? " · last run " + s.last.result : "") } : null, "runner");
        return;
      }
      if (lastListener && lastListener.program) {
        const p = lastListener.program, st = lastListener.status || {};
        badge("archProgBadge", "SHOW " + (st.show || ""), "good");
        showProgram({ code: p.code, summary: p.summary, sha: p.sha,
          meta: "Recorded as the show " + (st.show || "?") + ", which “trick or treat” plays (" + String(p.path).replace(/^\/work\//, "") + ")" }, "show");
        return;
      }
      showProgram(null, "none");
    } catch (e) { showProgram(null, "none"); }
    finally { progBusy = false; }
  }

  /* ---------- the demo day's coordinator (software/architect/opendubs_architect/demo_day.py) ---------- */
  const DAY_NAMES = { IDLE_NAV: "PATROL", APPROACH: "APPROACH", ENGAGED: "WITH A VISITOR", CANDY: "CANDY", CHOREO: "SHOW", REST: "ARMS TO REST", PAUSED: "PAUSED" };
  let dayBusy = false;
  function secs(s) { return s >= 60 ? Math.floor(s / 60) + " min " + Math.round(s % 60) + " s" : Math.round(s) + " s"; }
  async function dayTick() {
    if (!shown() || dayBusy) return;
    dayBusy = true;
    const steps = document.querySelectorAll("[data-day-state]");
    try {
      const r = await fetch("/api/architect/day", { cache: "no-store" });
      if (!r.ok) {
        badge("archDayBadge", "not running", "dimbadge");
        steps.forEach((el) => el.classList.remove("on", "paused"));
        $("archDayWhy").textContent = "The demo day's coordinator is not running.";
        $("archDayMeta").textContent = "Start it on the robot: SHOW=<the candy show> software/architect/tools/dev.sh day";
        return;
      }
      const d = await r.json();
      badge("archDayBadge", (DAY_NAMES[d.state] || d.state || "?") + " · " + secs(d.since_s || 0),
        d.state === "PAUSED" ? "bad" : d.state === "CANDY" || d.state === "CHOREO" ? "good" : "warn");
      steps.forEach((el) => {
        const on = el.dataset.dayState === d.state;
        el.classList.toggle("on", on);
        el.classList.toggle("paused", on && d.state === "PAUSED");
      });
      $("archDayWhy").textContent = d.why ? d.why.charAt(0).toUpperCase() + d.why.slice(1) : "—";
      const bits = [];
      bits.push(d.person ? "nearest person " + d.person.distance_m.toFixed(1) + " m, " + Math.abs(d.person.bearing_deg).toFixed(0) + "° " +
                           (d.person.bearing_deg >= 0 ? "left" : "right") : "nobody seen");
      if (d.show) bits.push("show " + d.show);
      if (d.nav) bits.push("nav: " + d.nav + (d.nav_stub ? " (a stub: nothing moves)" : ""));
      if (d.next_choreo_s != null) bits.push("a random show in " + secs(Math.max(0, d.next_choreo_s)));
      if (d.choreo && d.choreo.length) bits.push("shows: " + d.choreo.join(", "));
      $("archDayMeta").textContent = bits.join(" · ");
    } catch (e) { badge("archDayBadge", "unreachable", "dimbadge"); }
    finally { dayBusy = false; }
  }

  /* ---------- speech ---------- */
  let after = 0, lastListener = null, listenBusy = false;
  const KINDS = { wake: "after \u201cOpenDubs\u201d", trick: "\u201ctrick or treat\u201d check", open: "while waiting for a reply" };
  // A transcript: what Parakeet heard (left), what the robot actually said on the Jabra (right, the
  // `said` event: the exact words played, the runner's own lines included), and between them, small,
  // what the listener decided. Greetings and answers are noted when chosen and shown when spoken.
  function fmt(e) {
    const at = e.after_s != null ? "+" + e.after_s.toFixed(2) + " s after the phrase" : new Date(e.t * 1000).toLocaleTimeString();
    switch (e.event) {
      case "heard": return { who: "you", text: e.text ? e.text : "(nothing intelligible)", meta: "heard " + (KINDS[e.kind] || e.kind) + " \u00b7 " + at };
      case "said": return { who: "robot", text: e.text, meta: "said " + new Date(e.t * 1000).toLocaleTimeString() };
      case "spotted": return { who: "note", text: new Date(e.t * 1000).toLocaleTimeString() + " \u2014 spotter caught \u201c" + String(e.keyword).replace(/_/g, " ").toLowerCase() + "\u201d" };
      case "posted": return { who: "act", text: (e.kind === "trigger" ? "sent the trigger" : "sent " + e.kind + ": " + e.text) + (e.state ? " \u2192 runner " + e.state : "") + " \u00b7 " + at };
      case "show_started": return { who: "act", text: "show armed: " + e.routine + " \u2014 press the deadman fresh to start it" };
      case "show_refused": return { who: "act", text: "show refused: " + (e.why || "status " + e.status) };
      case "show_stopped": return { who: "act", text: "show stopped" };
      case "greeted": return { who: "note", text: "chose a greeting \u00b7 " + at };
      case "replied": return { who: "note", text: "answering with the local model \u00b7 " + at };
      case "held": return { who: "note", text: "no run started: a hold-on line \u00b7 " + at };
      case "light": return { who: "note", text: (e.ok ? "base strip flashed purple" : "base strip flash not accepted") + " \u00b7 " + at };
      case "not_confirmed": return { who: "note", text: "not confirmed as \u201ctrick or treat\u201d \u00b7 " + at };
      case "not_addressed": return { who: "note", text: "no wake phrase (" + (e.why || "") + ") \u00b7 " + at };
      case "turn_capped": return { who: "note", text: "stopped listening after " + e.seconds + " s \u00b7 " + at };
      case "dropped": return { who: "note", text: "ignored" + (e.why ? ": " + e.why : "") };
      case "listening": case "capture_open": return null;
      default: return { who: "note", text: e.event + (e.error ? ": " + e.error : "") };
    }
  }
  async function listenTick() {
    if (!shown() || listenBusy) return;
    listenBusy = true;
    try {
      const r = await fetch("/api/architect/listener?after=" + after, { cache: "no-store" });
      if (!r.ok) {
        lastListener = null;
        badge("archEars", "OFF", "dimbadge");
        $("archHeardNote").textContent = "The listener is not running (software/architect/tools/dev.sh listen).";
        return;
      }
      const d = await r.json(); lastListener = d;
      const s = d.status || {};
      badge("archEars", s.deaf ? (s.speaking ? "ROBOT TALKING" : "JUST SPOKE") : "LISTENING", s.deaf ? "warn" : "good");
      $("archLevel").style.width = Math.max(0, Math.min(100, ((s.level_db ?? -120) + 60) / 60 * 100)) + "%";
      $("archHeardNote").textContent = s.show ? "“Trick or treat” arms the show " + s.show + "." : "Posts to the ARCHITECT runner.";
      if (d.seq < after) { after = 0; $("archFeed").replaceChildren(); }   // the listener restarted
      for (const e of d.events || []) {
        const f = fmt(e); if (!f) continue;
        const li = document.createElement("li"); li.className = "arch-" + f.who;
        if (f.who === "you" || f.who === "robot") {
          const who = document.createElement("div"); who.className = "arch-who"; who.textContent = f.who === "you" ? "HEARD" : "ROBOT SAID";
          const b = document.createElement("div"); b.className = "arch-bubble"; b.textContent = f.text;
          const m = document.createElement("div"); m.className = "arch-when"; m.textContent = f.meta;
          li.append(who, b, m);
        } else {
          li.textContent = f.text;
        }
        $("archFeed").prepend(li);
        while ($("archFeed").children.length > 200) $("archFeed").lastChild.remove();
      }
      after = d.seq;
    } catch (e) { badge("archEars", "OFF", "dimbadge"); }
    finally { listenBusy = false; }
  }

  /* ---------- arms: the teleop tab's 3D view, live from /joint_states (rosbridge, via app.js) ---------- */
  // Built the first time the tab is shown (the canvas has no size before), and fed by app.js's
  // "opendubs-joint-states" event, which leaves teleop.js's own hook alone. Stale data reads STALE,
  // never as a pose: the teleop panel's rule.
  let arms = null, armsLoading = false, lastJointMs = 0;
  async function armsBoot() {
    if (arms || armsLoading || !shown()) return;
    armsLoading = true;
    try {
      arms = await new ArmView($("archArmCanvas")).load();
      for (const btn of document.querySelectorAll("[data-arch-armview]")) {
        btn.addEventListener("click", () => {
          document.querySelectorAll("[data-arch-armview]").forEach((b) => b.classList.remove("on"));
          btn.classList.add("on");
          arms.setView(btn.dataset.archArmview);
        });
      }
      arms.frame(); arms.render();
    } catch (e) {
      badge("archArmSrc", "render failed", "dimbadge");
      console.error("ARCHITECT arm view failed to load", e);
    } finally { armsLoading = false; }
  }
  function stowDeg(prefix) {
    let worst = 0;
    for (const [name, j] of Object.entries(arms.joints)) {
      if (name.startsWith(prefix) && !name.includes("finger")) worst = Math.max(worst, Math.abs(j.value) * 180 / Math.PI);
    }
    return worst;
  }
  window.addEventListener("opendubs-joint-states", (ev) => {
    const msg = ev.detail;
    if (!arms || !msg || !msg.name) return;
    const angles = {};
    for (let i = 0; i < msg.name.length; i++) angles[msg.name[i]] = msg.position?.[i];
    arms.setJoints(angles);
    lastJointMs = Date.now();
    badge("archArmSrc", "live", "good");
    for (const [side, id] of [["left", "archStowL"], ["right", "archStowR"]]) {
      const d = stowDeg(`openarm_${side}_`), el = $(id);
      el.textContent = d < 3 ? `${side} at stow` : `${side} ${Math.round(d)}° from stow`;
      el.style.color = d < 3 ? "var(--good)" : "var(--warn)";
    }
  });
  setInterval(() => {
    if (!shown()) return;
    armsBoot();
    if (!lastJointMs) { badge("archArmSrc", "no joint data", "dimbadge"); return; }
    const age = (Date.now() - lastJointMs) / 1000;
    if (age > 2) badge("archArmSrc", `STALE ${age.toFixed(0)}s`, "warn");
  }, 1000);
  const viewEl = $("view-architect");
  if (viewEl && window.MutationObserver) {
    new MutationObserver(() => { if (arms && viewEl.classList.contains("active")) { arms.frame(); arms.render(); } })
      .observe(viewEl, { attributes: true, attributeFilter: ["class"] });
  }
  window.addEventListener("resize", () => { if (arms) arms.frame(); });

  function badge(id, text, cls) { const b = $(id); if (b) { b.textContent = text; b.className = "badge " + cls; } }

  setInterval(camTick, 500);
  setInterval(listenTick, 500);
  setInterval(progTick, 1000);
  setInterval(dayTick, 1000);
  document.addEventListener("visibilitychange", () => { if (shown()) { camTick(); listenTick(); progTick(); dayTick(); } });
})();
