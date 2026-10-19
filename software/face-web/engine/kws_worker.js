// The wake-word spotter, off the page's main thread: sherpa-onnx's keyword spotter, built for the browser
// with its English model baked in (kws/build-kws.sh). It is fed the 16 kHz frames voice.js already makes
// and never sends audio anywhere: it says only "heard it".
//
//   page -> worker  {keywords}               once, first: the keyword lines (voice.js holds them)
//                   {frame: Int16Array}      20 ms of 16 kHz mono
//                   {reset: true}            forget what it was hearing (after a turn, a pause)
//   worker -> page  {ready: true} | {error} | {hit: "HEY_DUBS"}

/* global importScripts, createKws */
let kws = null, stream = null, keywords = null, runtime = false;
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

self.Module = {
  locateFile: (p) => "../vendor/kws/" + p,
  onRuntimeInitialized: () => { runtime = true; start(); },
  print: () => {}, printErr: () => {},
};
try {
  importScripts("../vendor/kws/sherpa-onnx-kws.js", "../vendor/kws/sherpa-onnx-wasm-kws-main.js");
} catch (e) { postMessage({ error: "the wake-word files are missing (kws/build-kws.sh): " + (e && e.message || e) }); }

function feed(frame) {
  const f = new Float32Array(frame.length);
  for (let i = 0; i < frame.length; i++) f[i] = frame[i] / 32768;
  stream.acceptWaveform(16000, f);
  while (kws.isReady(stream)) {
    kws.decode(stream);
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
  if (m.reset) { if (kws && stream) { stream.free(); stream = kws.createStream(); } return; }
  if (m.frame) { if (kws) feed(m.frame); else if (queue.length < 50) queue.push(m.frame); }
};
