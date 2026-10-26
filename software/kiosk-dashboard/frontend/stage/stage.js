/* OpenDubs stage page — the projector's picture, live (notes/design/choreo-stage.md). Choreography's.

   A dancing skeleton on the beat (the fallback figure), and a person in front of the depth camera drawn
   LIVE as a cartoon skeleton over it: the person takes over when they step in (a crossfade) and hands back
   when they leave. Skeleton points only, from /ws/skeleton (the dashboard relays the Thor's feed); no
   camera image ever reaches this page.

   THE SHOW'S TIMING comes from the Thor, over the same WebSocket (the dashboard's stage.py, from the show
   runner): the videos and when, the beat grid, start (the Thor's monotonic t0) and stop. The page plays the
   video on the show's clock (kept to it as show audio keeps the music) and dances on the show's beat. It
   needs nothing else, so it is the same page in the projector's app, on a small computer by the projector,
   or in any browser. The video carries the song: the projector plays the music.

   ANY SCREEN: the page lays itself out for whatever viewport it gets — a projector, a widescreen video wall,
   a phone — never assuming 1920x1080: the video is letterboxed or pillarboxed to its own aspect, and the
   skeletons are placed and scaled inside its picture. With NO screen (a stage Pi playing sound only) the
   browser may not draw at all, so the show's media is also kept in step from a timer.

   THE VIDEO, like a streaming service: the stage box's OWN copy when it has one (its media partition,
   synced while idle: no Wi-Fi at all), else streamed from the robot with a HEAD START — when a show is
   armed the page buffers `buffer_s` (25 s) ahead, reports it, and the runner's GO waits for that; it keeps
   reading ahead as it plays. A page that runs dry mid-show reports a stall: the tablet takes the music from
   the same point, the page goes silent, and it catches up on the show's clock when the data returns.

   BETWEEN SHOWS a STAGE READY card says what this screen is: the robot connected or not, the resolution,
   the sound output, the clock to the Thor, and an under-voltage warning — the last three from the device
   this page runs on, when it reports itself (a stage Pi's http://127.0.0.1:8095/device.json, or the
   kiosk app's OpenDubsKiosk.device()). Once a show is ARMED the screen goes black until it starts.

   Query (for testing, without a show): ?video=<url> loops a video under the skeletons (muted unless
   &sound=1); ?bpm=99.4 the dancer's tempo; ?hud=1 shows the feed, the show, the frame rate and the latency;
   ?card=0 hides the ready card; ?stretch=2 is for a screen that STRETCHES its input — a video wall that
   spreads a 16:9 frame over 32:9 — the page lays itself out at the wall's own shape (twice as wide) and
   squeezes that into the frame, so the wall shows everything in its true proportions (a stage box sets it
   in stage.conf's ROBOT_URL). The device this page runs on reloads a page that stops checking in:
   alive() (the kiosk app's, else a POST to the stage Pi's /alive) every 5 s from the draw loop, or from
   the sync timer when the device says it has no screen. A deploy that changes the dashboard's pages
   reloads this one too, between shows (it polls /api/frontend-version every 15 s). */
"use strict";
(() => {
  const q = new URLSearchParams(location.search);
  const cv = document.getElementById("stage"), g = cv.getContext("2d");
  const hud = document.getElementById("hud"), video = document.getElementById("video");
  const BPM = Number(q.get("bpm")) || 99.4, BEAT_MS = 60000 / BPM;
  let show = null;                                  // the show's timing, from the Thor
  const SEEK_S = 0.15, NUDGE = 0.03;                // a silent video: past 150 ms, seek; inside, a small rate change
  // a video heard (it carries the music): the rate stays 1, because any change time-stretches the sound,
  // which a Pi plays choppy; a seek only past 400 ms, and at most every 5 s
  const HEARD_SEEK_S = 0.4, HEARD_GAP_MS = 5000;
  const BONE = "#eeecde", DARK = "#0e121c";
  const card = document.getElementById("card");
  const DEVICE_URL = "http://127.0.0.1:8095";        // a stage Pi's own status service (the Tablet lane's image)
  const MAX_PIXELS = 3840 * 2160;                    // a canvas no larger than 4K, whatever the wall
  const BOX_MAX_PIXELS = 1920 * 1080;                // on a stage box, 1080p: a Pi drawing a 4K canvas every
                                                     // frame would starve the video; the screen scales it up
  const SOUND_NAMES = { usb: "USB", hdmi: "HDMI", jack: "3.5 mm jack", bt: "Bluetooth", none: "none" };
  let device = null, lastWsMsg = 0;
  // a screen that stretches its input by STRETCH across: lay out at its true shape, squeeze into the frame
  const STRETCH = Math.min(4, Math.max(0.25, Number(q.get("stretch")) || 1));
  const wall = document.getElementById("wall");
  if (STRETCH !== 1) { wall.style.width = `${100 * STRETCH}vw`; wall.style.transform = `scaleX(${1 / STRETCH})`; }
  const LW = () => innerWidth * STRETCH;               // the page's width as the wall shows it
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
    ws.onopen = () => { backoff = 500; burst(); if (pre.report) pre.report(); };   // a new socket: say it again
    ws.onmessage = (e) => {
      lastWsMsg = performance.now();
      const m = JSON.parse(e.data);
      if ("pong" in m) return onPong(m);
      if (m.type === "show") { show = m; showChanged(); return; }
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

  /* ---- the show's video, on the show's clock ------------------------------------------------------ */
  const manual = !!q.get("video");
  function showChanged() {
    if (!show || show.state === "stopped") {
      if (!manual) { video.pause(); video.hidden = true; video.removeAttribute("src"); }
      return;
    }
    if (!manual && show.state === "loaded") prepare(show);
  }

  /* ---- the show's video: the stage box's own copy first, else streamed with a head start ------------- */
  // A stage box keeps its own copies of the library's videos (its media partition, synced while idle), by
  // content hash: http://127.0.0.1:8095/media/<sha256>?id=<library id> — played from there, no Wi-Fi at all. Else
  // the page streams from the robot, and the GO waits until `buffer_s` (the show's, from projector.json) is
  // buffered ahead of where it starts; it keeps reading ahead while it plays. It reports
  // {"preload": {id, ready, local, buffered_s, need_s}} until the show starts.
  const MIN_START_S = 8;                            // the least head start a streamed video starts on
  let pre = { id: null };
  const localUrl = {};                              // a show's url -> the box's copy, for the show loaded now
  const src = (url) => localUrl[url] || url;
  function ahead(from) {                            // seconds buffered from `from` onward, in one stretch
    const b = video.buffered;
    for (let i = 0; i < b.length; i++) if (b.start(i) <= from + 0.5 && b.end(i) > from) return b.end(i) - from;
    return 0;
  }
  async function prepare(m) {
    if (pre.id === m.id) return;
    const me = pre = { id: m.id, local: false, buffered: 0, need: 0, ready: false, error: null };
    me.report = () => send({ preload: { id: me.id, ready: me.ready, local: me.local, need_s: me.need,
                                        buffered_s: Math.round(me.buffered * 10) / 10, error: me.error || undefined } });
    const first = (m.videos || [])[0];
    if (!first) { me.ready = true; return me.report(); }
    for (const v of m.videos || []) {
      delete localUrl[v.url];
      if (!v.sha256 || !(device && device.device === "stage-pi")) continue;
      const u = `${DEVICE_URL}/media/${v.sha256}?id=${encodeURIComponent(v.lib || "")}`;
      try { if ((await fetch(u, { method: "HEAD", cache: "no-store" })).ok) localUrl[v.url] = u; } catch (e) { /* none */ }
    }
    if (pre !== me) return;
    me.local = !!localUrl[first.url];
    me.need = Math.min(m.buffer_s || 25, Math.max(1, first.end_t - first.t));
    video.dataset.url = first.url; video.loop = false; video.preload = "auto"; video.src = src(first.url);
    video.addEventListener("loadedmetadata", () => { if (video.paused) video.currentTime = first.from_s; }, { once: true });
    video.onerror = () => { if (pre === me) me.error = `the video would not load (${video.error && video.error.code})`; };
    (function tick() {
      if (pre !== me || !show || show.id !== me.id || show.state !== "loaded") return;   // started, or replaced
      me.buffered = ahead(first.from_s);
      const rest = video.duration ? video.duration - first.from_s : Infinity;   // a short video: all of it
      // A paused <video> buffers only so far (Chromium caps it in BYTES: ~14 MB, 14 s of an 8 Mbit/s file,
      // a minute of a 2 Mbit/s one), so the head start is `need` seconds OR, when the browser has stopped
      // loading because it can already play through, whatever it holds — at least MIN_START_S.
      const satisfied = video.readyState === 4 && video.networkState === 1 && me.buffered >= MIN_START_S;
      me.ready = me.local || me.buffered >= Math.min(me.need, rest) - 0.25 || satisfied;
      me.report();
      setTimeout(tick, 500);
    })();
  }
  function showTime(now) {                          // seconds into the show, on the Thor's clock
    return show && show.state === "playing" && clock && show.t0_mono != null ? (now + clock.offset) / 1000 - show.t0_mono : null;
  }
  // each item plays its lead EARLY: the stage's own audio or picture delay (projector.json, set by ear). An
  // item that is HEARD (lead_kind "audio": the video carrying the song, or the song alone) starts by the
  // lead for THIS box's sound output: the robot's by-ear lead for that output (audio_leads_s), else the
  // box's own estimate of that output's delay (device.json audio_latency_ms: Bluetooth ~200 ms) plus the
  // item's own lead_s (stage_audio_lead_s, the stage's general trim). One lead serves picture and sound together: the browser holds the picture
  // back to match its sound output's delay. It is fixed for the show, so a jittery estimate never seeks it.
  let leadFor = { id: null };
  function audioLead(fallback) {
    const out = device && device.audio_output, table = (show && show.audio_leads_s) || {};
    if (out && typeof table[out] === "number") return { s: table[out], from: `set for ${out}` };
    if (device && typeof device.audio_latency_ms === "number")
      return { s: device.audio_latency_ms / 1000 + (fallback || 0), from: "the box's estimate" };
    return { s: fallback || 0, from: "the stage default" };
  }
  function leadOf(v) {
    if (v.lead_kind !== "audio") return v.lead_s || 0;
    if (leadFor.id !== show.id) leadFor = { id: show.id, ...audioLead(v.lead_s) };
    return leadFor.s;
  }
  const at = (v, st) => st + leadOf(v);
  function keepVideo(now) {
    const st = showTime(now);
    if (st == null || manual) return null;
    const it = (show.videos || []).find((v) => v.t <= at(v, st) && at(v, st) < v.end_t);
    if (!it) {                                       // between videos, or after: black (or the last frame held)
      const last = (show.videos || []).filter((v) => v.end_t <= at(v, st)).pop();
      if (!(last && last.end === "hold")) { video.pause(); video.hidden = true; }
      return st;
    }
    if (video.dataset.url !== it.url) { video.dataset.url = it.url; video.src = src(it.url); }   // the box's copy, if it has one
    const want = it.from_s + (at(it, st) - it.t);
    video.hidden = !!it.audio;                       // a song alone: heard, under the page's own scene
    if (show.muted && !video.muted) video.muted = true;     // the tablet took the music after a stall
    if (video.paused) {
      video.currentTime = want; video.muted = !!show.muted;
      video.play().catch(() => { video.muted = true; video.play().catch(() => {}); });   // a browser may refuse sound
      keepVideo.synced = now;
      return st;
    }
    if (video.readyState < 3) {                      // playing but out of data: a stall, told once per show
      keepVideo.dry = keepVideo.dry || now;
      if (now - keepVideo.dry > 1500 && keepVideo.stallFor !== show.id) {
        keepVideo.stallFor = show.id;
        send({ stall: { id: show.id, at_s: Math.round(st * 10) / 10 } });
      }
      return st;                                     // when the data returns, the seek below catches up
    }
    keepVideo.dry = 0;
    const err = video.currentTime - want;
    const heard = !video.muted;
    let rate = heard ? 1 : 1 - Math.max(-NUDGE, Math.min(NUDGE, err * 0.5));
    if (heard ? Math.abs(err) > HEARD_SEEK_S && now - (keepVideo.seekAt || 0) > HEARD_GAP_MS : Math.abs(err) > SEEK_S) {
      video.currentTime = want; rate = 1; keepVideo.seekAt = now; keepVideo.seeks = (keepVideo.seeks || 0) + 1;
    }
    if (video.playbackRate !== rate) video.playbackRate = rate;
    keepVideo.err = err;
    keepVideo.synced = now;
    return st;
  }

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
  function dancer(ms, beatMs) {
    const b = ms / (beatMs || BEAT_MS), ph = b % 1, beat = Math.floor(b) % 4, bounce = Math.abs(Math.sin(Math.PI * ph));
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
  // metres (x right, y up) -> pixels inside the picture R = {x, y, w, h} (the video's letterboxed area, or
  // the whole screen), feet near its bottom. The dancer is centred and 1.75 m tall in it; a live person is
  // scaled to fit and placed where they stand (the camera's centre at the picture's centre), MIRRORED — they
  // see themselves as in a mirror — and kept inside it. Scaled by the picture's height, and never wider
  // than it, so a tall narrow screen still fits the figure.
  function place(J, R, mirror, fit) {
    const W = R.w, H = Math.min(R.h, R.w * 1.2);
    const pts = Object.values(J);
    const minY = Math.min(...pts.map((p) => p[1])), maxY = Math.max(...pts.map((p) => p[1]));
    const midX = (Math.min(...pts.map((p) => p[0])) + Math.max(...pts.map((p) => p[0]))) / 2;
    const s = fit ? (H * 0.78) / Math.max(1.2, maxY - minY + 0.25) : (H * 0.78) / 1.75;
    const sx = mirror ? -1 : 1;
    const off = fit ? Math.max(-W * 0.35, Math.min(W * 0.35, sx * midX * s)) : 0;
    const P = {};
    const y0 = R.y + R.h * 0.95;
    for (const [k, p] of Object.entries(J)) P[k] = [R.x + W / 2 + off + sx * (p[0] - midX) * s, y0 - (p[1] - minY) * s];
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
  function picture(W, H) {                         // where the video's picture lands (object-fit: contain)
    if (video.hidden || !video.videoWidth || !video.videoHeight) return { x: 0, y: 0, w: W, h: H };
    const a = video.videoWidth / video.videoHeight, w = Math.min(W, H * a), h = w / a;
    return { x: (W - w) / 2, y: (H - h) / 2, w, h };
  }
  function draw(now) {
    lastDraw = now;
    const maxPx = device && device.device === "stage-pi" ? BOX_MAX_PIXELS : MAX_PIXELS;
    const k = Math.min(devicePixelRatio || 1, Math.sqrt(maxPx / Math.max(1, LW() * innerHeight)));
    const W = Math.round(LW() * k), H = Math.round(innerHeight * k);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }   // only when the screen changes
    const st = keepVideo(now);                     // first: the show's video kept to the show's clock
    showCard(now);
    if (show && show.state === "loaded" && !manual) {   // armed: black until the show starts
      g.fillStyle = "#000"; g.fillRect(0, 0, W, H);
      return finish(now, st, null);
    }
    // a show's video plays CLEAN unless the show draws the skeletons over it (video: {skeletons: true}, Them
    // Bones): nothing over the picture, and nothing drawn per frame
    if (show && show.state === "playing" && !manual && !video.hidden && !show.skeletons) {
      if (!draw.clean) { g.clearRect(0, 0, W, H); draw.clean = true; }
      return finish(now, st, null);
    }
    draw.clean = false;
    const R = picture(W, H);
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
    const beat = show && show.beat && st != null ? show.beat : null;
    const [DP, ds] = place(beat ? dancer((st - beat.offset_s) * 1000, 60000 / beat.bpm) : dancer(now), R, false, false);
    skeleton(DP, ds, 1 - liveAlpha);
    if (who) { const [LP, ls] = place(who.joints, R, true, true); skeleton(LP, ls, liveAlpha); draw.last = [LP, ls]; }
    else if (draw.last && liveAlpha > 0.01) skeleton(draw.last[0], draw.last[1], liveAlpha);
    if (who && clock && latest.painted !== true) {          // capture -> painted, on the Thor's clock
      latest.painted = true;
      lat.push(now + clock.offset - latest.t * 1000);
      while (lat.length > 120) lat.shift();
    }
    finish(now, st, who);
  }
  function finish(now, st, who) {
    frames++;
    if (now - fpsAt > 1000) { fps = frames * 1000 / (now - fpsAt); frames = 0; fpsAt = now; }
    if (now - lastAlive > 5000) { lastAlive = now; alive(); }
    if (q.get("hud")) {
      const s = [...lat].sort((a, b) => a - b), p = (k) => (s.length ? Math.round(s[Math.floor(k * (s.length - 1))]) : "–");
      hud.textContent = `show ${show ? show.state : "none"}${st != null ? " · " + st.toFixed(1) + " s" : ""}`
        + `${keepVideo.err != null ? " · video " + (keepVideo.err * 1000).toFixed(0) + " ms, seeks " + (keepVideo.seeks || 0) : ""}\n`
        + `feed ${latest ? latest.status || "live" : "none"} · ${latest ? (now - latest.arrived).toFixed(0) + " ms old" : ""}\n`
        + `person ${who ? "yes" : "no"} · ${fps.toFixed(0)} fps · ${cv.width}×${cv.height} · clock ±${clock ? clock.err.toFixed(1) : "?"} ms\n`
        + `latency capture→paint p50 ${p(0.5)} p95 ${p(0.95)} ms (n ${s.length})`;
    }
    requestAnimationFrame(draw);
  }

  /* ---- no screen: the show's media kept in step from a timer, and the device told the page is alive ---- */
  let lastDraw = -1e9;
  setInterval(() => {
    const now = performance.now();
    if (now - lastDraw > 400) {                     // the browser is not drawing (no screen, or hidden)
      keepVideo(now);
      if (device && device.screen_connected === false && now - lastAlive > 5000) { lastAlive = now; alive(); }
    }
  }, 250);
  function alive() {
    try {
      if (window.OpenDubsKiosk?.alive) return window.OpenDubsKiosk.alive();
      if (device && device.device === "stage-pi") fetch(DEVICE_URL + "/alive", { method: "POST", mode: "cors" }).catch(() => {});
    } catch (e) { /* not on a device that watches */ }
  }

  /* ---- the device this page runs on, and what the page reports of itself ------------------------------ */
  async function readDevice() {
    let d = null;
    try {
      if (window.OpenDubsKiosk?.device) d = JSON.parse(window.OpenDubsKiosk.device());
      else {
        const r = await fetch(DEVICE_URL + "/device.json", { mode: "cors", cache: "no-store" });
        if (r.ok) d = await r.json();
      }
    } catch (e) { d = null; }                       // any other browser: nothing to report
    readDevice.misses = d ? 0 : (readDevice.misses || 0) + 1;
    device = d && typeof d === "object" ? {
      device: d.device, name: d.name, model: d.model, audio_output: d.audio_output, audio_name: d.audio_name,
      audio_latency_ms: typeof d.audio_latency_ms === "number" ? d.audio_latency_ms : undefined,
      audio_setting: typeof d.audio_setting === "string" ? d.audio_setting : undefined,
      under_voltage_now: d.under_voltage_now, under_voltage_since_boot: d.under_voltage_since_boot,
      throttled_now: d.throttled_now, screen_connected: d.screen?.connected, screen_mode: d.screen?.mode,
      media_items: d.media?.items, media_bytes: d.media?.bytes, media_free: d.media?.free,
      media_syncing: d.media?.syncing } : null;
    send({ page: { ...(device || {}), viewport: [innerWidth, innerHeight], dpr: devicePixelRatio, stretch: STRETCH,
                   clock_err_ms: clock ? Math.round(clock.err * 10) / 10 : undefined,
                   // the audio lead this page used for the last show it played, and where it came from
                   audio_lead_ms: leadFor.id ? Math.round(leadFor.s * 1000) : undefined,
                   audio_lead_from: leadFor.id ? leadFor.from : undefined,
                   sound: video.muted && !video.paused ? "muted" : "on" } });
  }
  // every 5 s on a device that answers; elsewhere, after 3 misses, once a minute (a refused connection
  // to 127.0.0.1 is logged by the browser each time)
  (function poll() {
    readDevice().finally(() => setTimeout(poll, (readDevice.misses || 0) >= 3 ? 60000 : 5000));
  })();

  /* ---- the STAGE READY card, between shows ---------------------------------------------------------- */
  const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function showCard(now) {
    const between = !show || show.state === "stopped";
    if (!between) showCard.at = 0;                   // redraw at once when the card comes back
    const on = between && q.get("card") !== "0" && !manual;
    card.hidden = !on;
    if (!on || now - (showCard.at || 0) < 1000) return;
    showCard.at = now;
    const up = ws && ws.readyState === 1 && now - lastWsMsg < 15000;
    const sound = !device ? "unknown (not a stage device)"
      : SOUND_NAMES[device.audio_output] || device.audio_output || "unknown";
    const res = `${Math.round(screen.width * devicePixelRatio)}×${Math.round(screen.height * devicePixelRatio)}`
      + (device && device.screen_mode ? ` (${device.screen_mode})` : "")
      + (STRETCH !== 1 ? ` · the screen stretches it ×${STRETCH}: laid out as ${Math.round(screen.width * devicePixelRatio * STRETCH)}`
                         + `×${Math.round(screen.height * devicePixelRatio)}` : "")
      + (device && device.screen_connected === false ? " — NO SCREEN: sound only" : "");
    const rows = [["Robot", up ? "connected" : "NOT connected", !up],
                  ["Screen", res, false],
                  ["Sound", sound + (device && device.audio_name ? ` · ${device.audio_name}` : "")
                    + (device && device.audio_latency_ms != null ? ` · ~${Math.round(device.audio_latency_ms)} ms delay` : ""),
                   device && device.audio_output === "none"],
                  ["Clock to the Thor", clock ? `in step, ±${clock.err.toFixed(1)} ms` : "measuring…", !clock]];
    if (device && device.media_items != null)        // the box's own copies (its media stick)
      rows.push(["Local videos", `${device.media_items} held (${(device.media_bytes / 1e9).toFixed(1)} GB, `
                 + `${(device.media_free / 1e9).toFixed(1)} GB free)${device.media_syncing ? " · syncing" : ""}`, false]);
    const uv = device && (device.under_voltage_now || device.under_voltage_since_boot);
    card.innerHTML = `<b>OpenDubs stage — ready</b>` + rows.map(([k, v, bad]) =>
      `<div class="row${bad ? " bad" : ""}"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")
      + (uv ? `<div class="row bad"><span>Power</span><span>UNDER-VOLTAGE ${device.under_voltage_now ? "now" : "since boot"}`
              + ` — use the proper power supply</span></div>` : "");
  }

  setInterval(() => {                               // report the measured latency to the dashboard
    if (!lat.length) return;
    const s = [...lat].sort((a, b) => a - b);
    send({ latency_ms: { p50: Math.round(s[Math.floor(0.5 * (s.length - 1))]), p95: Math.round(s[Math.floor(0.95 * (s.length - 1))]), n: s.length } });
  }, 2000);

  /* ---- a deploy reloads the page, between shows (as the dashboard's app.js followDeploys) ---------- */
  (function followDeploys() {
    let seen = null;
    async function check() {
      try {
        const v = (await (await fetch("/api/frontend-version", { cache: "no-store" })).json()).version;
        if (seen === null) seen = v;
        else if (v !== seen && (!show || show.state === "stopped")) { location.reload(); return; }
      } catch (e) { /* the dashboard restarting during the deploy: try again next round */ }
      setTimeout(check, 15000);
    }
    check();
  })();
  requestAnimationFrame(draw);
})();
