// The tracker's detector, OFF the page's main thread. A detection takes ~40 ms on the bench tablet;
// on the main thread that held every face frame behind it (frame p95 44 ms at 90 Hz). Here it costs
// the face nothing: the page posts a frame, keeps drawing, and takes the answer when it comes.

import { FilesetResolver, FaceLandmarker } from "../vendor/mediapipe/vision_bundle.mjs";

const VENDOR = new URL("../vendor/mediapipe/", import.meta.url).href;

// MediaPipe loads its WebAssembly glue with importScripts(), which a MODULE worker does not have, and
// without it falls back to a <script> tag, which a worker has no document for ("ModuleFactory not
// set"). This is importScripts as a classic worker defines it: fetch synchronously, evaluate at the
// global scope. It only ever loads our own vendored file. Installed UNCONDITIONALLY: a module worker
// does define importScripts — as a function that throws — so "only if missing" never installed it.
{
  self.importScripts = (...urls) => {
    for (const url of urls) {
      const x = new XMLHttpRequest();
      x.open("GET", url, false);
      x.send();
      if (x.status !== 200) throw new Error(`importScripts ${url}: HTTP ${x.status}`);
      (0, eval)(x.responseText);
    }
  };
}
let lm = null;

function yawPitch(m) {
  const d = m.data;
  return [Math.atan2(-d[8], Math.hypot(d[0], d[4])) * 180 / Math.PI, Math.atan2(d[9], d[10]) * 180 / Math.PI];
}

onmessage = async (ev) => {
  try { await handle(ev.data); }
  catch (e) { postMessage({ error: String(e && (e.stack || e.message) || e) }); }
};

async function handle(msg) {
  if (msg.init) {
    const t0 = performance.now();
    const fileset = await FilesetResolver.forVisionTasks(VENDOR + "wasm");
    lm = await FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: VENDOR + "face_landmarker.task", delegate: msg.delegate },
      canvas: msg.delegate === "GPU" ? new OffscreenCanvas(1, 1) : undefined,
      runningMode: "VIDEO", numFaces: 1,
      outputFaceBlendshapes: true, outputFacialTransformationMatrixes: true,
    });
    postMessage({ ready: true, loadMs: performance.now() - t0 });
    return;
  }
  const t0 = performance.now();
  const r = lm.detectForVideo(msg.bitmap, msg.ts);
  const detectMs = performance.now() - t0;
  msg.bitmap.close();
  const out = { seq: msg.seq, detectMs, found: false };
  if (r.faceLandmarks && r.faceLandmarks.length) {
    const p = r.faceLandmarks[0][168];          // between the eyes
    const [yaw, pitch] = r.facialTransformationMatrixes?.length ? yawPitch(r.facialTransformationMatrixes[0]) : [0, 0];
    Object.assign(out, { found: true, x: p.x, y: p.y, yaw, pitch });
  }
  postMessage(out);
}
