// The face's half of the head protocol (notes/design/head-protocol.md §3), understood HERE, in the
// face's own code, so the face on the tablet and its twin in a laptop browser answer identically
// (tablet plan DT-10). The kiosk app's door, or door_dev.py on a laptop, only carries the lines.

import { EMOTES, CUES, THEMES } from "./vocab.js";

export const ENGINE_VERSION = "0.1.0";

export function handle(face, audio, line, now) {
  const [verb, ...rest] = line.trim().split(" ");
  const arg = rest.join(" ");
  switch (verb) {
    case "PING":
      return `PONG kind=tablet-face engine=${ENGINE_VERSION} library=none mount=unknown mode=face ` +
             `st=${face.stateName()} audio=${audio.enabled ? "on" : "OFF"}`;
    case "STATE":
      if (!arg) break;
      face.setState(arg, now);                // like the Tab5: stored, acknowledged, shown if known
      return `OK state ${arg}`;
    case "EMOTE":
      if (!arg) break;
      return face.setEmote(arg, now) === null ? "ERR unknown emote" : `OK emote ${arg}`;
    case "THEME":
      if (!arg) break;
      return face.setTheme(arg) ? `OK theme ${arg}` : "ERR unknown theme";
    case "CUE":
      if (!CUES.includes(arg.toLowerCase())) return "ERR unknown cue";
      audio.cue(arg.toLowerCase());
      return audio.enabled ? `OK cue ${arg}` : `OK cue ${arg} (AUDIO OFF -- enable it in SETTINGS)`;
    case "CFG":
      return "ERR cfg: the library is not built yet (tablet plan 4.4)";
  }
  return "ERR unknown";
}

// The six earcons, synthesised; audible only when audio is on, as on the head (it defaults off).
export class Earcons {
  constructor(enabled = false) { this.enabled = enabled; this.ctx = null; }
  cue(name) {
    if (!this.enabled) return;
    this.ctx ??= new AudioContext();
    const plan = {
      heard: [[880, 0.07]], attend: [[660, 0.06], [990, 0.06]], refuse: [[520, 0.12], [390, 0.14]],
      fault: [[700, 0.08], [700, 0.08], [700, 0.08]], mode: [[950, 0.35]], boot: [[523, 0.1], [659, 0.1], [784, 0.16]],
    }[name] || [];
    let t = this.ctx.currentTime;
    for (const [f, d] of plan) {
      const o = this.ctx.createOscillator(), g = this.ctx.createGain();
      o.frequency.value = f; g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.18, t + 0.008); g.gain.linearRampToValueAtTime(0, t + d);
      o.connect(g).connect(this.ctx.destination); o.start(t); o.stop(t + d + 0.02);
      t += d + 0.05;
    }
  }
}
