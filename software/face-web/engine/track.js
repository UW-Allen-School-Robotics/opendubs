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
  const img = new Image();
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
  return v.play().then(() => v);
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
  stats.track = "loading";
  const tLoad = performance.now();
  const fileset = await FilesetResolver.forVisionTasks(VENDOR + "wasm");
  const lm = await FaceLandmarker.createFromOptions(fileset, {
    baseOptions: { modelAssetPath: VENDOR + "face_landmarker.task", delegate: "GPU" },
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
