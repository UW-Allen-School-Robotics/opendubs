// The tracker's detector, OFF the page's main thread. A detection takes ~40 ms on the bench tablet;
// on the main thread that held every face frame behind it (frame p95 44 ms at 90 Hz). Here it costs
// the face nothing: the page posts a frame, keeps drawing, and takes the answer when it comes.
//
// Three cues, as the Tab5 had two (percept/): MOTION on every frame (frame differencing on a small luma
// copy, any range and angle), a PERSON detector (MediaPipe's object detector, EfficientDet-Lite0,
// "person": several metres, any facing) and the FACE (close range, and the only cue that says someone
// is looking AT the robot). The page asks for at most one detector per frame and arbitrates.

import { FilesetResolver, FaceLandmarker, ObjectDetector } from "../vendor/mediapipe/vision_bundle.mjs";
import { Motion, W as MW, H as MH } from "./percept/motion.js";

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
let lm = null, od = null, odError = null;
const motion = new Motion();
const small = new OffscreenCanvas(MW, MH);
const sg = small.getContext("2d", { willReadFrequently: true });
const luma = new Uint8Array(MW * MH);

function motionOf(bitmap, suppress) {
  sg.drawImage(bitmap, 0, 0, MW, MH);
  const px = sg.getImageData(0, 0, MW, MH).data;
  for (let i = 0, j = 0; j < luma.length; i += 4, j++) luma[j] = (px[i] * 77 + px[i + 1] * 150 + px[i + 2] * 29) >> 8;
  const r = motion.update(luma, suppress);
  return { valid: r.valid, nx: r.nx, ny: r.ny, strength: r.strength, gated: r.gated };
}

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
    // The person detector. Without it the tracker still runs on face and motion, and says so.
    try {
      od = await ObjectDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: VENDOR + "efficientdet_lite0.tflite", delegate: msg.delegate },
        canvas: msg.delegate === "GPU" ? new OffscreenCanvas(1, 1) : undefined,
        runningMode: "VIDEO", scoreThreshold: 0.3, maxResults: 3, categoryAllowlist: ["person"],
      });
    } catch (e) { odError = String(e && e.message || e).split("\n")[0]; }
    postMessage({ ready: true, loadMs: performance.now() - t0, person: !!od, personError: odError });
    return;
  }
  const out = { seq: msg.seq, detectMs: 0, ran: msg.run || null, found: false, person: null };
  const { width: fw, height: fh } = msg.bitmap;
  out.motion = motionOf(msg.bitmap, !!msg.suppress);
  const t0 = performance.now();
  if (msg.run === "face") {
    const r = lm.detectForVideo(msg.bitmap, msg.ts);
    if (r.faceLandmarks && r.faceLandmarks.length) {
      const p = r.faceLandmarks[0][168];          // between the eyes
      const [yaw, pitch] = r.facialTransformationMatrixes?.length ? yawPitch(r.facialTransformationMatrixes[0]) : [0, 0];
      Object.assign(out, { found: true, x: p.x, y: p.y, yaw, pitch });
    }
  } else if (msg.run === "person" && od) {
    const r = od.detectForVideo(msg.bitmap, msg.ts);
    let best = null;
    for (const d of r.detections || []) {
      const s = d.categories && d.categories[0] ? d.categories[0].score : 0;
      if (!best || s > best.score) best = { score: s, box: d.boundingBox };
    }
    if (best && best.box) {
      const b = best.box;                         // pixels; where a head is: the box's top, centred
      out.person = { x: (b.originX + b.width / 2) / fw, y: (b.originY + b.height * 0.12) / fh,
                     h: b.height / fh, score: best.score };
    }
  }
  out.detectMs = performance.now() - t0;
  msg.bitmap.close();
  postMessage(out);
}
