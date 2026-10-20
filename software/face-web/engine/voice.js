// Talking to the robot from its face: the Talk button, the microphone, the /ws/voice client and the
// reply's playback (notes/workplan-voice.md TL-1 to TL-4). software/voice/wire.py is the whole contract.
//
//   - Press and HOLD Talk to speak; release to finish. A press while the robot is answering cuts the
//     reply (barge-in): `press` covers both. Audio goes out only while Talk is held.
//   - The microphone opens on a press and closes on `conversation_end`, after 20 s with no turn, or when
//     the socket drops. It is NEVER left open.
//   - The REC dot is lit from the microphone track itself (live and enabled), never from a message: a
//     message can be lost or lie; the track cannot.
//   - The button and the dot live in the system strip: plain page elements above the face and any media,
//     never drawn by a skin.
//   - Hidden while a show is running (the service says so) and while the operator's controls are open;
//     greyed, with a short reason, while the service is down, the socket is closed, or voice is off.
//
// THE WAKE WORD, "hey dubs" (the user's decision, 2026-10-19: the face moves with tracking, so nobody
// can press a button on it). Agreed with the Voice lane, which owns the wire:
//   - While voice is on and the service offers it (`ready.wake`), the microphone stays open and a
//     keyword spotter in a Worker (kws_worker.js) listens ON THE TABLET. Nothing leaves the tablet: the
//     last 1.5 s of frames sit in a local ring buffer.
//   - On a hit: `wake {keyword, pre_roll_ms}`, then the buffered frames (seq from 0, the keyword in
//     them), then live frames until the service moves the turn on (state think or idle). The SERVICE
//     ends the utterance (its VAD) and checks the transcript; a false wake ends silently.
//   - No wake while a turn is in progress, a reply is playing, Talk is held, or for 1 s after a reply,
//     so the robot cannot wake itself. During a show voice is off, and so is the microphone.
//   - Two indicators, both from the page's own audio path: a hollow white RING while the microphone is
//     open (listening for the keyword, nothing sent), the red REC dot while audio is actually going out.

const UP_RATE = 16000, FRAME = 320;                 // 20 ms of 16 kHz mono (wire.UP_FRAME_SAMPLES)
const DOWN_RATE = 24000;                            // wire.DOWN_RATE
const JITTER_S = 0.12;                              // the playback's jitter buffer
const MIC_IDLE_MS = 20000;                          // no turn for this long: the microphone closes
const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
// The keyword, as the model's own tokens (sherpa-onnx text2token with its bpe.model), boost 5.0,
// threshold 0.01: tuned for RECALL. The robot heard 0 wakes from several clear "hey dubs" with the
// earlier line (2.0, 0.10; 2026-10-20). Run through this worker on the desk, that line missed "hey dubs"
// said ON ITS OWN in all 5 voices tried (3 macOS, 2 Kokoro) and fired only when more speech followed;
// this one caught all 5, and nothing in 4 near-miss sentences ("hey bob…", "hey there, thumbs up…").
// The Thor's transcript check drops a false hit silently (not_addressed), so a loose line costs little.
// The service may send its own line in `ready.wake.keywords`, so it can be tuned without this page.
const WAKE_KEYWORDS = "▁HE Y ▁DU B S :5.0 #0.01 @HEY_DUBS";
// The self-test: a known "hey dubs" (wake-selftest.wav, 16 kHz mono, made with Kokoro-82M, Apache-2.0)
// fed straight into a second spotter once per page load, bypassing the microphone. Its verdict in the
// health report splits "the spotter or its line is broken on this device" from "the room or the mic".
const SELFTEST_WAV = new URL("./wake-selftest.wav", import.meta.url);
const SELFTEST_READY_MS = 30000, SELFTEST_HIT_MS = 10000;   // loading the model, then decoding the clip
const PRE_ROLL_FRAMES = 75;                         // 1.5 s of 20 ms frames
const WAKE_QUIET_MS = 1000;                         // no wake this long after a reply has played
// Push-to-talk clipped both ends of what people say (the Voice lane, 2026-10-19): the word spoken as the
// finger lands, and the last syllable, since people let go on it. So a press sends the last 300 ms the
// microphone heard (when it was already open), and a release keeps sending 400 ms before `release`.
const TALK_PRE_FRAMES = 15, TALK_TAIL_MS = 400;

// Capture: low-pass at 7 kHz BEFORE resampling to 16 kHz (decimating without it folds ESC whine onto
// the vowels), then 20 ms Int16 frames. From tools/voice-bench/audio_lab.html.
const WORKLET = `
class Cap extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / ${UP_RATE}; this.pos = 0; this.out = new Int16Array(${FRAME}); this.n = 0;
    const taps = 63, fc = 7000 / sampleRate; this.h = new Float32Array(taps); let s = 0;
    for (let i = 0; i < taps; i++) { const m = i - (taps - 1) / 2, w = 0.54 - 0.46 * Math.cos(2 * Math.PI * i / (taps - 1));
      this.h[i] = (m === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * m) / (Math.PI * m)) * w; s += this.h[i]; }
    for (let i = 0; i < taps; i++) this.h[i] /= s; this.z = new Float32Array(taps); this.zi = 0; }
  process(inputs) { const x = inputs[0][0]; if (!x) return true;
    const y = new Float32Array(x.length), h = this.h, z = this.z, T = h.length;
    for (let i = 0; i < x.length; i++) { z[this.zi] = x[i]; let acc = 0, k = this.zi;
      for (let j = 0; j < T; j++) { acc += h[j] * z[k]; k = k === 0 ? T - 1 : k - 1; } y[i] = acc; this.zi = (this.zi + 1) % T; }
    for (; this.pos < y.length; this.pos += this.ratio) { const i = Math.floor(this.pos), f = this.pos - i;
      const v = i + 1 < y.length ? y[i] * (1 - f) + y[i + 1] * f : y[i];
      this.out[this.n++] = Math.max(-32768, Math.min(32767, v * 32767));
      if (this.n === ${FRAME}) { this.port.postMessage(this.out.slice(0)); this.n = 0; } }
    this.pos -= y.length; return true; } }
registerProcessor("opendubs-cap", Cap);`;

export function start({ face }) {
  const strip = document.getElementById("sys"), talk = document.getElementById("talk");
  const rec = document.getElementById("rec"), ring = document.getElementById("ear"), note = document.getElementById("talkNote");
  const op = document.getElementById("op");
  const st = { ws: null, ready: false, on: false, why: "connecting", tries: 0,
               ctx: null, stream: null, node: null, holding: false, pressed: false, seq: 0, maxMs: 15000, holdTimer: null,
               idleTimer: null, convo: "idle", cut: new Set(), play: new Map(), tNext: 0, analyser: null, fail: null,
               wake: null, kws: null, kwsReady: false, buf: [], waking: false, wakeTimer: null, quietUntil: 0,
               sentAt: -1e9, opening: false, hits: [], kwsStats: null,
               meter: { frames: 0, posted: 0, sq: 0, n: 0, peak: 0 } };
  window.OpenDubsVoice = st;                        // for the HUD and a desk check

  // ---- the strip ---------------------------------------------------------------------------------
  const render = () => {
    const show = !/^a show is running/.test(st.why || "") && !(op && op.classList.contains("open"));
    strip.classList.toggle("hidden", !show);
    const usable = st.ready && st.on && !st.fail;
    talk.disabled = !usable;
    talk.textContent = st.holding ? "Listening…" : st.convo === "think" ? "Thinking…" : st.convo === "speak" ? "Speaking…" : "Hold to talk";
    note.textContent = usable ? "" : st.fail || st.why || "voice is not available";
  };
  // Both from the audio path: the ring from the track itself, the dot from frames actually going out.
  const watchRec = () => {
    const t = st.stream && st.stream.getAudioTracks()[0];
    const open = !!(t && t.readyState === "live" && t.enabled);
    const sending = open && performance.now() - st.sentAt < 300;
    rec.classList.toggle("on", sending);
    ring.classList.toggle("on", open && !sending);
  };
  // Listening for the keyword: only while voice is on, the service offers it and the page is showing.
  const wantWake = () => !!(st.ready && st.on && st.wake && !st.fail && document.visibilityState === "visible");
  const keepMic = () => {
    if (wantWake() && !st.stream && !st.opening) openMic().catch((e) => { st.fail = `the microphone: ${e.message}`; });
    else if (!wantWake() && st.stream && !st.holding && !st.waking && !st.idleTimer) closeMic();
  };
  setInterval(() => { keepMic(); watchRec(); render(); }, 250);

  // ---- the socket --------------------------------------------------------------------------------
  const send = (o) => { if (st.ws && st.ws.readyState === WebSocket.OPEN) st.ws.send(JSON.stringify(o)); };
  const connect = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/voice`);
    ws.binaryType = "arraybuffer";
    st.ws = ws;
    ws.onopen = () => { st.tries = 0; send({ type: "hello", protocol: 1, up: { rate: UP_RATE, format: "s16le", channels: 1 } }); };
    ws.onmessage = (e) => { if (typeof e.data === "string") onControl(JSON.parse(e.data)); else onAudio(e.data); };
    ws.onclose = () => {
      st.ws = null; st.ready = false;
      if (!st.fail) st.fail = "the voice link dropped; reconnecting";
      release(); clearTimeout(st.tailTimer); st.pressed = false; closeMic(); stopPlayback();
      setTimeout(connect, BACKOFF_MS[Math.min(st.tries++, BACKOFF_MS.length - 1)]);
    };
  };
  const onControl = (m) => {
    switch (m.type) {
      case "ready":
        st.ready = true; st.fail = null; st.on = !!(m.voice && m.voice.on); st.why = m.voice && m.voice.why;
        if (m.max_utterance_s) st.maxMs = m.max_utterance_s * 1000;
        st.wake = m.wake && m.wake.keyword ? m.wake : null;   // only a service that offers it gets `wake`
        if (st.wake) startSpotter();
        break;
      case "voice": st.on = !!m.on; st.why = m.why; if (!st.on) { release(); stopPlayback(); } break;
      case "state":
        st.convo = m.convo || "idle"; touch();
        if (st.waking && st.convo !== "listen") endWake();   // the service ended the utterance (its VAD)
        break;
      case "turn":                                    // not_addressed: a false wake, ended silently
        touch(); if (st.waking && m.status !== "ok") endWake(); break;
      case "heard": touch(); break;
      case "tts_end": drained(m.turn); break;
      case "stop": cutTurn(m.turn); break;
      case "conversation_end": if (!wantWake()) closeMic(); break;
      case "error": if (m.code === "VOICE_SERVICE_DOWN") st.fail = "the voice service is not running"; else st.fail = m.message || m.code; break;
    }
    render();
  };

  // ---- the microphone ------------------------------------------------------------------------------
  const touch = () => {
    clearTimeout(st.idleTimer);
    st.idleTimer = setTimeout(() => { st.idleTimer = null; if (!wantWake()) closeMic(); }, MIC_IDLE_MS);
  };
  const audioCtx = async () => {
    if (!st.ctx) {
      st.ctx = new AudioContext();
      st.analyser = st.ctx.createAnalyser(); st.analyser.fftSize = 512;
      st.analyser.connect(st.ctx.destination);
    }
    if (st.ctx.state !== "running") await st.ctx.resume();
    return st.ctx;
  };
  let workletAdded = false;
  const openMic = async () => {
    if (st.stream || st.opening) return;
    st.opening = true;
    try {
      const ctx = await audioCtx();
      st.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true,
                                                                      autoGainControl: true, channelCount: 1 } });
      if (!workletAdded) {
        await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
        workletAdded = true;
      }
      st.node = new AudioWorkletNode(ctx, "opendubs-cap");
      ctx.createMediaStreamSource(st.stream).connect(st.node);
      st.node.port.onmessage = (e) => onFrame(e.data);
    } finally { st.opening = false; }
  };
  // Every 20 ms frame: out to the service only during a turn (Talk held, or after a wake); otherwise
  // into the local ring buffer and the keyword spotter, and nowhere else.
  const sendFrame = (frame) => {
    if (!st.ws || st.ws.readyState !== WebSocket.OPEN) return;
    const b = new ArrayBuffer(8 + FRAME * 2), dv = new DataView(b);
    dv.setUint32(0, st.seq++, true); dv.setUint32(4, performance.now() >>> 0, true);
    new Int16Array(b, 8).set(frame);
    st.ws.send(b); st.sentAt = performance.now();
  };
  function onFrame(frame) {
    const mt = st.meter; mt.frames++;
    for (let i = 0; i < frame.length; i++) { const v = frame[i]; mt.sq += v * v; if (v > mt.peak) mt.peak = v; else if (-v > mt.peak) mt.peak = -v; }
    mt.n += frame.length;
    if (st.pressed) { sendFrame(frame); return; }      // a Talk turn, its 400 ms tail included
    if (st.waking) { sendFrame(frame); return; }
    st.buf.push(frame); if (st.buf.length > PRE_ROLL_FRAMES) st.buf.shift();   // local only, until a turn
    if (!st.wake) return;
    if (st.kwsReady && !suppressed()) { st.kws.postMessage({ frame }); st.meter.posted++; }   // a copy: the ring buffer keeps its own
  }
  const closeMic = () => {
    clearTimeout(st.idleTimer); st.idleTimer = null; st.buf = []; endWake();
    if (st.node) { try { st.node.disconnect(); } catch (e) {} st.node = null; }
    if (st.stream) { st.stream.getTracks().forEach((t) => t.stop()); st.stream = null; }
    watchRec();
  };

  // ---- the wake word ------------------------------------------------------------------------------
  function startSpotter() {
    if (st.kws) return;
    st.kws = new Worker(new URL("./kws_worker.js", import.meta.url));
    st.kws.onmessage = (e) => {
      if (e.data.stats) { st.kwsStats = e.data.stats; return; }
      if (e.data.ready) { st.kwsReady = true; return; }
      if (e.data.error) { st.wakeError = e.data.error; return; }
      if (e.data.hit) onWake();
    };
    const line = typeof st.wake.keywords === "string" && st.wake.keywords.includes("@") ? st.wake.keywords : WAKE_KEYWORDS;
    st.kws.postMessage({ keywords: line });
    selfTest(line);
  }
  // A second worker, so the test can neither wake the robot nor disturb the live spotter's state. It
  // reports "hit", "miss", or "error: …", and is gone once it has answered.
  async function selfTest(line) {
    if (st.selftest) return;
    st.selftest = "running";
    let w = null;
    const done = (v) => { st.selftest = v; if (w) w.terminate(); w = null; };
    try {
      const buf = await (await fetch(SELFTEST_WAV)).arrayBuffer();
      const v = new DataView(buf);
      let at = 12;                                             // the RIFF chunks, to "data"
      while (at + 8 <= buf.byteLength && v.getUint32(at, false) !== 0x64617461) at += 8 + v.getUint32(at + 4, true);
      if (at + 8 > buf.byteLength) return done("error: no data chunk in the test clip");
      const pcm = new Int16Array(buf.slice(at + 8, at + 8 + (v.getUint32(at + 4, true) & ~1)));
      const sil = new Int16Array(16000);                       // 1 s of silence on each side, then 1 s more
      const clip = new Int16Array(sil.length * 3 + pcm.length);
      clip.set(pcm, sil.length);
      w = new Worker(new URL("./kws_worker.js", import.meta.url));
      let timer = setTimeout(() => done("error: the test spotter never got ready"), SELFTEST_READY_MS);
      w.onmessage = (e) => {
        const m = e.data;
        if (m.error) { clearTimeout(timer); done("error: " + String(m.error).slice(0, 120)); }
        else if (m.hit) { clearTimeout(timer); done("hit"); }
        else if (m.ready && st.selftest === "running") {
          st.selftest = "fed";
          for (let i = 0; i + 320 <= clip.length; i += 320) w.postMessage({ frame: clip.slice(i, i + 320) });
          clearTimeout(timer); timer = setTimeout(() => done("miss"), SELFTEST_HIT_MS);
        }
      };
      w.postMessage({ keywords: line });
    } catch (e) { done("error: " + String(e && e.message || e).slice(0, 120)); }
  }
  const suppressed = () => st.convo !== "idle" || st.holding || st.pressed || st.waking || st.play.size > 0 ||
                            performance.now() < st.quietUntil || !st.on;
  function onWake() {
    const at = Date.now(), ok = !suppressed() && !!st.ws && st.ws.readyState === WebSocket.OPEN;
    st.hits.push({ at, accepted: ok }); if (st.hits.length > 500) st.hits.shift();
    try { navigator.sendBeacon && navigator.sendBeacon("/api/head/wake_hit", JSON.stringify({ at, accepted: ok })); } catch (e) {}
    if (!ok) return;
    const pre = st.buf; st.buf = [];
    st.seq = 0; st.waking = true;
    send({ type: "wake", keyword: st.wake.keyword, pre_roll_ms: pre.length * 20 });
    for (const f of pre) sendFrame(f);
    touch();
    clearTimeout(st.wakeTimer); st.wakeTimer = setTimeout(endWake, st.maxMs);   // the page's own cap
    render();
  }
  function endWake() {
    clearTimeout(st.wakeTimer);
    if (!st.waking) return;
    st.waking = false;
    if (st.kws) st.kws.postMessage({ reset: true });
  }

  // ---- the listening health report: numbers only, never audio --------------------------------------
  // Every 2 s, what the wake word's path is really doing on this device, for /api/head/wake_hits: the
  // audio context, the microphone track's own settings, frames per second through the worklet and into
  // the spotter, the input level, and the spotter's state. Read with nobody at the bench (2026-10-19:
  // the spotter never fired on the robot though the desk said it should).
  const dbfs = (x) => (x > 0 ? Math.round(20 * Math.log10(x / 32768) * 10) / 10 : null);
  setInterval(() => {
    // A playback entry with nothing playing, 2 s past its scheduled end, is stale (a stray chunk after its
    // turn drained, say). Left alone it would keep the spotter suppressed for good, with no error.
    if (st.ctx) for (const [turn, pl] of [...st.play]) {
      if (!pl.srcs.size && st.ctx.currentTime > pl.end + 2) { st.play.delete(turn); st.staleDropped = (st.staleDropped || 0) + 1; }
    }
    if (!st.wake && !st.stream) return;
    const mt = st.meter, t = st.stream && st.stream.getAudioTracks()[0], set = t ? t.getSettings() : {};
    const report = {
      at: Date.now(), wake_offered: !!st.wake, convo: st.convo, suppressed: suppressed(),
      // each term of suppressed(), so a stuck one shows in one read (the Voice lane's suggestion)
      why_suppressed: { convo: st.convo, playing: st.play.size, holding: st.holding, pressed: st.pressed,
                        waking: st.waking, on: st.on, quiet_ms: Math.max(0, Math.round(st.quietUntil - performance.now())),
                        stale_dropped: st.staleDropped || 0 },
      ctx: st.ctx ? st.ctx.state : null, ctx_rate: st.ctx ? st.ctx.sampleRate : null,
      track: t ? { state: t.readyState, enabled: t.enabled, muted: t.muted, rate: set.sampleRate ?? null,
                   channels: set.channelCount ?? null, ec: set.echoCancellation ?? null,
                   ns: set.noiseSuppression ?? null, agc: set.autoGainControl ?? null } : null,
      frames_per_s: mt.frames / 2, to_spotter_per_s: mt.posted / 2,
      rms_dbfs: mt.n ? dbfs(Math.sqrt(mt.sq / mt.n)) : null, peak_dbfs: dbfs(mt.peak),
      spotter: st.kwsReady ? "ready" : st.wakeError ? "error: " + String(st.wakeError).slice(0, 160) : st.kws ? "loading" : "off",
      spotter_per_s: st.kwsStats, hits: st.hits.length,
      spotter_selftest: st.selftest || null, keywords: st.wake && typeof st.wake.keywords === "string" &&
        st.wake.keywords.includes("@") ? "from the service" : "page default",
    };
    st.meter = { frames: 0, posted: 0, sq: 0, n: 0, peak: 0 };
    st.health = report;
    try { navigator.sendBeacon && navigator.sendBeacon("/api/head/voice_health", JSON.stringify(report)); } catch (e) {}
  }, 2000);

  // ---- Talk ----------------------------------------------------------------------------------------
  const press = async () => {
    if (talk.disabled || st.holding) return;
    finishRelease();                                 // a press inside the last turn's tail ends that turn first
    st.holding = true; st.seq = 0; render();
    try { await openMic(); } catch (e) { st.holding = false; st.fail = `the microphone: ${e.message}`; render(); return; }
    if (!st.holding) return;                         // released while the microphone was opening
    send({ type: "press" }); st.pressed = true;
    const pre = st.buf.slice(-TALK_PRE_FRAMES); st.buf = [];
    for (const f of pre) sendFrame(f);               // what was said as the finger landed
    touch();
    clearTimeout(st.holdTimer); st.holdTimer = setTimeout(release, st.maxMs);
  };
  function release() {
    clearTimeout(st.holdTimer);
    if (!st.holding) return;
    st.holding = false; render();
    if (!st.pressed) return;                         // only after a press the service saw
    clearTimeout(st.tailTimer); st.tailTimer = setTimeout(finishRelease, TALK_TAIL_MS);
  }
  function finishRelease() {
    clearTimeout(st.tailTimer);
    if (!st.pressed) return;
    st.pressed = false; send({ type: "release" }); touch(); render();
  }
  // The button keeps its touches: the face's long-press (operator.js) never sees them.
  for (const ev of ["pointerdown", "pointerup", "pointercancel", "pointermove"]) {
    talk.addEventListener(ev, (e) => e.stopPropagation());
  }
  talk.addEventListener("pointerdown", (e) => { talk.setPointerCapture(e.pointerId); press(); });
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) talk.addEventListener(ev, release);
  talk.addEventListener("contextmenu", (e) => e.preventDefault());

  // ---- playback ------------------------------------------------------------------------------------
  function onAudio(buf) {
    if (buf.byteLength < 8 || !st.ctx) return;
    const dv = new DataView(buf), turn = dv.getUint32(0, true);
    if (st.cut.has(turn)) return;                    // a reply already cut: its late chunks are dropped
    const pcm = new Int16Array(buf, 8, (buf.byteLength - 8) >> 1);
    const ab = st.ctx.createBuffer(1, pcm.length, DOWN_RATE), ch = ab.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const src = st.ctx.createBufferSource(); src.buffer = ab; src.connect(st.analyser);
    if (st.tNext < st.ctx.currentTime + 0.005) st.tNext = st.ctx.currentTime + JITTER_S;
    src.start(st.tNext); st.tNext += ab.duration;
    const p = st.play.get(turn) || { srcs: new Set(), end: 0, done: false };
    p.srcs.add(src); p.end = st.tNext; st.play.set(turn, p);
    src.onended = () => { p.srcs.delete(src); if (p.done && !p.srcs.size) finished(turn); };
  }
  function drained(turn) {
    const p = st.play.get(turn);
    if (!p || !p.srcs.size) finished(turn); else p.done = true;
  }
  function finished(turn) {
    if (st.play.delete(turn) || !st.cut.has(turn)) send({ type: "played", turn });
    st.quietUntil = performance.now() + WAKE_QUIET_MS;          // the room's tail of the reply
    if (st.kws) st.kws.postMessage({ reset: true });
  }
  function cutTurn(turn) {
    st.cut.add(turn); if (st.cut.size > 32) st.cut.delete(st.cut.values().next().value);
    const p = st.play.get(turn);
    if (p) { p.srcs.forEach((s) => { try { s.stop(); } catch (e) {} }); st.play.delete(turn); }
  }
  function stopPlayback() { for (const t of [...st.play.keys()]) cutTurn(t); }

  // ---- the mouth, from the audio actually playing --------------------------------------------------
  const env = new Float32Array(512);
  setInterval(() => {
    if (!st.analyser || typeof face.setMouthLevel !== "function") return;
    if (!st.play.size) { face.setMouthLevel(0); return; }
    st.analyser.getFloatTimeDomainData(env);
    let s = 0; for (let i = 0; i < env.length; i++) s += env[i] * env[i];
    face.setMouthLevel(Math.min(1, Math.sqrt(s / env.length) * 4));
  }, 33);

  render();
  connect();
}
