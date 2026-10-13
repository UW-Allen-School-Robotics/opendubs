// The operator's way out of the face, for a tablet that is both the face and the dashboard (tablet
// plan 5.4, the minimum): a LONG-PRESS anywhere opens three big buttons — STOP SHOW, DASHBOARD, FACE.
// Never a single tap, so a brush against a mounted face does nothing. The RC deadman and the e-stop
// remain the primary stops; this is the third.
//
// Served by the robot's dashboard (/face/), the page also loads the dashboard's show-audio client, so
// the face holds the show-audio role: show music and ambient play from the face (show_audio.js, the
// Choreography lane's, loaded as it is).

const HOLD_MS = 1500;          // how long a press must last
const IDLE_CLOSE_MS = 10000;   // the overlay closes itself if nobody uses it

export function start({ onRobot }) {
  const op = document.getElementById("op");
  const note = document.getElementById("opNote");
  const defaultNote = note.textContent;
  let timer = null, closeTimer = null, startXY = null;

  if (onRobot) {
    const s = document.createElement("script");
    s.src = "/show_audio.js";                 // the dashboard's own copy, same origin
    document.head.appendChild(s);
  }

  const close = () => { op.classList.remove("open"); note.textContent = defaultNote; clearTimeout(closeTimer); };
  const open = () => {
    op.classList.add("open");
    // A deliberate touch: let the show-audio client start audio if this browser was holding it back.
    try { window.OpenDubsShowAudio && window.OpenDubsShowAudio.claim && window.OpenDubsShowAudio.claim(); } catch (e) {}
    clearTimeout(closeTimer); closeTimer = setTimeout(close, IDLE_CLOSE_MS);
  };
  const cancel = () => { clearTimeout(timer); timer = null; startXY = null; };

  addEventListener("pointerdown", (e) => {
    if (op.classList.contains("open")) return;
    startXY = [e.clientX, e.clientY];
    timer = setTimeout(() => { timer = null; open(); }, HOLD_MS);
  });
  addEventListener("pointermove", (e) => {      // a swipe is not a press
    if (startXY && Math.hypot(e.clientX - startXY[0], e.clientY - startXY[1]) > 40) cancel();
  });
  addEventListener("pointerup", cancel);
  addEventListener("pointercancel", cancel);

  document.getElementById("opBack").addEventListener("click", close);
  document.getElementById("opDash").addEventListener("click", () => {
    location.href = onRobot ? "/" : "about:blank";
  });
  document.getElementById("opStop").addEventListener("click", async () => {
    clearTimeout(closeTimer); closeTimer = setTimeout(close, IDLE_CLOSE_MS);
    if (!onRobot) { note.textContent = "No robot here: this face is a twin."; return; }
    note.textContent = "Stopping the show…";
    try {
      const r = await fetch("/api/choreo/stop", { method: "POST" });
      const body = await r.json().catch(() => ({}));
      note.textContent = r.ok ? "Show stopped." : `Not stopped: ${body.error || body.detail || r.status}`;
    } catch (e) {
      note.textContent = `Not stopped: ${e.message}. Use the RC deadman or the e-stop.`;
    }
  });
}
