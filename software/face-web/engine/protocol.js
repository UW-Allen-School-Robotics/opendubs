// The face's half of the head protocol (notes/design/head-protocol.md §3), understood HERE, in the
// face's own code, so the face on the tablet and its twin in a laptop browser answer identically
// (tablet plan DT-10). The kiosk app's door, or door_dev.py on a laptop, only carries the lines.

import { EMOTES, CUES, THEMES } from "./vocab.js";

export const ENGINE_VERSION = "0.1.0";
// Head protocol v4's gestures, by their wire names (software/head-protocol/head_protocol.py MOVES).
export const MOVES = ["wink_l", "wink_r", "blink2", "look_l", "look_r", "roll", "bob", "sparkle"];

export function handle(face, audio, line, now, skins) {
  const [verb, ...rest] = line.trim().split(" ");
  const arg = rest.join(" ");
  switch (verb) {
    case "PING":
      return `PONG kind=tablet-face engine=${ENGINE_VERSION} library=none mount=unknown mode=face ` +
             `st=${face.stateName()} audio=${audio.enabled ? "on" : "OFF"}` +
             (skins ? ` skin=${skins.shownName()}` + (skins.error ? ` skin_error=${skins.error.replace(/\s+/g, "_")}` : "") : "");
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
    case "SKIN": {
      // Head protocol v2. A code skin (default, ring) or a library skin's id. Unknown, or an asset
      // that will not load: the default eyes, and say so. Never a blank face.
      if (!arg || !skins) return "ERR skin <name> | default";
      const answer = (r) => r.ok ? `OK skin ${arg}`
        : r.error === "unknown skin" ? `ERR unknown skin ${arg} (showing default)`
        : `ERR skin ${arg}: ${r.error} (showing default)`;
      const r = skins.select(arg);
      // Unknown here may only mean "added since this page read the library" (a show's new skin, the
      // robot's library sync): look once more before falling back. The reply is then a promise.
      if (r.ok || r.error !== "unknown skin" || !skins.refresh) return answer(r);
      return skins.refresh().then((fresh) => answer(fresh ? skins.select(arg) : r));
    }
    case "SPIN": {
      // Head protocol v3: a show lands a spinning skin's counter-rotation on a beat. The skin eases
      // it (its own ease_s; the wire cannot make it snap). A skin that does not spin answers OK and
      // does nothing, so a show never fails because a different skin was chosen.
      const [mode, hold, extra] = rest;
      const ok = !extra && ((mode === "COUNTER" && (hold === undefined || /^\d+(\.\d+)?$/.test(hold))) ||
                            ((mode === "TOGETHER" || mode === "AUTO") && hold === undefined));
      if (!ok) return "ERR spin COUNTER [hold_s] | TOGETHER | AUTO";
      const skin = skins && skins.current;
      if (skin && typeof skin.spin === "function") skin.spin(mode.toLowerCase(), hold === undefined ? null : Number(hold));
      return `OK spin ${mode.toLowerCase()}`;
    }
    case "MOVE": {
      // Head protocol v4: a one-shot gesture, timed by the face (no parameters, so it cannot snap).
      // Unknown names are loud; a known one a skin cannot draw is the face's to ignore, OK.
      // Answered `ERR unknown` until the face can play moves at all, so conformance says PENDING.
      if (typeof face.move !== "function") break;
      const name = arg.trim().toLowerCase();
      if (!MOVES.includes(name)) return `ERR move ${name || "<name>"}`;
      face.move(name, now);
      return `OK move ${name}`;
    }
    case "CFG":
      return "ERR cfg: the library is not built yet (tablet plan 4.4)";
  }
  return "ERR unknown";
}

// The six earcons, synthesised; audible only when audio is on, as on the head (it defaults off).
/** A rate-limited, time-capped re-read of the skin library, for SKIN's second look. At most once per
 *  `gapMs`, and never longer than `capMs`, so the reply still beats the door's 0.4 s timeout; a read
 *  that runs over still lands, and the next SKIN finds it. Resolves true if it re-read in time. */
export function libraryRefresher(skins, baseUrl, { gapMs = 2000, capMs = 250 } = {}) {
  let last = -Infinity;
  return () => {
    const now = performance.now();
    if (now - last < gapMs) return Promise.resolve(false);
    last = now;
    return Promise.race([skins.loadLibrary(baseUrl).then(() => true),
                         new Promise((res) => setTimeout(() => res(false), capMs))]);
  };
}

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
