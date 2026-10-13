/* OpenDubs dashboard — show audio (choreography plan 4.2).

   The show runner keeps the show clock on the Thor; the song plays here, on the ONE device holding the
   show-audio role (standing rule 12). This runs on every page of the dashboard, not only Shows,
   because the tablet holds the role whatever it is showing.

   1. Clock. Ping bursts over /ws/show-audio, NTP-style: offset θ = ((t1 − t0) + (t2 − t3)) / 2,
      keeping the sample with the smallest round trip (its error is at most half of it) —
      notes/design/choreo-teach-and-sync.md, "The handshake".
   2. Load. The runner names the show's files; this page downloads and decodes all of them, then says
      "ready" (decided: whole files, started on cue — never streamed).
   3. Start. The runner names show time 0 as a Thor monotonic time. It becomes this page's time
      (− θ), then audio-hardware time through getOutputTimestamp(), which already includes the output
      latency where the platform reports it truthfully (experiment S2 measures whether it does).
   4. Keep in step. Measured on the tablet (S1/S3, 2026-10-08): the Thor's and the tablet's clocks agree
      within 0.1 ppm and the audio clock runs true when the tablet is left alone — but handling it (a
      cable pulled, the tablet picked up) can stall the audio clock by 10–70 ms, which would leave the
      music behind for the rest of the show. So, once a second while playing, the show time being HEARD
      is compared with the show time the Thor's clock says it is, and any difference is removed by a
      slight speed change (at most 1 %, which is inaudible) — or, if it is large, by restarting in step.
   5. Stop fades (an abort, DEC-6); cut is immediate (an e-stop). Losing the connection stops the music
      and nothing else (DEC-7).

   Optional and detected (rule 12): any browser can hold the role by pressing "Play show audio here";
   the kiosk tablet claims it on start-up when nobody holds it. Web Audio needs no secure context.

   6. AMBIENT MUSIC (the "Head & face" page's API, /api/choreo/ambient): library songs played here
      while no show runs — one <audio> element made in script (no page markup, so the face page can
      include this file as it is), the list looped (shuffled if asked), 1 s fades. A show TAKES OVER:
      ambient fades out on the runner's `load` and resumes, from the next song, when the show ends
      (`stop` or `cut`). What plays, and whether the browser blocked starting it, is reported to the
      hub; claim() (a tap) retries a blocked start. */
"use strict";
(() => {
  const K = window.OpenDubsKiosk;
  const st = { connected: false, holder: null, youHold: false, state: "idle", loadId: null,
               clock: null, error: null, late_ms: null, last_start: null,
               sync: { err_ms: null, rate: 1, corrections: 0, resyncs: 0 } };
  let ws = null, ctx = null, buffers = [], playing = [], gain = null, retry = 500;
  const samples = [];                        // {theta, delta, at}
  const pending = new Map();                 // ping t0 -> true

  const audio = () => {
    if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
    return ctx;
  };
  const send = (m) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(m)); };

  /* ---- 1. the Thor's clock ------------------------------------------------------------------ */
  function burst() {
    for (let i = 0; i < 8; i++) setTimeout(() => {
      const t0 = performance.now(); pending.set(t0, true); send({ ping: t0 });
    }, i * 40);
  }
  function onPong(m) {
    const t3 = performance.now();
    if (!pending.delete(m.pong)) return;
    const t0 = m.pong, delta = (t3 - t0) - (m.t2 - m.t1);
    samples.push({ theta: ((m.t1 - t0) + (m.t2 - t3)) / 2, delta, at: t3 });
    while (samples.length && t3 - samples[0].at > 20000) samples.shift();   // the last ~20 s
    const best = samples.reduce((a, b) => (b.delta < a.delta ? b : a));
    st.clock = { offset_ms: best.theta, error_ms: best.delta / 2 };
  }
  // Thor monotonic ms -> this page's performance.now() ms
  const toPerf = (thorMs) => thorMs - st.clock.offset_ms;

  /* performance.now() ms -> AudioContext seconds, for a sample to be HEARD at that moment */
  function ctxAt(perfMs) {
    const c = audio();
    if (c.getOutputTimestamp) {
      const ts = c.getOutputTimestamp();
      if (ts.performanceTime > 0) return ts.contextTime + (perfMs - ts.performanceTime) / 1000;
    }
    return c.currentTime + (perfMs - performance.now()) / 1000 - (c.outputLatency || 0) - (c.baseLatency || 0);
  }

  /* ---- 2. load, 3. start, 4. stop ----------------------------------------------------------- */
  function silence(fadeS) {
    if (play) { clearInterval(play.timer); play = null; }
    const c = ctx, srcs = playing; playing = [];
    if (!c || !srcs.length) { st.state = buffers.length ? "ready" : "idle"; return; }
    const end = c.currentTime + (fadeS || 0);
    if (fadeS && gain) { gain.gain.setValueAtTime(gain.gain.value, c.currentTime); gain.gain.linearRampToValueAtTime(0, end); }
    for (const s of srcs) { try { s.stop(end); } catch (e) { /* not started */ } }
    st.state = "idle";
  }

  async function load(m) {
    silence(0);
    st.loadId = m.id; st.state = "loading"; st.error = null; buffers = [];
    try {
      const got = await Promise.all((m.items || []).map(async (it) => {
        const r = await fetch(it.url);
        if (!r.ok) throw new Error(`${it.url}: HTTP ${r.status}`);
        return { ...it, buf: await audio().decodeAudioData(await r.arrayBuffer()) };
      }));
      if (st.loadId !== m.id) return;          // superseded while downloading
      buffers = got; st.state = "ready"; send({ ready: m.id });
    } catch (e) {
      st.state = "error"; st.error = String(e.message || e);   // the runner times out and runs silently
    }
  }

  /* What is heard is a straight line in context time between corrections: show time `anchorShow` at
     context time `anchorCtx`, advancing at `rate`. */
  let play = null;                   // {m, anchorCtx, anchorShow, rate, timer}
  const SYNC_EVERY_MS = 1000;        // how often to check
  const DEADBAND_S = 0.005;          // closer than this is in step
  const CORRECT_OVER_S = 3;          // remove an error over about this long...
  const MAX_RATE_DEV = 0.01;         // ...never faster or slower than 1 % (inaudible)
  const RESYNC_S = 0.25;             // further off than this: restart in step instead
  const heard = (ctxT) => play.anchorShow + (ctxT - play.anchorCtx) * play.rate;

  // Every item from show time `showT`, heard from context time `atCtx`, at speed 1.
  function startSources(c, atCtx, showT) {
    for (const it of buffers) {
      const off = showT - it.at;
      if (off >= it.buf.duration) continue;                       // long over
      const s = c.createBufferSource(); s.buffer = it.buf; s.connect(gain);
      if (off >= 0) s.start(atCtx, off); else s.start(atCtx - off);   // already due: join in step
      playing.push(s);
    }
  }

  function start(m) {
    if (m.id !== st.loadId || st.state !== "ready" || !st.clock) return;
    const c = audio(); c.resume();
    gain = c.createGain(); gain.connect(c.destination);
    const perfT0 = toPerf(m.t0_mono * 1000), base = ctxAt(perfT0);
    st.late_ms = Math.max(0, (c.currentTime - base) * 1000);
    // Kept for the sync experiments (S1–S3): what was asked, and what this page made of it.
    st.last_start = { t0_mono: m.t0_mono, perf_t0: perfT0, ctx_t0: base, perf_now: performance.now(),
                      ctx_now: c.currentTime, clock: { ...st.clock },
                      latency_s: (c.outputLatency || 0) + (c.baseLatency || 0),
                      via: c.getOutputTimestamp && c.getOutputTimestamp().performanceTime > 0 ? "getOutputTimestamp" : "latency" };
    const now = Math.max(c.currentTime, base);                   // late: join in step, not from 0
    play = { m, anchorCtx: now, anchorShow: now - base, rate: 1, timer: null };
    startSources(c, now, now - base);
    st.sync = { err_ms: 0, rate: 1, corrections: 0, resyncs: 0 };
    play.timer = setInterval(keepInStep, SYNC_EVERY_MS);
    st.state = "playing";
  }

  function keepInStep() {
    const c = ctx;
    if (!play || st.state !== "playing" || !c || !c.getOutputTimestamp || !st.clock) return;
    const ts = c.getOutputTimestamp();
    if (!(ts.performanceTime > 0)) return;
    // Where the Thor's clock says the show is, at the moment the sample now leaving the speaker is heard.
    const t0perf = toPerf(play.m.t0_mono * 1000);
    const err = (ts.performanceTime - t0perf) / 1000 - heard(ts.contextTime);   // > 0: the music is behind
    st.sync.err_ms = Math.round(err * 1000);
    const now = c.currentTime;
    if (Math.abs(err) > RESYNC_S) {
      // Too far to ease back without being heard: restart everything in step, a moment from now.
      const at = now + 0.05;
      const showAt = (ts.performanceTime + (at - ts.contextTime) * 1000 - t0perf) / 1000;
      for (const s of playing) { try { s.stop(now); } catch (e) { /* not started */ } }
      playing = [];
      play.anchorCtx = at; play.anchorShow = showAt; play.rate = 1;
      startSources(c, at, showAt);
      st.sync.rate = 1; st.sync.resyncs++;
      return;
    }
    const want = Math.abs(err) < DEADBAND_S ? 1
      : 1 + Math.max(-MAX_RATE_DEV, Math.min(MAX_RATE_DEV, err / CORRECT_OVER_S));
    if (want === play.rate) return;
    play.anchorShow = heard(now); play.anchorCtx = now; play.rate = want;   // re-anchor, then change speed
    for (const s of playing) { try { s.playbackRate.setValueAtTime(want, now); } catch (e) { /* ended */ } }
    st.sync.rate = Math.round(want * 10000) / 10000;
    if (want !== 1) st.sync.corrections++;
  }

  /* ---- 6. ambient music ---------------------------------------------------------------------------- */
  const amb = { msg: null, el: null, order: [], idx: -1, show: false, blocked: false, now: null, fade: null };
  const AMB_FADE_S = 1.0;
  const ambReport = () => send({ ambient: { now: amb.now, blocked: amb.blocked } });
  function ambEl() {
    if (!amb.el) {
      amb.el = new Audio();
      amb.el.preload = "auto";
      amb.el.addEventListener("ended", () => ambNext());
      amb.el.addEventListener("error", () => ambNext());       // a bad file: skip it, never stall
    }
    return amb.el;
  }
  function ambFade(to, then) {
    const el = ambEl(), from = el.volume, steps = 20;
    clearInterval(amb.fade);
    let k = 0;
    amb.fade = setInterval(() => {
      k++; el.volume = Math.max(0, Math.min(1, from + (to - from) * k / steps));
      if (k >= steps) { clearInterval(amb.fade); amb.fade = null; if (then) then(); }
    }, AMB_FADE_S * 1000 / steps);
  }
  const ambWanted = () => !!(amb.msg && amb.msg.playing && amb.msg.items && amb.msg.items.length
                             && !amb.show && st.youHold);
  function ambOrder() {
    const ids = amb.msg.items.map((x, i) => i);
    if (amb.msg.shuffle) for (let i = ids.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1)); [ids[i], ids[j]] = [ids[j], ids[i]];
    }
    amb.order = ids; amb.idx = -1;
  }
  function ambPlayCurrent() {
    const el = ambEl(), it = amb.msg.items[amb.order[amb.idx]];
    el.src = it.url; el.volume = 0; amb.now = it.id;
    const p = el.play();
    if (p && p.then) p.then(() => { amb.blocked = false; ambReport(); ambFade(amb.msg.volume); })
      .catch((e) => { amb.blocked = e && e.name === "NotAllowedError"; amb.now = null; ambReport(); });
    else ambFade(amb.msg.volume);
    ambReport();
  }
  function ambNext() {
    if (!ambWanted()) return ambStop();
    if (!amb.order.length || amb.idx + 1 >= amb.order.length) ambOrder();     // loop the list
    amb.idx++;
    ambPlayCurrent();
  }
  function ambStop() {
    if (!amb.el || amb.el.paused) { if (amb.now) { amb.now = null; ambReport(); } return; }
    ambFade(0, () => { amb.el.pause(); amb.now = null; ambReport(); });
  }
  function ambApply(next) {          // bring what plays in line with what is wanted
    if (!ambWanted()) return ambStop();
    if (next || !amb.el || amb.el.paused || !amb.now) return ambNext();
    ambFade(amb.msg.volume);                                     // a volume change while playing
  }
  function onAmbient(m) {
    const listChanged = !amb.msg || JSON.stringify(amb.msg.items) !== JSON.stringify(m.items)
                        || amb.msg.shuffle !== m.shuffle;
    amb.msg = m; amb.show = !!m.show_active;
    if (listChanged) amb.order = [];
    ambApply(!!m.next || (listChanged && m.playing));
  }

  /* ---- the connection ------------------------------------------------------------------------ */
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/show-audio`);
    ws.onopen = () => {
      st.connected = true; retry = 500; burst();
      if (K) send({ claim: true, label: "tablet", only_if_free: true });
    };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if ("pong" in m) return onPong(m);
      if ("holder" in m) {
        st.holder = m.holder; st.youHold = !!m.you_hold;
        if (!st.youHold) { silence(0); ambStop(); }
        return;
      }
      if (m.op === "ambient") return onAmbient(m);
      if (m.op === "load") { amb.show = true; ambStop(); load(m); }            // a show takes over
      else if (m.op === "start") start(m);
      else if (m.op === "stop") { silence(m.fade_s || 0); amb.show = false; setTimeout(() => ambApply(true), (m.fade_s || 0) * 1000 + 500); }
      else if (m.op === "cut") { silence(0); amb.show = false; ambApply(true); }
    };
    ws.onclose = () => {
      st.connected = false; st.youHold = false; st.holder = null;
      silence(0); ambStop();                                     // DEC-7: the music stops, nothing else
      setTimeout(connect, retry); retry = Math.min(retry * 2, 8000);
    };
  }
  setInterval(burst, 5000);
  connect();

  window.OpenDubsShowAudio = {
    status: () => ({ ...st, ctx: ctx ? ctx.state : "none",
                     ambient: { playing: !!(amb.el && !amb.el.paused), now: amb.now, blocked: amb.blocked, show: amb.show } }),
    claim: () => { audio().resume(); if (amb.blocked) { amb.blocked = false; ambApply(true); } send({ claim: true, label: K ? "tablet" : `a browser (${(navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "?"})` }); },
  };
})();
