// AirPlay, heard from the face: the Thor's receiver (shairport-sync, the Thor lane's) → the dashboard's
// relay (backend/airplay_relay.py) → /ws/airplay → here → the speaker.
//
// Heard ONLY when both hold:
//   - this page holds the show-audio role (window.OpenDubsShowAudio.status().youHold), so the music
//     never comes out of two devices; and
//   - the show-audio hub's gate is open. The hub (show_audio.py, the Choreography lane's) is the one
//     arbiter of the speaker: a show mutes AirPlay, AirPlay pauses ambient. It calls setGate() through
//     show_audio.js. The gate starts MUTED, so a page that just connected is never loud before the
//     hub has spoken.
//
// Exposes window.OpenDubsAirplay = { setGate, now(), stats } and the "now playing" for the caption.

const GAIN = { on: 1, duck: 0.3, mute: 0 };

export function start({ onRobot }) {
  if (!onRobot || window.OpenDubsAirplay) return;
  const api = window.OpenDubsAirplay = {
    gate: "mute", stats: {}, playing: { active: false, title: null, artist: null },
    setGate(g) { if (g in GAIN) { api.gate = g; apply(); } },
    now() { return api.playing; },
  };
  let ctx = null, node = null, gain = null, fmtOk = false, ready = null;   // ready: set up once, shared

  const holder = () => {
    const sa = window.OpenDubsShowAudio;
    try { return !!(sa && typeof sa.status === "function" && sa.status().youHold); } catch (e) { return false; }
  };
  function apply() {
    if (!gain) return;
    const g = holder() ? GAIN[api.gate] : 0;
    gain.gain.setTargetAtTime(g, ctx.currentTime, 0.08);       // ~200 ms to settle: no click
  }
  setInterval(apply, 1000);                                     // the role can move between devices

  async function audio() {
    ctx = new AudioContext({ sampleRate: 44100, latencyHint: "interactive" });
    await ctx.audioWorklet.addModule(new URL("./airplay-worklet.js", import.meta.url));
    node = new AudioWorkletNode(ctx, "airplay-player", { outputChannelCount: [2] });
    gain = ctx.createGain(); gain.gain.value = 0;
    node.connect(gain).connect(ctx.destination);
    node.port.onmessage = (e) => { api.stats = e.data; };
    if (ctx.state !== "running") ctx.resume().catch(() => {});
    addEventListener("pointerdown", () => ctx.resume().catch(() => {}), { once: true });
    apply();
  }

  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/airplay`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = async (m) => {
      if (typeof m.data === "string") {
        const msg = JSON.parse(m.data);
        if (msg.format) fmtOk = msg.format === "s16le" && msg.rate === 44100 && msg.channels === 2;
        if (msg.now) api.playing = msg.now;
        return;
      }
      if (!fmtOk) return;
      ready = ready || audio();
      await ready;
      node.port.postMessage(m.data, [m.data]);
    };
    ws.onclose = () => { api.playing = { active: false, title: null, artist: null }; setTimeout(connect, 2000); };
  }
  connect();
}
