// Head protocol v5's device levels on the face: VOLUME and BRIGHT (notes/design/head-protocol.md §3).
//
// A show sets a level with an optional fade; the fade runs HERE, on the device, so a cue is one line
// at its time and the wire cannot make it jerky. Every value is clamped to the robot's settings —
// brightness never below `brightMin`, so a show cannot black the face out by mistake; volume never
// above `volMax` — and the caller is told the value actually applied. IDLE returns to the robot's
// idle setting, and so does a lost door (main.js), so a dead runner cannot leave the robot dimmed.
//
// On the tablet the kiosk app does the device part (window.OpenDubsKiosk.setBrightness /
// setMediaVolume). Without it — a twin in a laptop browser, or the headless engine in CI — brightness
// is shown as a dimming of the page and volume is only remembered.

export const DEFAULTS = { brightMin: 0.15, volMax: 1.0, idle: { volume: 0.6, bright: 1.0 } };
export const FADE_MAX_S = 30;
const STEP_MS = 33;

export class Levels {
  constructor(settings = {}) {
    this.cfg = { ...DEFAULTS, idle: { ...DEFAULTS.idle } };
    // Until the robot's settings say otherwise, idle is what the device is set to now: a show's
    // IDLE must not override the volume someone chose on the tablet.
    const K = typeof window !== "undefined" ? window.OpenDubsKiosk : null;
    if (K && typeof K.getMediaVolume === "function") this.cfg.idle.volume = K.getMediaVolume();
    this.configure(settings);
    this.level = { ...this.cfg.idle };     // what is applied now
    this.timer = { volume: null, bright: null };
    this.touched = false;                  // a show has set something since the last idle
  }

  /** The robot's settings (the Head & face page): brightMin, volMax and the idle levels. */
  configure(s = {}) {
    if (typeof s.brightMin === "number") this.cfg.brightMin = Math.min(1, Math.max(0.05, s.brightMin));
    if (typeof s.volMax === "number") this.cfg.volMax = Math.min(1, Math.max(0, s.volMax));
    for (const k of ["volume", "bright"]) {
      if (s.idle && typeof s.idle[k] === "number") this.cfg.idle[k] = this.clamp(k, s.idle[k]);
    }
  }

  clamp(which, v) {
    return which === "bright" ? Math.min(1, Math.max(this.cfg.brightMin, v)) : Math.max(0, Math.min(this.cfg.volMax, v));
  }

  /** Fade `which` ("volume" | "bright") to `v` over `fadeS` seconds. Returns the applied target. */
  set(which, v, fadeS = 0) {
    const target = this.clamp(which, v);
    const ms = Math.min(FADE_MAX_S, Math.max(0, fadeS || 0)) * 1000;
    clearInterval(this.timer[which]);
    this.touched = true;
    const from = this.level[which], t0 = Date.now();
    if (ms <= 0 || from === target) { this._write(which, target); return target; }
    this.timer[which] = setInterval(() => {
      const p = Math.min(1, (Date.now() - t0) / ms);
      this._write(which, from + (target - from) * p);
      if (p >= 1) clearInterval(this.timer[which]);
    }, STEP_MS);
    return target;
  }

  /** Back to the idle setting. Returns the applied target. */
  idle(which, fadeS = 0) {
    const v = this.set(which, this.cfg.idle[which], fadeS);
    this.touched = false;                  // at idle: nothing for a lost door to undo
    return v;
  }

  /** Both back to idle — a lost door, or a show's end that never said so. Quiet if nothing changed. */
  restoreIdle(fadeS = 1) {
    if (!this.touched) return;
    this.idle("volume", fadeS); this.idle("bright", fadeS);
    this.touched = false;
  }

  _write(which, v) {
    this.level[which] = v;
    const K = typeof window !== "undefined" ? window.OpenDubsKiosk : null;
    if (which === "bright") {
      if (K && typeof K.setBrightness === "function") K.setBrightness(v);
      else if (typeof document !== "undefined") document.documentElement.style.filter = v >= 1 ? "" : `brightness(${v})`;
    } else if (K && typeof K.setMediaVolume === "function") {
      K.setMediaVolume(v);
    }
  }
}
