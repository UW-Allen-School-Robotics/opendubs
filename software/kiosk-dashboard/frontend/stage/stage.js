/* OpenDubs stage page — the projector's picture, live (notes/design/choreo-stage.md). Choreography's.

   A dancing skeleton on the beat (the fallback figure), and a person in front of the depth camera drawn
   LIVE as a cartoon skeleton over it: the person takes over when they step in (a crossfade) and hands back
   when they leave. Skeleton points only, from /ws/skeleton (the dashboard relays the Thor's feed); no
   camera image ever reaches this page.

   Query: ?video=<url> plays a video under the skeletons (looped, muted unless &sound=1); ?bpm=99.4 the
   fallback dancer's tempo; ?hud=1 shows the feed, the frame rate and the measured latency.
   The kiosk app on the projector reloads a page that stops checking in: alive() is called from the
   draw loop every 5 s, so a page whose drawing stops also stops checking in. */
"use strict";
(() => {
  const q = new URLSearchParams(location.search);
  const cv = document.getElementById("stage"), g = cv.getContext("2d");
  const hud = document.getElementById("hud"), video = document.getElementById("video");
  const BPM = Number(q.get("bpm")) || 99.4, BEAT_MS = 60000 / BPM;
  const BONE = "#eeecde", DARK = "#0e121c";
  if (q.get("hud")) hud.style.display = "block";
  if (q.get("video")) {
    video.src = q.get("video"); video.hidden = false; video.muted = !q.get("sound");
    video.play().catch(() => {});
  }

  /* ---- the feed, and the Thor's clock (as show audio keeps it) ----------------------------------- */
  let ws = null, backoff = 500, latest = null, clock = null;
  const samples = [], pending = new Set(), lat = [];
  const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/skeleton`);
    ws.onopen = () => { backoff = 500; burst(); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if ("pong" in m) return onPong(m);
      if (m.v === 1) { m.arrived = performance.now(); latest = m; }
    };
    ws.onclose = () => { setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 8000); };
  }
  function burst() {
    for (let i = 0; i < 8; i++) setTimeout(() => { const t0 = performance.now(); pending.add(t0); send({ ping: t0 }); }, i * 40);
  }
  function onPong(m) {
    const t3 = performance.now();
    if (!pending.delete(m.pong)) return;
    const t0 = m.pong, delta = (t3 - t0) - (m.t2 - m.t1);
    samples.push({ theta: ((m.t1 - t0) + (m.t2 - t3)) / 2, delta, at: t3 });
    while (samples.length > 40) samples.shift();
    const best = samples.reduce((a, b) => (b.delta < a.delta ? b : a));
    clock = { offset: best.theta, err: best.delta / 2 };
  }
  setInterval(burst, 10000);                       // keep the clock fresh
  connect();

  /* ---- the cartoon skeleton (skeleton_art.py, in canvas) ------------------------------------------- */
  const BONES = [["l_shoulder", "l_elbow"], ["l_elbow", "l_wrist"], ["r_shoulder", "r_elbow"], ["r_elbow", "r_wrist"],
                 ["l_hip", "l_knee"], ["l_knee", "l_ankle"], ["r_hip", "r_knee"], ["r_knee", "r_ankle"]];
  function bone(a, b, w) {
    const dx = b[0] - a[0], dy = b[1] - a[1], n = Math.hypot(dx, dy) || 1, px = -dy / n * w * 0.35, py = dx / n * w * 0.35;
    g.beginPath(); g.moveTo(a[0] + px, a[1] + py); g.lineTo(b[0] + px, b[1] + py); g.lineTo(b[0] - px, b[1] - py);
    g.lineTo(a[0] - px, a[1] - py); g.closePath(); g.fill();
    for (const [x, y] of [a, b]) for (const s of [-1, 1]) {
      g.beginPath(); g.arc(x + s * px * 1.1, y + s * py * 1.1, w * 0.45, 0, 7); g.fill();
    }
  }
  // P: {name: [px, py]} in screen pixels; s: pixels per metre
  function skeleton(P, s, alpha) {
    if (alpha <= 0.01) return;
    g.save(); g.globalAlpha = alpha; g.fillStyle = BONE; g.strokeStyle = BONE;
    const w = Math.max(4, s * 0.055);
    for (const [a, b] of BONES) if (P[a] && P[b]) bone(P[a], P[b], w);
    if (P.neck && P.pelvis) {
      const n = P.neck, p = P.pelvis;
      g.lineWidth = w * 0.6; g.beginPath(); g.moveTo(...n); g.lineTo(...p); g.stroke();
      g.lineWidth = w * 0.45;
      for (let k = 0; k < 5; k++) {
        const f = 0.12 + k * 0.11, x = n[0] + (p[0] - n[0]) * f, y = n[1] + (p[1] - n[1]) * f, half = s * (0.15 - 0.012 * k);
        g.beginPath(); g.ellipse(x, y + s * 0.02, half, s * 0.05, 0, Math.PI * 1.1, Math.PI * 1.9); g.stroke();
      }
      g.beginPath(); g.ellipse(p[0], p[1] + s * 0.015, s * 0.15, s * 0.075, 0, 0, 7); g.fill();
      g.fillStyle = DARK; g.beginPath(); g.ellipse(p[0], p[1] + s * 0.025, s * 0.06, s * 0.035, 0, 0, 7); g.fill(); g.fillStyle = BONE;
    }
    g.lineWidth = w * 0.5;
    for (const [a, b] of [["l_shoulder", "neck"], ["r_shoulder", "neck"], ["l_hip", "pelvis"], ["r_hip", "pelvis"]])
      if (P[a] && P[b]) { g.beginPath(); g.moveTo(...P[a]); g.lineTo(...P[b]); g.stroke(); }
    if (P.head) {
      const [hx, hy] = P.head, r = s * 0.13;
      g.beginPath(); g.ellipse(hx, hy - r * 0.15, r, r * 0.95, 0, 0, 7); g.fill();
      g.fillRect(hx - r * 0.55, hy + r * 0.5, r * 1.1, r * 0.55);
      g.fillStyle = DARK;
      for (const sx of [-1, 1]) { g.beginPath(); g.ellipse(hx + sx * r * 0.42, hy + r * 0.05, r * 0.26, r * 0.25, 0, 0, 7); g.fill(); }
      g.beginPath(); g.moveTo(hx, hy + r * 0.35); g.lineTo(hx - r * 0.1, hy + r * 0.55); g.lineTo(hx + r * 0.1, hy + r * 0.55); g.fill();
      g.fillStyle = BONE;
    }
    for (const k of ["l_wrist", "r_wrist", "l_ankle", "r_ankle"]) if (P[k]) {
      g.beginPath(); g.ellipse(P[k][0], P[k][1], w * 0.8, w * 0.6, 0, 0, 7); g.fill();
    }
    g.restore();
  }

  /* ---- the dancing fallback figure, on the beat (a simple 2D model, metres) ------------------------ */
  function limb(o, a1, a2, l1, l2) {
    const r = Math.PI / 180;
    const x1 = o[0] + l1 * Math.sin(a1 * r), y1 = o[1] - l1 * Math.cos(a1 * r);
    return [[x1, y1], [x1 + l2 * Math.sin((a1 + a2) * r), y1 - l2 * Math.cos((a1 + a2) * r)]];
  }
  function dancer(ms) {
    const b = ms / BEAT_MS, ph = b % 1, beat = Math.floor(b) % 4, bounce = Math.abs(Math.sin(Math.PI * ph));
    const swing = beat % 2 === 0 ? 55 : -55;
    const px = 0.05 * Math.sin(Math.PI * b / 2), py = 0.98 - 0.05 * bounce;
    const neck = [px, py + 0.47], head = [neck[0] + 0.02 * Math.sin(Math.PI * b), neck[1] + 0.17];
    const ls = [neck[0] - 0.19, neck[1] - 0.03], rs = [neck[0] + 0.19, neck[1] - 0.03];
    const [le, lw] = limb(ls, -(90 + swing * 0.6), -40 - 30 * ph, 0.28, 0.26);
    const [re, rw] = limb(rs, 90 - swing * 0.6, 40 + 30 * ph, 0.28, 0.26);
    const lh = [px - 0.10, py - 0.03], rh = [px + 0.10, py - 0.03];
    const [lk, la] = limb(lh, -4 - 8 * bounce - (beat === 3 ? 25 : 0), 6 * bounce, 0.44, 0.43);
    const [rk, ra] = limb(rh, 4 + 8 * bounce + (beat === 1 ? 25 : 0), -6 * bounce, 0.44, 0.43);
    return { head, neck, l_shoulder: ls, r_shoulder: rs, l_elbow: le, r_elbow: re, l_wrist: lw, r_wrist: rw,
             pelvis: [px, py], l_hip: lh, r_hip: rh, l_knee: lk, r_knee: rk, l_ankle: la, r_ankle: ra };
  }

  /* ---- placing a skeleton on the screen -------------------------------------------------------------- */
  // metres (x right, y up) -> pixels, feet near the bottom. The dancer is centred and 1.75 m tall on the
  // screen; a live person is scaled to fit and placed where they stand (the camera's centre at the
  // screen's centre), MIRRORED — they see themselves as in a mirror — and kept on the screen.
  function place(J, W, H, mirror, fit) {
    const pts = Object.values(J);
    const minY = Math.min(...pts.map((p) => p[1])), maxY = Math.max(...pts.map((p) => p[1]));
    const midX = (Math.min(...pts.map((p) => p[0])) + Math.max(...pts.map((p) => p[0]))) / 2;
    const s = fit ? (H * 0.78) / Math.max(1.2, maxY - minY + 0.25) : (H * 0.78) / 1.75;
    const sx = mirror ? -1 : 1;
    const off = fit ? Math.max(-W * 0.35, Math.min(W * 0.35, sx * midX * s)) : 0;
    const P = {};
    for (const [k, p] of Object.entries(J)) P[k] = [W / 2 + off + sx * (p[0] - midX) * s, H * 0.95 - (p[1] - minY) * s];
    return [P, s];
  }
  const MIN_CONF = 0.3;
  function person(f) {                            // the one shown: the most confident, then the nearest
    const ok = (f.people || []).map((p) => {
      const js = Object.entries(p.joints || {}).filter(([, v]) => (v[3] ?? 1) >= MIN_CONF);
      const conf = js.length ? js.reduce((a, [, v]) => a + (v[3] ?? 1), 0) / 15 : 0;
      const z = js.length ? js.reduce((a, [, v]) => a + v[2], 0) / js.length : 99;
      return { joints: Object.fromEntries(js.map(([k, v]) => [k, v])), score: conf - z * 0.05 };
    }).filter((p) => Object.keys(p.joints).length >= 6);
    ok.sort((a, b) => b.score - a.score);
    return ok[0] || null;
  }

  /* ---- the draw loop ------------------------------------------------------------------------------------ */
  let liveAlpha = 0, lastSeen = -1e9, lastAlive = 0, frames = 0, fpsAt = performance.now(), fps = 0;
  function draw(now) {
    const W = cv.width = innerWidth * devicePixelRatio, H = cv.height = innerHeight * devicePixelRatio;
    if (video.hidden) {                            // no video: a dark light table
      const grd = g.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, H * 0.9);
      grd.addColorStop(0, "#0c2a44"); grd.addColorStop(1, "#04070f");
      g.fillStyle = grd; g.fillRect(0, 0, W, H);
    } else g.clearRect(0, 0, W, H);
    const fresh = latest && now - latest.arrived < 500;
    const who = fresh ? person(latest) : null;
    if (who) lastSeen = now;
    const target = now - lastSeen < 1500 ? 1 : 0;  // hand back 1.5 s after the person leaves
    liveAlpha += (target - liveAlpha) * Math.min(1, (now - (draw.prev || now)) / 400);
    draw.prev = now;
    const [DP, ds] = place(dancer(now), W, H, false, false);
    skeleton(DP, ds, 1 - liveAlpha);
    if (who) { const [LP, ls] = place(who.joints, W, H, true, true); skeleton(LP, ls, liveAlpha); draw.last = [LP, ls]; }
    else if (draw.last && liveAlpha > 0.01) skeleton(draw.last[0], draw.last[1], liveAlpha);
    if (who && clock && latest.painted !== true) {          // capture -> painted, on the Thor's clock
      latest.painted = true;
      lat.push(now + clock.offset - latest.t * 1000);
      while (lat.length > 120) lat.shift();
    }
    frames++;
    if (now - fpsAt > 1000) { fps = frames * 1000 / (now - fpsAt); frames = 0; fpsAt = now; }
    if (now - lastAlive > 5000) { lastAlive = now; try { window.OpenDubsKiosk?.alive?.(); } catch (e) { /* not in the kiosk */ } }
    if (q.get("hud")) {
      const s = [...lat].sort((a, b) => a - b), p = (k) => (s.length ? Math.round(s[Math.floor(k * (s.length - 1))]) : "–");
      hud.textContent = `feed ${latest ? latest.status || "live" : "none"} · ${latest ? (now - latest.arrived).toFixed(0) + " ms old" : ""}\n`
        + `person ${who ? "yes" : "no"} · ${fps.toFixed(0)} fps · clock ±${clock ? clock.err.toFixed(1) : "?"} ms\n`
        + `latency capture→paint p50 ${p(0.5)} p95 ${p(0.95)} ms (n ${s.length})`;
    }
    requestAnimationFrame(draw);
  }
  setInterval(() => {                               // report the measured latency to the dashboard
    if (!lat.length) return;
    const s = [...lat].sort((a, b) => a - b);
    send({ latency_ms: { p50: Math.round(s[Math.floor(0.5 * (s.length - 1))]), p95: Math.round(s[Math.floor(0.95 * (s.length - 1))]), n: s.length } });
  }, 2000);
  requestAnimationFrame(draw);
})();
