// Looking at a person, in the page: MediaPipe Face Landmarker on the screen-side camera, driving the
// face's gaze. Richer than the Tab5's box detector: 478 landmarks, 52 blendshapes and a head-pose
// matrix per face, so the face knows where a person is AND whether they are looking at it.
//
// Two inputs, chosen by `tracksrc=`:
//   camera (default)  getUserMedia — the laptop's webcam, the tablet's screen-side camera
//   sweep             the esp-dl reference face (the Tab5 probe's own stimulus) sliding left and
//                     right across a canvas: a person who walks past on cue, so the measurement
//                     needs nobody at the bench and is the same on every machine
//
// It publishes what a person costs: detections per second, the detector's time per frame, and —
// for the camera — capture-to-result latency from requestVideoFrameCallback's capture time.

import { FilesetResolver, FaceLandmarker } from "../vendor/mediapipe/vision_bundle.mjs";

const VENDOR = new URL("../vendor/mediapipe/", import.meta.url).href;
const GAIN = 1.4;                       // the frame's edge is past full deflection, as an eye leads

function sweepSource() {
  // The bench stimulus: only for `tracksrc=sweep`, never for a face in use. A packaged face ships
  // without test/, so a missing photo is a clear refusal, not a canvas waiting for an image forever.
  const img = new Image();
  const missing = new Promise((_, reject) => {
    img.onerror = () => reject(new Error("the sweep stimulus test/human_face.jpg is not here"));
  });
  img.src = new URL("../test/human_face.jpg", import.meta.url).href;
  const c = document.createElement("canvas");
  c.width = 640; c.height = 360;
  const g = c.getContext("2d");
  const t0 = performance.now();
  const draw = () => {
    const t = (performance.now() - t0) / 1000;
    g.fillStyle = "#3a3a3a"; g.fillRect(0, 0, c.width, c.height);
    if (img.complete) {
      const x = (c.width - img.width) / 2 + Math.sin(t * 0.8) * 200;   // walks past, ~8 s a cycle
      g.drawImage(img, x, (c.height - img.height) / 2);
    }
    requestAnimationFrame(draw);
  };
  draw();
  const v = document.createElement("video");
  v.muted = true; v.playsInline = true; v.srcObject = c.captureStream(30);
  return Promise.race([missing, img.decode().then(() => v.play()).then(() => v)]);
}

async function cameraSource() {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } },
    audio: false,
  });
  const v = document.createElement("video");
  v.muted = true; v.playsInline = true; v.srcObject = stream;
  await v.play();
  return v;
}

function yawPitch(m) {
  // The facial transformation matrix, column-major 4x4: rotation in the upper 3x3.
  const d = m.data;
  const yaw = Math.atan2(-d[8], Math.hypot(d[0], d[4])) * 180 / Math.PI;
  const pitch = Math.atan2(d[9], d[10]) * 180 / Math.PI;
  return [yaw, pitch];
}

export async function start(face, stats, src = new URLSearchParams(location.search).get("tracksrc") || "camera") {
  const q = new URLSearchParams(location.search);
  const delegate = (q.get("delegate") || "GPU").toUpperCase();
  if (q.get("trackmain") === "1") return startMain(face, stats, src, delegate);
  stats.track = "loading";
  const worker = new Worker(new URL("./track_worker.js", import.meta.url), { type: "module" });
  const video = await (src === "sweep" ? sweepSource() : cameraSource());
  const m = { detectMs: [], latencyMs: [], hits: 0, frames: 0, since: performance.now(), src, delegate, worker: true };
  window.trackStats = m;
  const pct = (a, p) => { if (!a.length) return NaN; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
  worker.onerror = (e) => { stats.track = `worker failed: ${e.message || "error"} (${e.filename || ""}:${e.lineno || ""})`; };
  const ready = new Promise((res, rej) => { worker.onmessage = (e) => {
    if (e.data.error) { stats.track = "worker failed: " + e.data.error.split("\n")[0]; rej(new Error(e.data.error)); }
    else if (e.data.ready) { m.loadMs = e.data.loadMs; res(); } }; });
  worker.postMessage({ init: true, delegate });
  await ready;

  let busy = false, seq = 0;
  const captured = new Map();
  worker.onmessage = (e) => {
    const r = e.data;
    busy = false;
    if (r.error) { stats.track = "worker failed: " + r.error.split("\n")[0]; return; }
    const now = performance.now();
    if (m.warmupMs === undefined) { m.warmupMs = r.detectMs; m.since = now; return; }   // the first is a warm-up
    m.frames++;
    m.detectMs.push(r.detectMs); if (m.detectMs.length > 120) m.detectMs.shift();
    const cap = captured.get(r.seq); captured.delete(r.seq);
    if (cap) { m.latencyMs.push(now - cap); if (m.latencyMs.length > 120) m.latencyMs.shift(); }
    if (r.found) {
      m.hits++;
      const facing = Math.max(0, 1 - Math.abs(r.yaw) / 45);
      // ix, iy: where in the camera's image (0..1), for the neck (main.js → the door → neck_follow.py)
      face.look({ x: -(r.x - 0.5) * 2 * GAIN, y: (r.y - 0.5) * 2 * GAIN, conf: 0.4 + 0.6 * facing, at: now, ix: r.x, iy: r.y });
      m.yaw = r.yaw;
    } else face.look(null);
    const el = (now - m.since) / 1000;
    stats.track = `${src} ${delegate} worker, warm-up ${m.warmupMs.toFixed(0)} ms, ${(m.frames / el).toFixed(1)} Hz, ` +
      `${(100 * m.hits / Math.max(1, m.frames)).toFixed(0)}% found, detect p50 ${pct(m.detectMs, 0.5).toFixed(1)} ` +
      `p95 ${pct(m.detectMs, 0.95).toFixed(1)} ms, frame->result p50 ${pct(m.latencyMs, 0.5).toFixed(0)} ms` +
      (m.yaw !== undefined ? `, yaw ${m.yaw.toFixed(0)}` : "");
  };
  const onFrame = async (now, meta) => {
    if (!busy) {
      busy = true;
      const s = ++seq;
      // From the camera's own capture time where the browser gives one, else from now.
      captured.set(s, meta && meta.captureTime ? meta.captureTime : performance.now());
      const bitmap = await createImageBitmap(video);
      worker.postMessage({ bitmap, ts: performance.now(), seq: s }, [bitmap]);
    }
    video.requestVideoFrameCallback(onFrame);
  };
  video.requestVideoFrameCallback(onFrame);
}

// The first version: the detector on the page's main thread. Kept for comparison (trackmain=1).
async function startMain(face, stats, src, delegate) {
  stats.track = "loading";
  const tLoad = performance.now();
  const fileset = await FilesetResolver.forVisionTasks(VENDOR + "wasm");
  const lm = await FaceLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: VENDOR + "face_landmarker.task", delegate },
    runningMode: "VIDEO", numFaces: 1,
    outputFaceBlendshapes: true, outputFacialTransformationMatrixes: true,
  });
  const loadMs = performance.now() - tLoad;
  const video = await (src === "sweep" ? sweepSource() : cameraSource());

  const m = { detectMs: [], latencyMs: [], hits: 0, frames: 0, since: performance.now(), loadMs, src };
  window.trackStats = m;
  const pct = (a, p) => { if (!a.length) return NaN; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };

  let busy = false;
  const onFrame = (now, meta) => {
    if (!busy) {
      busy = true;
      const t0 = performance.now();
      const r = lm.detectForVideo(video, t0);
      const t1 = performance.now();
      // THE FIRST DETECTION IS A WARM-UP (the GPU path compiles its shaders, and on the main thread
      // that stalls the face for as long as it takes). Reported on its own, then the counts restart,
      // so steady state is not averaged with it.
      if (m.warmupMs === undefined) {
        m.warmupMs = t1 - t0; m.since = t1; busy = false;
        video.requestVideoFrameCallback(onFrame);
        return;
      }
      m.detectMs.push(t1 - t0); if (m.detectMs.length > 120) m.detectMs.shift();
      if (meta && meta.captureTime) { m.latencyMs.push(t1 - meta.captureTime); if (m.latencyMs.length > 120) m.latencyMs.shift(); }
      m.frames++;
      if (r.faceLandmarks && r.faceLandmarks.length) {
        m.hits++;
        const pts = r.faceLandmarks[0];
        // Between the eyes (landmark 168): where a person is, in the camera's frame.
        const p = pts[168];
        let [yaw, pitch] = r.facialTransformationMatrixes?.length ? yawPitch(r.facialTransformationMatrixes[0]) : [0, 0];
        // Facing the robot is worth more than being near it: confidence falls off with head turn.
        const facing = Math.max(0, 1 - Math.abs(yaw) / 45);
        // The camera looks out of the screen, so a person on its right is on the face's left.
        face.look({ x: -(p.x - 0.5) * 2 * GAIN, y: (p.y - 0.5) * 2 * GAIN, conf: 0.4 + 0.6 * facing, at: performance.now() });
        m.yaw = yaw; m.pitch = pitch; m.x = p.x; m.y = p.y;
      } else face.look(null);
      busy = false;
    }
    const el = (performance.now() - m.since) / 1000;
    stats.track = `${src} warm-up ${(m.warmupMs ?? NaN).toFixed(0)} ms, ${(m.frames / el).toFixed(1)} Hz, ${(100 * m.hits / Math.max(1, m.frames)).toFixed(0)}% found, ` +
      `detect p50 ${pct(m.detectMs, 0.5).toFixed(1)} p95 ${pct(m.detectMs, 0.95).toFixed(1)} ms` +
      (m.latencyMs.length ? `, capture->result p50 ${pct(m.latencyMs, 0.5).toFixed(0)} ms` : "") +
      (m.yaw !== undefined ? `, yaw ${m.yaw.toFixed(0)}` : "");
    video.requestVideoFrameCallback(onFrame);
  };
  video.requestVideoFrameCallback(onFrame);
}
