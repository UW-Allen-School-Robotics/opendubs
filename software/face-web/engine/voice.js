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
// The keyword, as the model's own tokens (sherpa-onnx text2token with its bpe.model), boost 1.5,
// threshold 0.20: the Voice lane's line, tried on the desk with the real model.
const WAKE_KEYWORDS = "▁HE Y ▁DU B S :1.5 #0.20 @HEY_DUBS";
const PRE_ROLL_FRAMES = 75;                         // 1.5 s of 20 ms frames
const WAKE_QUIET_MS = 1000;                         // no wake this long after a reply has played

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
               sentAt: -1e9, opening: false, hits: [] };
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
      release(); closeMic(); stopPlayback();
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
    if (st.holding && st.pressed) { sendFrame(frame); return; }
    if (st.waking) { sendFrame(frame); return; }
    if (!st.wake) return;
    st.buf.push(frame); if (st.buf.length > PRE_ROLL_FRAMES) st.buf.shift();
    if (st.kwsReady && !suppressed()) st.kws.postMessage({ frame });   // a copy: the ring buffer keeps its own
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
      if (e.data.ready) { st.kwsReady = true; return; }
      if (e.data.error) { st.wakeError = e.data.error; return; }
      if (e.data.hit) onWake();
    };
    st.kws.postMessage({ keywords: WAKE_KEYWORDS });
  }
  const suppressed = () => st.convo !== "idle" || st.holding || st.waking || st.play.size > 0 ||
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

  // ---- Talk ----------------------------------------------------------------------------------------
  const press = async () => {
    if (talk.disabled || st.holding) return;
    st.holding = true; st.seq = 0; render();
    try { await openMic(); } catch (e) { st.holding = false; st.fail = `the microphone: ${e.message}`; render(); return; }
    if (!st.holding) return;                         // released while the microphone was opening
    send({ type: "press" }); st.pressed = true;
    touch();
    clearTimeout(st.holdTimer); st.holdTimer = setTimeout(release, st.maxMs);
  };
  function release() {
    clearTimeout(st.holdTimer);
    if (!st.holding) return;
    st.holding = false;
    if (st.pressed) { st.pressed = false; send({ type: "release" }); }   // only after a press it saw
    touch(); render();
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
