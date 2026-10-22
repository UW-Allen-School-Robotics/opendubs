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
import { Attention, KIND } from "./percept/attention.js";

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
  // What the tracker is doing, as numbers, for the robot (main.js → the door → /api/head/follow and
  // the Head & face page): visible without plugging the tablet in.
  const info = stats.trackInfo = { state: "loading", hz: 0, found_pct: 0, frame: null, last_found_at: null };
  const worker = new Worker(new URL("./track_worker.js", import.meta.url), { type: "module" });
  let video;
  try {
    video = await (src === "sweep" ? sweepSource() : cameraSource());
  } catch (e) {
    info.state = `failed: camera: ${e.message || e.name}`; stats.track = info.state;
    throw e;
  }
  // The tablet opens ONE camera at a time. The robot's own camera streams (the dashboard's
  // tablet_sensors.js) share this capture for the screen-side camera, and PAUSE tracking to use the
  // back-side one (the user's decision, 2026-10-15): the eyes go back to their idle gaze, and tracking
  // resumes when the stream ends. The face also pauses it on battery (main.js), so each pause names its
  // reason and tracking resumes only when none is left.
  let paused = false;
  const holds = new Set();
  const stopVideo = () => { const s = video && video.srcObject; if (s && s.getTracks) s.getTracks().forEach((t) => t.stop()); };
  window.OpenDubsTracking = {
    video: () => (paused ? null : video),               // the live screen-side capture, to share
    paused: () => paused,
    pause(why = "stream") {
      holds.add(why);
      if (paused) return;
      paused = true; stopVideo(); face.look(null);
      stats.track = info.state = why === "battery" ? "paused: the tablet is on battery" : "paused: the camera is lent to a stream";
    },
    async resume(why = "stream") {
      holds.delete(why);
      if (!paused || holds.size) return;
      video = await (src === "sweep" ? sweepSource() : cameraSource());
      paused = false; stats.track = "resumed"; info.state = "running";
      video.requestVideoFrameCallback(onFrame);
    },
  };
  addEventListener("pagehide", stopVideo);
  const m = { detectMs: [], latencyMs: [], hits: 0, frames: 0, since: performance.now(), src, delegate, worker: true };
  window.trackStats = m;
  const pct = (a, p) => { if (!a.length) return NaN; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
  worker.onerror = (e) => { stats.track = info.state = `failed: worker: ${e.message || "error"} (${e.filename || ""}:${e.lineno || ""})`; };
  const ready = new Promise((res, rej) => { worker.onmessage = (e) => {
    if (e.data.error) { stats.track = info.state = "failed: worker: " + e.data.error.split("\n")[0]; rej(new Error(e.data.error)); }
    else if (e.data.ready) { m.loadMs = e.data.loadMs; m.personModel = e.data.person; m.personError = e.data.personError; res(); } }; });
  worker.postMessage({ init: true, delegate });
  await ready;

  // ATTENTION, as the Tab5 did it (percept/attention.js): every result's cues go to the arbiter, and the
  // one winner (face > person > motion) is what the eyes, and through the door the neck, look at.
  const att = new Attention();
  m.cues = { motion: 0, person: 0, face: 0 };
  let busy = false, seq = 0, lastPerson = { at: -1e9, h: 0 };
  const captured = new Map();
  worker.onmessage = (e) => {
    const r = e.data;
    busy = false;
    if (r.error) { stats.track = info.state = "failed: worker: " + r.error.split("\n")[0]; return; }
    const now = performance.now();
    if (m.warmupMs === undefined) { m.warmupMs = r.detectMs; m.since = now; return; }   // the first is a warm-up
    m.frames++;
    if (r.ran) { m.detectMs.push(r.detectMs); if (m.detectMs.length > 120) m.detectMs.shift(); }
    const cap = captured.get(r.seq); captured.delete(r.seq);
    if (cap) { m.latencyMs.push(now - cap); if (m.latencyMs.length > 120) m.latencyMs.shift(); }
    // The cues, in image coordinates -1..1 (x right, y down), the same for all three.
    const seen = cap || now;              // when the frame was captured: the neck needs it (neck_follow.py)
    if (r.motion && r.motion.valid) { att.noteMotion(now, r.motion.nx, r.motion.ny, r.motion.strength, seen); m.cues.motion++; }
    if (r.person) {
      att.notePerson(now, (r.person.x - 0.5) * 2, (r.person.y - 0.5) * 2, r.person.score, seen);
      lastPerson = { at: now, h: r.person.h }; m.cues.person++;
    }
    if (r.found) {
      // Facing the robot is what a face is worth: the score falls off with head turn.
      const facing = Math.max(0, 1 - Math.abs(r.yaw) / 45);
      att.noteFace(now, (r.x - 0.5) * 2, (r.y - 0.5) * 2, 0.3 + 0.7 * facing, seen);
      m.yaw = r.yaw; m.cues.face++;
    }
    const g = att.tick(now);
    if (g.kind === KIND.NONE) face.look(null);
    else {
      m.hits++; info.last_found_at = now;
      // The camera looks out of the screen, so a person on its right is on the face's left (x flips).
      // ix, iy: where in the camera's image (0..1), for the neck (main.js → the door → neck_follow.py).
      face.look({ x: -g.nx * GAIN, y: g.ny * GAIN, conf: g.confidence, at: now, kind: g.kind,
                  ix: (g.nx + 1) / 2, iy: (g.ny + 1) / 2, seen: g.seen });
    }
    const el = (now - m.since) / 1000;
    info.state = "running"; info.hz = +(m.frames / el).toFixed(1);
    info.found_pct = Math.round(100 * m.hits / Math.max(1, m.frames));
    info.frame = video && video.videoWidth ? [video.videoWidth, video.videoHeight] : null;
    info.kind = g.kind; info.confidence = +g.confidence.toFixed(2);
    info.cues = { ...m.cues };
    if (m.personModel === false) info.person_model = m.personError || "not loaded";
    stats.track = `${src} ${delegate} worker, warm-up ${m.warmupMs.toFixed(0)} ms, ${info.hz} Hz, ` +
      `${info.found_pct}% tracking (${g.kind}${g.kind !== KIND.NONE ? ` ${info.confidence}` : ""}), ` +
      `cues m${m.cues.motion}/p${m.cues.person}/f${m.cues.face}, detect p50 ${pct(m.detectMs, 0.5).toFixed(1)} ` +
      `p95 ${pct(m.detectMs, 0.95).toFixed(1)} ms, frame->result p50 ${pct(m.latencyMs, 0.5).toFixed(0)} ms` +
      (m.yaw !== undefined ? `, yaw ${m.yaw.toFixed(0)}` : "");
  };
  // Which detector this frame: the person detector on every other frame; the face on the rest, but
  // only when a person is near (a big box) or none has been seen lately (someone close enough that the
  // face fills the view). At ~15 frames a second that is ~7 detections a second, the old tracker's load.
  const which = (n, now) => {
    if (n % 2 === 0) return m.personModel ? "person" : "face";
    const near = now - lastPerson.at < 1500 && lastPerson.h > 0.35;
    const none = now - lastPerson.at > 1500;
    return near || none ? "face" : null;
  };
  const onFrame = async (now, meta) => {
    if (paused) return;                                 // the loop stops; resume() restarts it
    if (!busy) {
      busy = true;
      const s = ++seq;
      // From the camera's own capture time where the browser gives one, else from now.
      captured.set(s, meta && meta.captureTime ? meta.captureTime : performance.now());
      const bitmap = await createImageBitmap(video);
      worker.postMessage({ bitmap, ts: performance.now(), seq: s, run: which(s, performance.now()) }, [bitmap]);
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
