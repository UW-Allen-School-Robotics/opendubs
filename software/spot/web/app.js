// Spot player page. Any open page can STOP; the operator page (paired with the code the player prints)
// also beats every 200 ms, which keeps Spot's E-Stop released, and is the only page that can release it.
"use strict";
const $ = (id) => document.getElementById(id);
const BEAT_MS = 200;
let ws = null, state = null, logSeq = 0, built = "";
let token = sessionStorage.getItem("spot-operator-token");

function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }
function stop(why) { send({ op: "stop", why }); }

function connect() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws` +
                     (token ? `?token=${encodeURIComponent(token)}` : ""));
  ws.onmessage = (e) => render(JSON.parse(e.data));
  ws.onclose = () => {
    $("link").textContent = "player unreachable"; $("link").className = "pill down";
    setTimeout(connect, 1000);
  };
}

// The heartbeat. A hidden page stops beating, so Spot stops: nobody is watching it.
setInterval(() => { if (state && state.operator && !document.hidden) send({ op: "beat" }); }, BEAT_MS);

// STOP: pointerdown, not click, so it acts on touch at once. Space stops wherever the focus is and never
// presses a focused button.
$("stop").addEventListener("pointerdown", (e) => { e.preventDefault(); stop("STOP button"); });
for (const type of ["keydown", "keyup", "keypress"]) {
  window.addEventListener(type, (e) => {
    if (e.code === "Space" || e.key === " ") {
      e.preventDefault(); e.stopPropagation();
      if (type === "keydown" && !e.repeat) stop("space bar");
    }
  }, true);
}
document.addEventListener("click", (e) => { if (e.target.closest("button")) e.target.closest("button").blur(); });

$("claim").onclick = async () => {
  const r = await fetch("/api/claim", { method: "POST", headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ code: $("code").value.trim() }) });
  const j = await r.json();
  if (!r.ok) { $("pair-msg").textContent = j.detail; return; }
  token = j.token; sessionStorage.setItem("spot-operator-token", token);
  $("code").value = ""; $("pair-msg").textContent = "";
  ws.onclose = null; ws.close(); connect();
};
$("release").onclick = () => send({ op: "release" });
$("connect").onclick = () => send({ op: "connect", host: $("host").value.trim() });
$("disconnect").onclick = () => send({ op: "disconnect" });
$("arm").onclick = () => send({ op: "arm" });
$("start").onclick = () => send({ op: "start" });
$("upload").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  let plan;
  try { plan = JSON.parse(await f.text()); } catch (err) { alert(`not JSON: ${err}`); return; }
  const r = await fetch("/api/load", { method: "POST", headers: { "Content-Type": "application/json" },
                                       body: JSON.stringify({ name: f.name, plan }) });
  if (!r.ok) alert((await r.json()).detail);
  e.target.value = "";
};

function buildGroups(commands) {
  const key = JSON.stringify(commands);
  if (key === built) return;
  built = key;
  const root = $("groups"); root.innerHTML = "";
  const groups = {};
  for (const c of commands) {
    if (!groups[c.group]) {
      const g = document.createElement("div"); g.className = "group";
      g.innerHTML = `<h3></h3><div class="row"></div>`; g.querySelector("h3").textContent = c.group;
      root.appendChild(g); groups[c.group] = g.querySelector(".row");
    }
    const b = document.createElement("button");
    b.className = "tb cmd"; b.tabIndex = -1; b.textContent = c.label;
    b.onclick = () => send({ op: "cmd", name: c.name });
    groups[c.group].appendChild(b);
  }
}

// One button per stored show, as on the dashboard: its name, its length, the parts it moves, and the reason
// when it can't run here. A tap loads it (the operator only; not while a show is armed or playing).
let showsKey = "";
// The words a person reads, as on opendubs2's runner: READY, never "armed", since nothing moves until Start.
const WORDS = { EMPTY: "NO SHOW", LOADED: "LOADED", ARMED: "READY", PLAYING: "SHOW RUNNING", DONE: "DONE", STOPPED: "STOPPED" };
function renderShows(s, pb) {
  const busy = ["ARMED", "PLAYING"].includes(pb.state);
  const key = JSON.stringify([s.shows, pb.plan, busy, s.operator]);
  if (key === showsKey) return;
  showsKey = key;
  const root = $("shows"); root.innerHTML = "";
  if (!s.shows.length) { root.innerHTML = `<p class="dim">No shows stored on this player yet.</p>`; return; }
  for (const sh of s.shows) {
    const b = document.createElement("button");
    b.className = "show" + (sh.name === pb.plan ? " loaded" : "") + (sh.can_run ? "" : " cant");
    b.tabIndex = -1; b.disabled = !s.operator || busy;
    const len = sh.length_s == null ? "?" : `${Math.floor(sh.length_s / 60)}:${String(Math.round(sh.length_s % 60)).padStart(2, "0")}`;
    b.innerHTML = `<span class="lbl"></span><span class="dur"></span><span class="parts"></span><span class="why"></span>`;
    b.querySelector(".lbl").textContent = sh.label;
    b.querySelector(".dur").textContent = sh.can_run ? len : `${len} · can't run`;
    b.querySelector(".parts").textContent = sh.parts.join(" · ");
    b.querySelector(".why").textContent = sh.why.join("; ");
    b.title = sh.why.join("; ");
    b.onclick = () => send({ op: "load", name: sh.name });
    root.appendChild(b);
  }
}

function render(s) {
  state = s;
  $("link").textContent = s.operator ? "operator" : (s.operator_present ? "viewer · operator elsewhere" : "viewer");
  $("link").className = "pill up";
  $("fake").hidden = !s.fake;
  $("pair").hidden = s.operator;
  $("op").hidden = !s.operator;
  for (const el of document.querySelectorAll(".op-only")) el.hidden = !s.operator;

  const e = $("estop");
  if (!s.connected) { e.className = "estop off"; e.textContent = s.connecting ? "connecting…" : "E-Stop: not connected"; }
  else if (s.estop === "RELEASED") { e.className = "estop released"; e.textContent = "E-Stop RELEASED: Spot may move"; }
  else { e.className = "estop stopped"; e.textContent = "E-Stop STOPPED"; }

  if (document.activeElement !== $("host")) $("host").value = s.host;
  $("connect").disabled = s.connected || s.connecting;
  $("disconnect").disabled = !s.connected;
  $("release").disabled = !s.connected || s.estop === "RELEASED";
  buildGroups(s.commands);
  for (const b of document.querySelectorAll(".cmd")) b.disabled = !s.connected;

  const pb = s.playback;
  renderShows(s, pb);
  $("summary").textContent = pb.plan ? `${pb.plan}\n${pb.summary}` : "no plan loaded";
  $("pstate").textContent = WORDS[pb.state] || pb.state; $("pstate").className = `state ${pb.state}`;
  $("pwhy").textContent = pb.state === "PLAYING"
    ? `t = ${pb.show_s.toFixed(1)} of ${pb.length_s.toFixed(1)} s · sent ${pb.sent}/${pb.windows}`
    : (pb.state === "ARMED" ? "nothing moves until Start (or ENCORE's start)" : pb.why);
  $("progress").style.width = pb.state === "PLAYING" ? `${Math.min(100, Math.max(0, 100 * pb.show_s / pb.length_s))}%`
                                                    : (pb.state === "DONE" ? "100%" : "0");
  const canArm = ["LOADED", "STOPPED", "DONE"].includes(pb.state) && s.problems.length === 0;
  $("arm").disabled = !canArm;
  $("start").disabled = pb.state !== "ARMED";
  $("problems").innerHTML = "";
  if (pb.plan && !["ARMED", "PLAYING"].includes(pb.state))
    for (const p of s.problems) { const li = document.createElement("li"); li.textContent = p; $("problems").appendChild(li); }

  const st = s.status || {};
  $("status").textContent = st.error ? st.error : (s.connected
    ? `motors ${st.motors ?? "?"}   battery ${st.battery_pct ?? "?"} %   lease ${st.lease ?? "?"}\n` +
      `robot e-stops: ${(st.estop_robot || []).join(", ")}\nfaults: ${(st.faults || []).join(", ") || "none"}`
    : "-");

  const log = $("log"), atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
  for (const [seq, line] of s.log) if (seq > logSeq) { log.textContent += line + "\n"; logSeq = seq; }
  if (atEnd) log.scrollTop = log.scrollHeight;
}

connect();
