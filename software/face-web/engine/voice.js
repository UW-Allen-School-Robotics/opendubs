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
// THE WAKE PHRASE, "Hey OpenDubs", spoken "hey open dubs" (the user's decision, 2026-10-20; it was "hey
// dubs" from 2026-10-19: the face moves with tracking, so nobody can press a button on it). Agreed with
// the Voice lane, which owns the wire:
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
// The phrase as the model's own tokens ("HEY OPEN DUBS" through sherpa-onnx text2token and its bpe.model;
// sentencepiece agrees, and all eight are in tokens.txt), boost and threshold from the desk grid below,
// tuned for RECALL: the Thor's transcript check drops a false hit silently (not_addressed), so a loose
// line costs little. The short "hey dubs" is deliberately NOT a line: the page must not fire on it (the
// Thor still accepts a transcript reading "hey dubs", for when the recogniser drops "open").
// Desk grid (2026-10-20), this worker with its gain stage, synthetic "hey open dubs" from 5 voices (3
// macOS, 2 Kokoro) at -6/-24/-36 dBFS peak, clean and under -60 dBFS RMS pink noise; 28 near misses
// ("open the door", "hey, open the window", "hey dubs", "hey, open the door please", "they opened the
// tubs yesterday", "hey bob…", at two levels, clean and noisy): boost 4.0, threshold 0.02 caught 29 of 30
// with no false hit, the same as 5.0/0.01; 5.0/0.03 caught 27, 5.0/0.05 24. So the stricter of the two.
// The service may send its own line in `ready.wake.keywords`, so it can be tuned without this page.
const WAKE_KEYWORDS = "▁HE Y ▁O P EN ▁DU B S :4.0 #0.02 @HEY_OPENDUBS";
// The self-test: a known "hey open dubs" (wake-selftest.wav, 16 kHz mono, made with Kokoro-82M, Apache-2.0)
// fed straight into a second spotter once per page load, bypassing the microphone. Its verdict in the
// health report splits "the spotter or its line is broken on this device" from "the room or the mic".
const SELFTEST_WAV = new URL("./wake-selftest.wav", import.meta.url);
// A spotter not ready this long after it started is started again, with its files fetched afresh: on
// 2026-10-20 a deploy restarted the dashboard while the tablet was downloading the model, and the spotter
// sat at "loading" for 8 minutes with nothing to say why. After the last try it reports an error.
const SPOTTER_READY_MS = 60000, SPOTTER_TRIES = 4, SPOTTER_RETRY_MS = 5000;
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
        // The face's conversation looks (Face & HRI's face.js). The page owns WHEN; the engine owns
        // what it looks like, so this stays one line and no appearance is decided here.
        if (typeof face.setConvo === "function") face.setConvo(st.convo);
        if (st.waking && st.convo !== "listen") endWake();   // the service ended the utterance (its VAD)
        break;
      case "turn":                                    // not_addressed: a false wake, ended silently
        touch(); if (st.waking && m.status !== "ok") endWake();
        // A turn that did not land gets a brief "didn't catch that" on the face, so the spoken
        // "say that again" has an expression to go with it. The ENGINE decides which statuses
        // deserve one -- `not_addressed` and `cut` are silent by the wire contract, and a false
        // wake must not make the face twitch -- so every status is passed and none is filtered
        // here, which keeps that judgement in one place instead of two.
        if (typeof face.convoReact === "function") face.convoReact(m.status);
        break;
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
      // The platform's voice processing, ON unless the Thor has switched it off for an A/B (face_door.py
      // /api/head/mic_processing): the track Talk and the spotter share.
      // And which microphone: the default unless the Thor has chosen one (/api/head/mic_device), by the
      // start of its device id or its label; one the tablet does not list falls back to the default.
      const proc = !st.procOff, audio = { echoCancellation: proc, noiseSuppression: proc, autoGainControl: proc, channelCount: 1 };
      st.micDeviceError = null;
      if (st.micWant && st.micWant !== "default") {
        const ins = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
        const pick = ins.find((d) => d.deviceId.startsWith(st.micWant)) || ins.find((d) => d.label === st.micWant);
        if (pick) audio.deviceId = { exact: pick.deviceId };
        else st.micDeviceError = `no input "${st.micWant}" on this tablet; using the default`;
      }
      st.stream = await navigator.mediaDevices.getUserMedia({ audio });
      if (!workletAdded) {
        await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
        workletAdded = true;
      }
      // Each opening has its own source and node, and closeMic() unhooks both. On 2026-10-20 it unhooked
      // only the node's OUTPUT: the old source kept feeding the old node, whose frames kept arriving, so
      // every reconnect of the voice link (each deploy) added another stream of frames. The spotter, Talk
      // and the capture got them interleaved: 100 frames/s after one reconnect, 150 after two, and audio
      // nothing could understand. A frame from a node that is no longer current is dropped as well.
      const node = st.node = new AudioWorkletNode(ctx, "opendubs-cap");
      st.src = ctx.createMediaStreamSource(st.stream);
      st.src.connect(node);
      st.nodes = (st.nodes || 0) + 1;
      node.port.onmessage = (e) => { if (st.node === node) onFrame(e.data); else st.stale = (st.stale || 0) + 1; };
      describeMic();
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
    bands(frame);
    if (st.pressed) { sendFrame(frame); return; }      // a Talk turn, its 400 ms tail included
    if (st.waking) { sendFrame(frame); return; }
    st.buf.push(frame); if (st.buf.length > PRE_ROLL_FRAMES) st.buf.shift();   // local only, until a turn
    if (!st.wake) return;
    if (st.cap) captureFrame(frame);
    if (st.kwsReady && !suppressed()) { st.kws.postMessage({ frame }); st.meter.posted++; }   // a copy: the ring buffer keeps its own
  }
  // One clip of the microphone path's frames (the spotter's input, whether or not it was listening at
  // that moment), only while the Thor has armed it (face_door.py /api/head/capture): a FIXED window of
  // CAP_MS of wall time from the moment the page sees it armed, with no level trigger, so the person is
  // cued at the arm. (The first version started on the first frame over -36 dBFS: on 2026-10-20 it fired
  // at once on something else and, with frames doubled, closed after 1.3 s.) Sent once to the Thor's
  // /tmp with how many frames arrived in how long; never kept here, never sent anywhere else.
  const CAP_MS = 5000;
  function captureFrame(frame) {
    const c = st.cap;
    c.frames.push(frame);
    const ms = performance.now() - c.t0;
    if (ms < CAP_MS) return;
    const pcm = new Int16Array(c.frames.length * frame.length);
    c.frames.forEach((f, i) => pcm.set(f, i * frame.length));
    st.cap = null; st.capSent = (st.capSent || 0) + 1;
    const meta = { frames: c.frames.length, ms: Math.round(ms), ctx_rate: st.ctx ? st.ctx.sampleRate : null, mic: st.mic || null,
                   suppressed_at_start: c.suppressed, mic_opened: st.nodes || 0 };
    fetch("/api/head/capture/clip", { method: "POST", body: pcm.buffer,
                                      headers: { "X-Capture-Meta": JSON.stringify(meta).slice(0, 1000) } }).catch(() => {});
  }
  setInterval(async () => {
    if (!st.kwsReady) return;
    try {
      const cap = await (await fetch("/api/head/capture", { cache: "no-store" })).json();
      const armed = !!cap.armed;
      // The A/B: a change of processing reopens the microphone (keepMic opens it again within 250 ms).
      const off = cap.mic_processing === "off";
      const dev = typeof cap.mic_device === "string" ? cap.mic_device : "default";
      if (off !== !!st.procOff || dev !== (st.micWant || "default")) {
        st.procOff = off; st.micWant = dev;
        if (st.stream && !st.holding && !st.waking) closeMic();
      }
      const g = +cap.spotter_gain_max_db;
      if (g && g !== st.gainMaxDb && st.kws) { st.gainMaxDb = g; st.kws.postMessage({ gainMaxDb: g }); }
      if (armed && !st.cap) st.cap = { frames: [], t0: performance.now(), suppressed: suppressed() };
      if (!armed) st.cap = null;
    } catch (e) { st.cap = null; }
  }, 2000);
  // Where the energy is, without recording anything: per 2 s, the mean level in a few bands from 512-point
  // spectra of the microphone frames. Below 300 Hz, 300 Hz-3 kHz (where a voice's words are), 3-8 kHz, and
  // two narrow bands round the steady tones in the one capture (0.84 and 1.5 kHz), plus the strongest bin.
  // dB relative to a full-scale sine, so the bands compare with each other, not with dBFS RMS exactly.
  const N = 512, HANN = Float32Array.from({ length: N }, (_, i) => 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (N - 1)));
  const BANDS = { below_300: [0, 300], speech_300_3k: [300, 3000], high_3k_8k: [3000, 8000],
                  tone_840: [790, 890], tone_1500: [1450, 1550] };
  const spec = { buf: new Float32Array(N), n: 0, sums: null, frames: 0, peak: new Float64Array(N / 2) };
  function fft(re, im) {                                     // in place, radix 2
    for (let i = 1, j = 0; i < N; i++) { let b = N >> 1; for (; j & b; b >>= 1) j ^= b; j ^= b;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; } }
    for (let len = 2; len <= N; len <<= 1) {
      const a = -2 * Math.PI / len, wr = Math.cos(a), wi = Math.sin(a);
      for (let i = 0; i < N; i += len) { let cr = 1, ci = 0;
        for (let k = 0; k < len / 2; k++) { const p = i + k, q = p + len / 2;
          const tr = re[q] * cr - im[q] * ci, ti = re[q] * ci + im[q] * cr;
          re[q] = re[p] - tr; im[q] = im[p] - ti; re[p] += tr; im[p] += ti;
          const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t; } } }
  }
  function bands(frame) {
    for (let i = 0; i < frame.length; i++) {
      spec.buf[spec.n++] = frame[i] / 32768;
      if (spec.n < N) continue;
      spec.n = 0;
      const re = Float64Array.from(spec.buf, (v, k) => v * HANN[k]), im = new Float64Array(N);
      fft(re, im);
      spec.sums = spec.sums || Object.fromEntries(Object.keys(BANDS).map((k) => [k, 0]));
      for (let b = 1; b < N / 2; b++) {
        // Hann: a full-scale sine's peak bin is N/4, and its energy spreads over 1.5 bins' worth (ENBW),
        // so a tone of peak A reads 20 log10 A dB in its band.
        const pw = (re[b] * re[b] + im[b] * im[b]) / (N * N / 16 * 1.5), hz = b * 16000 / N;
        spec.peak[b] += pw;
        for (const [k, [lo, hi]] of Object.entries(BANDS)) if (hz >= lo && hz < hi) spec.sums[k] += pw;
      }
      spec.frames++;
    }
  }
  function bandReport() {
    if (!spec.frames) return null;
    const out = {};
    for (const [k, v] of Object.entries(spec.sums)) out[k] = v > 0 ? Math.round(10 * Math.log10(v / spec.frames) * 10) / 10 : null;
    let best = 1; for (let b = 2; b < N / 2; b++) if (spec.peak[b] > spec.peak[best]) best = b;
    out.strongest_hz = Math.round(best * 16000 / N);
    spec.sums = null; spec.frames = 0; spec.peak.fill(0);
    return out;
  }
  // Which microphone the page was given, and what else there is: for "why is it so quiet?" from the Thor.
  // Labels and Chrome's per-origin device ids (shortened) only.
  async function describeMic() {
    try {
      const t = st.stream && st.stream.getAudioTracks()[0], set = t ? t.getSettings() : {};
      const ins = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
      st.mic = { label: t ? t.label : null, device: (set.deviceId || "").slice(0, 8) || null,
                 asked: st.micWant || "default", device_error: st.micDeviceError || null,
                 inputs: ins.map((d) => ({ label: d.label, device: (d.deviceId || "").slice(0, 8) })) };
    } catch (e) { st.mic = { error: String(e && e.message || e).slice(0, 120) }; }
  }
  const closeMic = () => {
    clearTimeout(st.idleTimer); st.idleTimer = null; st.buf = []; endWake();
    if (st.src) { try { st.src.disconnect(); } catch (e) {} st.src = null; }
    if (st.node) { try { st.node.port.onmessage = null; st.node.disconnect(); } catch (e) {} st.node = null; }
    if (st.stream) { st.stream.getTracks().forEach((t) => t.stop()); st.stream = null; }
    watchRec();
  };

  // ---- the wake word ------------------------------------------------------------------------------
  function startSpotter() {
    if (st.kws) return;
    const attempt = st.kwsTries = (st.kwsTries || 0) + 1;
    // The try number rides on the URL, so a retry never reuses a file the browser kept from a cut transfer.
    const w = st.kws = new Worker(new URL(`./kws_worker.js?try=${attempt}`, import.meta.url));
    const line = typeof st.wake.keywords === "string" && st.wake.keywords.includes("@") ? st.wake.keywords : WAKE_KEYWORDS;
    const retry = (why) => {
      clearTimeout(st.kwsTimer);
      if (st.kws !== w) return;
      w.terminate(); st.kws = null; st.kwsStats = null;
      st.kwsRetryWhy = `try ${attempt}: ${why}`.slice(0, 160);
      if (attempt < SPOTTER_TRIES) setTimeout(() => { if (st.wake) startSpotter(); }, SPOTTER_RETRY_MS);
      else st.wakeError = `not ready after ${attempt} tries (${why})`;
    };
    st.kwsTimer = setTimeout(() => retry(`not ready after ${SPOTTER_READY_MS / 1000} s`), SPOTTER_READY_MS);
    w.onmessage = (e) => {
      if (st.kws !== w) return;
      if (e.data.stats) { st.kwsStats = e.data.stats; return; }
      if (e.data.ready) {
        clearTimeout(st.kwsTimer); st.kwsReady = true; st.wakeError = null;
        if (st.gainMaxDb) w.postMessage({ gainMaxDb: st.gainMaxDb });          // a restarted spotter keeps it
        selfTest(line); return;
      }
      if (e.data.error) { retry(String(e.data.error)); return; }
      if (e.data.hit) onWake();
    };
    w.postMessage({ keywords: line });
  }
  // A second worker, so the test can neither wake the robot nor disturb the live spotter's state. It
  // reports "hit", "miss", or "error: …", and is gone once it has answered. It starts once the live
  // spotter is ready, so its files come from the cache rather than a second download beside the first.
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
      w = new Worker(new URL(`./kws_worker.js?try=${st.kwsTries || 1}`, import.meta.url));   // the files that loaded
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
      mic: st.mic || null, mic_opened: st.nodes || 0, stale_frames: st.stale || 0,
      spotter_gain_max_db: st.gainMaxDb || 24,
      rms_dbfs: mt.n ? dbfs(Math.sqrt(mt.sq / mt.n)) : null, peak_dbfs: dbfs(mt.peak),
      bands_db: bandReport(), mic_processing: st.procOff ? "off" : "on",
      spotter: st.kwsReady ? "ready" : st.wakeError ? "error: " + String(st.wakeError).slice(0, 160) : st.kws ? "loading" : "off",
      spotter_tries: st.kwsTries || 0, spotter_retried: st.kwsRetryWhy || null,
      spotter_per_s: st.kwsStats, hits: st.hits.length, capture: st.cap ? "recording" : null,
      captures_sent: st.capSent || 0,
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
    // THE FACE ANSWERS THE FINGER, not the round trip: a press is unambiguously listening, and
    // waiting for the service's `state` would leave the face blank for the latency. Only from
    // idle, so it cannot interrupt a turn already in flight.
    if (st.convo === "idle" && typeof face.setConvo === "function") face.setConvo("listen");
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
    // Back to the SERVICE'S word rather than to idle: if it has moved the turn on, the face follows
    // it, and if it never answered at all the face returns to idle instead of sticking on listen.
    if (typeof face.setConvo === "function") face.setConvo(st.convo);
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
