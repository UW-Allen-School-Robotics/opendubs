// The wake-word spotter, off the page's main thread: sherpa-onnx's keyword spotter, built for the browser
// with its English model baked in (kws/build-kws.sh). It is fed the 16 kHz frames voice.js already makes
// and never sends audio anywhere: it says only "heard it".
//
//   page -> worker  {keywords}               once, first: the keyword lines (voice.js holds them)
//                   {frame: Int16Array}      20 ms of 16 kHz mono
//                   {reset: true}            forget what it was hearing (after a turn, a pause)
//   worker -> page  {ready: true} | {error} | {hit: "HEY_DUBS"}

/* global importScripts, createKws */
let kws = null, stream = null, keywords = null, runtime = false, fed = 0, decoded = 0;
// For the page's health report: frames it was given and decodes it ran, per second. Numbers only.
setInterval(() => { postMessage({ stats: { fed, decoded, ready: !!kws } }); fed = decoded = 0; }, 1000);
const queue = [];

const start = () => {
  if (!runtime || keywords === null || kws) return;
  try {
    kws = createKws(self.Module, {
      featConfig: { samplingRate: 16000, featureDim: 80 },
      modelConfig: {
        transducer: { encoder: "./encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
                      decoder: "./decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
                      joiner: "./joiner-epoch-12-avg-2-chunk-16-left-64.onnx" },
        tokens: "./tokens.txt", provider: "cpu", modelType: "", numThreads: 1, debug: 0,
        modelingUnit: "bpe", bpeVocab: "",
      },
      maxActivePaths: 4, numTrailingBlanks: 1, keywordsScore: 1.0, keywordsThreshold: 0.25,
      keywords,
    });
    stream = kws.createStream();
    postMessage({ ready: true });
    while (queue.length) feed(queue.shift());
  } catch (e) { postMessage({ error: String(e && e.message || e) }); }
};

// voice.js names its try (?try=N): a retry asks for the files afresh, never a copy cut short in a cache.
const TRY = new URLSearchParams(self.location.search).get("try");
const fresh = (p) => (TRY && TRY !== "1" ? `${p}?try=${TRY}` : p);
self.Module = {
  locateFile: (p) => fresh("../vendor/kws/" + p),
  onRuntimeInitialized: () => { runtime = true; start(); },
  print: () => {}, printErr: () => {},
};
try {
  importScripts(fresh("../vendor/kws/sherpa-onnx-kws.js"), fresh("../vendor/kws/sherpa-onnx-wasm-kws-main.js"));
} catch (e) { postMessage({ error: "the wake-word files are missing (kws/build-kws.sh): " + (e && e.message || e) }); }

// A gain stage in front of the spotter. It is LEVEL-sensitive: on the desk "hey dubs" peaking at -6 dBFS
// hit 3 of 3 voices, at -24 dBFS 2 of 3, at -30 dBFS none; and on the robot (2026-10-20) speech reached
// the page at about -24 dBFS peak, -54 dBFS RMS, with the tablet's own AGC on. So speech peaks are
// brought to about -6 dBFS: an envelope follows a peak at once and lets go over 1.5 s, and the gain is
// what puts that envelope at TARGET, at most +24 dB, never below 1. A frame quieter than GATE (-55 dBFS
// RMS) is not raised at all: lifting a quiet room into loud hiss before the word cost a hit on the desk
// (the Voice lane's point). The gain glides (about 10 ms) rather than jumping, and a sample is clamped
// to full scale in the rare case a glide overshoots.
// GAIN_MAX can be raised from the Thor for an A/B (face_door.py /api/head/spotter_gain, sent as gainMaxDb).
let GAIN_MAX = 16;
const TARGET = 0.5, RELEASE = Math.exp(-1 / (16000 * 1.5)), GATE = 0.00178, GLIDE = 0.006;
let env = 0, gain = 1;
function feed(frame) {
  const f = new Float32Array(frame.length);
  let sq = 0;
  for (let i = 0; i < frame.length; i++) sq += (frame[i] / 32768) ** 2;
  const quiet = Math.sqrt(sq / frame.length) < GATE;
  for (let i = 0; i < frame.length; i++) {
    const x = frame[i] / 32768, a = Math.abs(x);
    env = a > env ? a : env * RELEASE;
    const want = quiet ? 1 : Math.max(1, Math.min(GAIN_MAX, TARGET / Math.max(env, 1e-9)));
    gain = want < gain ? Math.max(1, Math.min(gain, TARGET / Math.max(a, 1e-9), want + (gain - want) * (1 - GLIDE * 4)))
                       : gain + (want - gain) * GLIDE;
    f[i] = Math.max(-1, Math.min(1, x * gain));
  }
  stream.acceptWaveform(16000, f); fed++;
  while (kws.isReady(stream)) {
    kws.decode(stream); decoded++;
    const r = kws.getResult(stream);
    if (r.keyword && r.keyword.length) {
      kws.reset(stream);                       // straight after a hit, as sherpa-onnx's own demo does
      postMessage({ hit: r.keyword });
    }
  }
}

onmessage = (e) => {
  const m = e.data;
  if (typeof m.keywords === "string") { keywords = m.keywords; start(); return; }
  if (typeof m.gainMaxDb === "number" && m.gainMaxDb >= 12 && m.gainMaxDb <= 40) { GAIN_MAX = 10 ** (m.gainMaxDb / 20); return; }
  if (m.reset) { if (kws && stream) { stream.free(); stream = kws.createStream(); } return; }
  if (m.frame) { if (kws) feed(m.frame); else if (queue.length < 50) queue.push(m.frame); }
};
