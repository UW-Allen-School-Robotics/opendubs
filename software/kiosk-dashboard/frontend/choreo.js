/* OpenDubs dashboard — the Shows page: the routine palette (choreography plan 4.4).
   Lists the show runner's routines (through the dashboard's own /api/choreo/), runs the one the
   operator taps, and offers STOP. One routine at a time; the runner refuses a second.
   Works in any browser on plain HTTP or HTTPS (standing rule 12): nothing here needs the tablet. */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  if (!el("view-shows")) return;
  let routines = [], status = null, timer = null, listKey = "";

  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || body.detail || `HTTP ${r.status}`);
    return body;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function render() {
    const running = status && status.running;
    el("showsStop").disabled = !running;
    const last = status && status.last;
    el("showsStatus").innerHTML = running
      ? `<b class="shows-running">▶ ${esc(labelOf(running))}</b> <span class="dim">${esc(status.status || "")}</span>`
      : last ? `Last: <b>${esc(labelOf(last.id))}</b> — <span class="${last.status === "COMPLETED" ? "ok" : "bad"}">${esc(last.status)}</span> <span class="dim">${esc(last.cause || "")}</span>`
      : "Pick a routine.";
    /* Rebuild the buttons only when something about them changed: rebuilding every poll would
       replace a button under a finger mid-tap, and the tap would be lost. */
    const key = JSON.stringify([routines.map((r) => [r.id, r.valid, r.duration_s, r.warnings]), running]);
    if (key === listKey) return;
    listKey = key;
    el("showsList").innerHTML = routines.map((r) => `
      <button class="shows-btn ${r.valid ? "" : "invalid"} ${running === r.id ? "active" : ""}" data-id="${esc(r.id)}"
        ${!r.valid || running ? "disabled" : ""}
        title="${esc(r.valid ? `${r.duration_s} s${r.warnings ? ` · ${r.warnings} warning(s)` : ""}` : `Does not validate: ${(r.problems || []).join("; ")}`)}">
        <span class="lbl">${esc(r.label)}</span><span class="dur">${r.valid ? `${Math.round(r.duration_s)} s` : "invalid"}</span>
      </button>`).join("") || '<div class="dim">No routines.</div>';
  }
  const labelOf = (id) => (routines.find((r) => r.id === id) || {}).label || id;

  async function refresh(full) {
    try {
      if (full || !routines.length) routines = await getJSON("/api/choreo/routines");
      status = await getJSON("/api/choreo/palette");
    } catch (e) {
      routines = []; status = null; listKey = "";
      el("showsStatus").innerHTML = `<span class="bad">${esc(e.message)}</span>`;
      el("showsList").innerHTML = ""; el("showsStop").disabled = true;
      return;
    }
    render();
  }

  el("showsList").addEventListener("click", async (e) => {
    const b = e.target.closest(".shows-btn"); if (!b || b.disabled) return;
    try { await getJSON(`/api/choreo/run/${encodeURIComponent(b.dataset.id)}`, { method: "POST" }); }
    catch (err) { el("showsStatus").innerHTML = `<span class="bad">${esc(err.message)}</span>`; }
    refresh(false);
  });
  el("showsStop").addEventListener("click", async () => {
    await getJSON("/api/choreo/stop", { method: "POST" }).catch(() => {});
    refresh(false);
  });

  /* Poll only while the page is on screen. */
  new MutationObserver(() => {
    const on = el("view-shows").classList.contains("active");
    if (on && !timer) { refresh(true); timer = setInterval(() => refresh(false), 1000); }
    if (!on && timer) { clearInterval(timer); timer = null; }
  }).observe(el("view-shows"), { attributes: true, attributeFilter: ["class"] });
})();
