/* The robot tablet's sensors, sent to the robot (backend/tablet_relay.py publishes them as /tablet/*).

   Loaded by the dashboard and by the face page, so the readings keep flowing whichever one the kiosk
   shows. It runs ONLY inside the kiosk app (window.OpenDubsKiosk): a laptop or phone that opens the
   dashboard is not the robot's tablet, and its sensors must never be published as the robot's.

   What it sends, each reading with its age on the tablet (the robot stamps it on its own clock):
     light     every 1 s     the kiosk app's light(): lux
     battery   every 5 s     battery() and thermal(): level, charging, plugged, temperature, throttling
     fix       every 1 s     the GNSS, from the WebView's geolocation. No position, or none for 5 s, is
                             sent as no fix: never a stale or guessed position.
     imu       ~60 Hz        acceleration with gravity (DeviceMotion), in batches every 100 ms. No
                             gyroscope on this tablet: no rotation rate, no orientation (only tilt,
                             which the robot works out from gravity while the tablet is still).
     camera    ON DEMAND     front (screen-side) and rear (back-side), as JPEG frames, only while the
                             robot asks (something on the Thor subscribes, or someone switched it on).
                             The face shows a sign whenever a camera is open; nothing is recorded here.
*/
(() => {
  "use strict";
  const K = window.OpenDubsKiosk;
  if (!K || window.OpenDubsTabletSensors) return;
  const URL_ = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/tablet`;
  const st = { connected: false, rtt_ms: null, sent: 0, fix: null, error: null, camera: {} };
  let ws = null, retry = 1000;
  const send = (m) => { if (ws && ws.readyState === 1 && st.connected) { ws.send(JSON.stringify(m)); st.sent++; } };
  const json = (f) => { try { return typeof K[f] === "function" ? JSON.parse(K[f]()) : null; } catch (e) { return null; } };

  function connect() {
    ws = new WebSocket(URL_);
    ws.onopen = () => { retry = 1000; ws.send(JSON.stringify({ hello: { kiosk: true, version: K.version ? K.version() : null } })); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.ok) { st.connected = true; ws.send(JSON.stringify({ ping: performance.now() })); }
      if (m.error) st.error = m.error;
      if (m.camera) for (const [cam, want] of Object.entries(m.camera)) setCamera(cam, want);
      if (m.pong !== undefined) {                     // the round trip, for the robot's stamps
        const rtt = performance.now() - m.pong;
        st.rtt_ms = st.rtt_ms === null ? rtt : Math.min(rtt, 0.8 * st.rtt_ms + 0.2 * rtt);
        ws.send(JSON.stringify({ rtt_ms: st.rtt_ms }));
      }
    };
    ws.onclose = () => {
      st.connected = false;
      for (const cam of Object.keys(streams)) setCamera(cam, { on: false });   // no robot asking: cameras off
      setTimeout(connect, retry); retry = Math.min(retry * 2, 10000);
    };
  }

  // ---- the readings -----------------------------------------------------------------------------
  setInterval(() => {
    const l = json("light");
    if (l && typeof l.lux === "number") send({ kind: "light", lux: l.lux, age_ms: l.age_ms || 0 });
  }, 1000);

  const battery = () => {
    const b = json("battery"), t = json("thermal");
    if (b) send({ kind: "battery", level: b.level, charging: b.charging, plugged: b.plugged,
                  battery_c: t ? t.battery_c : null, thermal: t ? t.status : null, age_ms: 0 });
  };
  setInterval(battery, 5000);

  let pos = null;                                     // the last position and when it was taken
  if (navigator.geolocation) {
    navigator.geolocation.watchPosition(
      (p) => { pos = p; st.fix = { acc: p.coords.accuracy }; st.error = null; },
      (err) => { st.fix = null; st.error = `location: ${err.message}`; },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
  }
  setInterval(() => {
    const age = pos ? Date.now() - pos.timestamp : null;
    if (!pos || age > 5000) { send({ kind: "fix", age_ms: 0 }); return; }       // no fix, said plainly
    const c = pos.coords;
    send({ kind: "fix", lat: c.latitude, lon: c.longitude, acc: c.accuracy,
           alt: c.altitude, alt_acc: c.altitudeAccuracy, age_ms: Math.max(0, age) });
  }, 1000);

  let motion = [], lastMotion = performance.now();      // samples since the last batch
  const onMotion = (e) => {
    lastMotion = performance.now();
    const a = e.accelerationIncludingGravity;
    if (a && a.x !== null && motion.length < 64) motion.push({ t: e.timeStamp, ax: a.x, ay: a.y, az: a.z });
  };
  addEventListener("devicemotion", onMotion);
  // Found on the robot tablet (2026-10-15): after the kiosk app restarted for an update, the page got
  // no motion events at all until it was reloaded. A visible page with no motion for 10 s takes its
  // listener off and puts it back, which makes the browser start the sensor again.
  st.motion_restarts = 0;
  setInterval(() => {
    if (document.visibilityState !== "visible" || performance.now() - lastMotion < 10000) return;
    removeEventListener("devicemotion", onMotion);
    setTimeout(() => addEventListener("devicemotion", onMotion), 200);
    st.motion_restarts++; lastMotion = performance.now();
  }, 5000);
  setInterval(() => {
    if (!motion.length) return;
    const now = performance.now();
    send({ kind: "imu", samples: motion.map((s) => ({ ax: s.ax, ay: s.ay, az: s.az, age_ms: Math.max(0, now - s.t) })) });
    motion = [];
  }, 100);

  // ---- the cameras, on demand -------------------------------------------------------------------
  // One stream per camera while the robot asks for it, closed the moment it stops asking. Frames go
  // as binary: 4 bytes of header length, the JSON header {cam, w, h, age_ms}, then the JPEG. A frame
  // is skipped while the last one is still on its way, so a slow link lowers the rate, never queues.
  const streams = {};                                   // cam -> {stream, video, canvas, timer}
  async function setCamera(cam, want) {
    const cur = streams[cam];
    if (!want.on) {
      if (cur) { clearInterval(cur.timer); cur.stream.getTracks().forEach((t) => t.stop()); delete streams[cam]; }
      st.camera[cam] = "off";
      return;
    }
    if (cur || !navigator.mediaDevices) return;
    streams[cam] = { opening: true, timer: null, stream: { getTracks: () => [] } };
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: {
        facingMode: { exact: cam === "rear" ? "environment" : "user" },
        width: { ideal: want.w }, height: { ideal: want.h }, frameRate: { ideal: want.fps } } });
      if (!streams[cam] || !streams[cam].opening) { stream.getTracks().forEach((t) => t.stop()); return; }   // turned off meanwhile
      const video = document.createElement("video");
      video.muted = true; video.playsInline = true; video.srcObject = stream;
      await video.play();
      const canvas = document.createElement("canvas"), g = canvas.getContext("2d");
      let inFlight = false;
      const grab = () => {
        if (inFlight || !ws || ws.readyState !== 1 || ws.bufferedAmount > 0 || !video.videoWidth) return;
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        const t0 = performance.now();
        g.drawImage(video, 0, 0);
        inFlight = true;
        canvas.toBlob(async (blob) => {
          try {
            if (!blob || !ws || ws.readyState !== 1) return;
            const head = new TextEncoder().encode(JSON.stringify({ cam, w: canvas.width, h: canvas.height,
                                                                    age_ms: performance.now() - t0 }));
            const out = new Uint8Array(4 + head.length + blob.size);
            new DataView(out.buffer).setUint32(0, head.length);
            out.set(head, 4); out.set(new Uint8Array(await blob.arrayBuffer()), 4 + head.length);
            ws.send(out);
          } finally { inFlight = false; }
        }, "image/jpeg", want.q);
      };
      streams[cam] = { stream, video, canvas, timer: setInterval(grab, 1000 / want.fps) };
      st.camera[cam] = "on";
      ws && ws.readyState === 1 && ws.send(JSON.stringify({ camera_error: { [cam]: null } }));
    } catch (e) {
      delete streams[cam];
      st.camera[cam] = `failed: ${e.message}`;
      ws && ws.readyState === 1 && ws.send(JSON.stringify({ camera_error: { [cam]: e.message || String(e) } }));
    }
  }

  // Leaving the page closes its streams (see main.js in the face for why).
  addEventListener("pagehide", () => { for (const cam of Object.keys(streams)) setCamera(cam, { on: false }); });

  setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ ping: performance.now() })); }, 5000);
  connect();
  setTimeout(battery, 2000);

  // The kiosk app's version and what its self-updater last decided (the Head & face page shows it).
  const app = () => {
    let update = null;
    try { update = typeof K.updateStatus === "function" ? JSON.parse(K.updateStatus()) : null; } catch (e) { /* older app */ }
    send({ kind: "app", version: K.version ? K.version() : null,
           versionCode: typeof K.versionCode === "function" ? K.versionCode() : null, update });
  };
  setInterval(app, 30000); setTimeout(app, 2500);
  window.OpenDubsTabletSensors = { status: () => ({ ...st }) };
})();
