/* The robot tablet's sensors, sent to the robot (backend/tablet_relay.py publishes them as /tablet/*).

   Loaded by the dashboard and by the face page, so the readings keep flowing whichever one the kiosk
   shows. It runs ONLY inside the kiosk app (window.OpenDubsKiosk): a laptop or phone that opens the
   dashboard is not the robot's tablet, and its sensors must never be published as the robot's.

   What it sends, each reading with its age on the tablet (the robot stamps it on its own clock):
     light     every 1 s     the kiosk app's light(): lux
     battery   every 10 s    battery() and thermal(): level, charging, plugged, temperature, throttling
     fix       every 1 s     the GNSS, from the WebView's geolocation. No position, or none for 5 s, is
                             sent as no fix: never a stale or guessed position.
*/
(() => {
  "use strict";
  const K = window.OpenDubsKiosk;
  if (!K || window.OpenDubsTabletSensors) return;
  const URL_ = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/tablet`;
  const st = { connected: false, rtt_ms: null, sent: 0, fix: null, error: null };
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
      if (m.pong !== undefined) {                     // the round trip, for the robot's stamps
        const rtt = performance.now() - m.pong;
        st.rtt_ms = st.rtt_ms === null ? rtt : Math.min(rtt, 0.8 * st.rtt_ms + 0.2 * rtt);
        ws.send(JSON.stringify({ rtt_ms: st.rtt_ms }));
      }
    };
    ws.onclose = () => { st.connected = false; setTimeout(connect, retry); retry = Math.min(retry * 2, 10000); };
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
  setInterval(battery, 10000);

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

  setInterval(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ ping: performance.now() })); }, 5000);
  connect();
  setTimeout(battery, 2000);
  window.OpenDubsTabletSensors = { status: () => ({ ...st }) };
})();
