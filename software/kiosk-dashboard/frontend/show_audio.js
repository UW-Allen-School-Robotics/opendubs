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
   4. Stop fades (an abort, DEC-6); cut is immediate (an e-stop). Losing the connection stops the music
      and nothing else (DEC-7).

   Optional and detected (rule 12): any browser can hold the role by pressing "Play show audio here";
   the kiosk tablet claims it on start-up when nobody holds it. Web Audio needs no secure context. */
"use strict";
(() => {
  const K = window.OpenDubsKiosk;
  const st = { connected: false, holder: null, youHold: false, state: "idle", loadId: null,
               clock: null, error: null, late_ms: null, last_start: null };
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
    for (const it of buffers) {
      const when = base + it.at, late = c.currentTime - when;
      if (late >= it.buf.duration) continue;                    // long over by the time we heard
      const s = c.createBufferSource(); s.buffer = it.buf; s.connect(gain);
      if (late > 0) s.start(c.currentTime, late); else s.start(when);   // late: join in step, not from 0
      playing.push(s);
    }
    st.state = "playing";
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
      if ("holder" in m) { st.holder = m.holder; st.youHold = !!m.you_hold; if (!st.youHold) silence(0); return; }
      if (m.op === "load") load(m);
      else if (m.op === "start") start(m);
      else if (m.op === "stop") silence(m.fade_s || 0);
      else if (m.op === "cut") silence(0);
    };
    ws.onclose = () => {
      st.connected = false; st.youHold = false; st.holder = null;
      silence(0);                                                // DEC-7: the music stops, nothing else
      setTimeout(connect, retry); retry = Math.min(retry * 2, 8000);
    };
  }
  setInterval(burst, 5000);
  connect();

  window.OpenDubsShowAudio = {
    status: () => ({ ...st, ctx: ctx ? ctx.state : "none" }),
    claim: () => { audio().resume(); send({ claim: true, label: K ? "tablet" : `a browser (${(navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "?"})` }); },
  };
})();
