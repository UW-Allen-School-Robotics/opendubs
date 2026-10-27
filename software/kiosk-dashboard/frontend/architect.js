/* ARCHITECT tab: the program ARCHITECT wrote, what the Jabra listener hears, and the Orbbec's image.
   Read-only, from /api/architect/ (backend/architect_proxy.py), and only while the tab is on screen:
   nothing is fetched while another view is up or the page is hidden, and the camera feed itself
   subscribes only while frames are asked for. The program is shown as ssa's CLI shows it (a numbered,
   highlighted panel), with the changes from the previous program when a correction replaces it. */
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

  /* ---------- speech ---------- */
  let after = 0, lastListener = null, listenBusy = false;
  const KINDS = { wake: "after OpenDubs", trick: "trick or treat check", open: "open window" };
  function fmt(e) {
    switch (e.event) {
      case "heard": return ["heard", e.text ? "“" + e.text + "”" : "(nothing intelligible)", KINDS[e.kind] || e.kind];
      case "spotted": return ["minor", "spotter caught " + String(e.keyword).replace(/_/g, " ").toLowerCase(), ""];
      case "posted": return ["act", "sent " + e.kind + (e.kind === "trigger" ? "" : ": " + e.text), e.state ? "runner now " + e.state : ""];
      case "show_started": return ["act", "show armed: " + e.routine, "press the deadman fresh to start it"];
      case "show_refused": return ["act", "show refused", e.why || ("status " + e.status)];
      case "show_stopped": return ["act", "show stopped", ""];
      case "greeted": case "replied": case "held": return ["said", "robot: " + e.text, e.event === "held" ? "no run started" : ""];
      case "light": return ["minor", e.ok ? "base strip: purple" : "base strip: flash not accepted", ""];
      case "not_confirmed": return ["minor", "not confirmed as trick or treat", ""];
      case "not_addressed": return ["minor", "no wake phrase: " + (e.why || ""), ""];
      case "turn_capped": return ["minor", "stopped listening after " + e.seconds + " s", ""];
      case "dropped": return ["minor", "ignored" + (e.why ? ": " + e.why : ""), ""];
      case "said": case "listening": case "capture_open": return null;
      default: return ["minor", e.event + (e.error ? ": " + e.error : ""), ""];
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
        const [cls, text, note] = f;
        const li = document.createElement("li"); li.className = "arch-" + cls;
        const t = document.createElement("span"); t.className = "arch-t";
        t.textContent = e.event === "spotted" || e.after_s == null ? new Date(e.t * 1000).toLocaleTimeString() : "+" + e.after_s.toFixed(2) + " s";
        const x = document.createElement("span"); x.textContent = text;
        if (note) { const n = document.createElement("div"); n.className = "dim"; n.textContent = note; x.append(n); }
        li.append(t, x); $("archFeed").prepend(li);
        while ($("archFeed").children.length > 150) $("archFeed").lastChild.remove();
      }
      after = d.seq;
    } catch (e) { badge("archEars", "OFF", "dimbadge"); }
    finally { listenBusy = false; }
  }

  function badge(id, text, cls) { const b = $(id); if (b) { b.textContent = text; b.className = "badge " + cls; } }

  setInterval(camTick, 500);
  setInterval(listenTick, 500);
  setInterval(progTick, 1000);
  document.addEventListener("visibilitychange", () => { if (shown()) { camTick(); listenTick(); progTick(); } });
})();
