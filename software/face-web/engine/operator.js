// The operator's way out of the face, for a tablet that is both the face and the dashboard (tablet
// plan 5.4, the minimum): HOLD A FINGER ON THE FACE FOR ONE SECOND, anywhere but the Talk button, and
// three big buttons open: STOP SHOW, DASHBOARD, FACE. Never a single tap, so a brush against a mounted
// face does nothing. The RC deadman and the e-stop remain the primary stops; this is the third.
//
// Every touch answers at once (2026-10-22, the user: the old 1.5 s long-press gave no sign it had been
// felt, and often did nothing): a ripple where the finger lands, and a ring around it that fills over
// the second. Lifting or sliding away empties it; a full ring always opens the controls. The page takes
// every touch itself (index.html: touch-action none, no callout, no context menu), so the WebView's own
// long-press can no longer cancel the hold halfway, which is what made the old gesture unreliable.
//
// Served by the robot's dashboard (/face/), the page also loads the dashboard's show-audio client, so
// the face holds the show-audio role: show music and ambient play from the face (show_audio.js, the
// Choreography lane's, loaded as it is).

const HOLD_MS = 1000;          // how long a press must last; the ring's fill (index.html) takes as long
const SLIDE_PX = 60;           // a finger that slides further is a swipe, not a press
const TAP_MS = 500;            // shorter than this, and not a swipe: a tap, which shows the hint
const HINT_MS = 3000;          // how long the hint stays
const IDLE_CLOSE_MS = 10000;   // the overlay closes itself if nobody uses it
const DASH_CHECK_MS = 2500;    // DASHBOARD asks the robot first, and says so if it does not answer

export function start({ onRobot }) {
  const op = document.getElementById("op");
  const note = document.getElementById("opNote");
  const defaultNote = note.textContent;
  const touch = document.getElementById("touch");
  let timer = null, closeTimer = null, startXY = null, downAt = 0, hintTimer = null;
  // Only the person who built it knows to long-press the face. A short tap says how, for a moment, in
  // small grey type at the bottom; nothing shows on its own, so a show's face is never cluttered.
  const hint = document.getElementById("hint");
  const showHint = () => {
    // Never over a caption: they share the bottom strip, and the caption already says what is going on.
    if (!hint || (window.faceCaptionShowing && window.faceCaptionShowing())) return;
    hint.classList.add("show");
    clearTimeout(hintTimer); hintTimer = setTimeout(() => hint.classList.remove("show"), HINT_MS);
  };

  if (onRobot) {
    // The dashboard's own copies, same origin: the show-audio client, and the tablet's sensors (which
    // send only inside the kiosk app), so both keep running while the kiosk shows the face.
    for (const src of ["/show_audio.js", "/tablet_sensors.js"]) {
      const s = document.createElement("script");
      s.src = src;
      document.head.appendChild(s);
    }
  }

  const close = () => { op.classList.remove("open"); note.textContent = defaultNote; clearTimeout(closeTimer); };
  const open = () => {
    op.classList.add("open");
    // A deliberate touch: let the show-audio client start audio if this browser was holding it back.
    try { window.OpenDubsShowAudio && window.OpenDubsShowAudio.claim && window.OpenDubsShowAudio.claim(); } catch (e) {}
    clearTimeout(closeTimer); closeTimer = setTimeout(close, IDLE_CLOSE_MS);
  };
  const cancel = () => { clearTimeout(timer); timer = null; startXY = null; touch && touch.classList.remove("down"); };
  // The ripple and the ring, restarted at the finger: the class comes off, a reflow, and back on.
  const feedback = (x, y) => {
    if (!touch) return;
    touch.classList.remove("down");
    touch.style.left = `${x}px`; touch.style.top = `${y}px`;
    void touch.offsetWidth;
    touch.classList.add("down");
  };

  addEventListener("contextmenu", (e) => e.preventDefault());
  addEventListener("pointerdown", (e) => {
    if (op.classList.contains("open") || !e.isPrimary) return;
    if (e.target && e.target.closest && e.target.closest("#sys")) return;   // Talk keeps its own touches
    startXY = [e.clientX, e.clientY]; downAt = performance.now();
    feedback(e.clientX, e.clientY);
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; touch && touch.classList.remove("down"); hint && hint.classList.remove("show"); open(); }, HOLD_MS);
  });
  addEventListener("pointermove", (e) => {      // a swipe is not a press
    if (startXY && Math.hypot(e.clientX - startXY[0], e.clientY - startXY[1]) > SLIDE_PX) cancel();
  });
  addEventListener("pointerup", () => {
    const tap = startXY && timer && performance.now() - downAt < TAP_MS;   // still a press, released early
    cancel();
    if (tap) showHint();
  });
  addEventListener("pointercancel", cancel);

  document.getElementById("opBack").addEventListener("click", close);
  document.getElementById("opDash").addEventListener("click", async () => {
    clearTimeout(closeTimer); closeTimer = setTimeout(close, IDLE_CLOSE_MS);
    if (!onRobot) { note.textContent = "No robot here: this face is a twin."; return; }
    note.textContent = "Opening the dashboard…";
    // Asked first, so a dashboard that is restarting says so here instead of leaving a blank page.
    const ask = new AbortController(), giveUp = setTimeout(() => ask.abort(), DASH_CHECK_MS);
    try {
      const r = await fetch("/api/health", { cache: "no-store", signal: ask.signal });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      location.href = "/";
    } catch (e) {
      note.textContent = "The robot's dashboard isn't answering (it may be restarting). The face keeps running: try again in a moment.";
    } finally { clearTimeout(giveUp); }
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
