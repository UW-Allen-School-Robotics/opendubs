// The face's behaviour: what the Tab5 face does (firmware/tab5_idf/app_face/app_face.cpp), as a
// model with no drawing in it. A skin draws whatever this produces, so every skin blinks, emotes,
// saccades and looks where it is told in the same way.

import { Spring, Ease } from "./anim.js";
import { STATES, EMOTES, THEMES, NO_LINK_COLOUR, QUOTES } from "./vocab.js";

export const DESIGN_W = 1280, DESIGN_H = 720;        // the Tab5's panel, the geometry's frame
const BODY = 480, GAP_FRAC = 0.34;
const IPD = BODY * (1 + GAP_FRAC);
export const SLOT = [DESIGN_W / 2 - IPD / 2, DESIGN_W / 2 + IPD / 2];
export const EYE_Y = DESIGN_H / 2;
const LINK_STALE_MS = 5000;
const GAZE_AMP_FLOOR = 0.35, GAZE_V = 0.55;

const rnd = (n) => Math.floor(Math.random() * n);
const rgb = (c) => [(c >> 16) & 255, (c >> 8) & 255, c & 255];

export class Face {
  constructor() {
    this.state = -1;            // index into STATES; -1 = never heard from the robot
    this.stateAt = 0;
    this.emote = "NEUTRAL";
    this.pin = null;            // an EMOTE <name> pins; EMOTE AUTO releases
    this.theme = "plain";
    this.bodyW = new Spring(BODY); this.bodyH = new Spring(BODY);
    this.radius = new Spring(BODY * 0.205);
    this.lid = new Spring(1);   // shut at first: the eyes open once, as the face's first act
    this.browRaise = new Spring(0); this.browAngle = new Spring(0);
    this.asym = new Spring(0);
    this.gazeX = new Ease(0); this.gazeY = new Ease(0);
    this.col = rgb(NO_LINK_COLOUR).map((v) => new Ease(v, 0.45));
    this.blinking = false; this.blinkEnd = 0;
    this.nextBlink = 0; this.nextSaccade = 0; this.nextEmote = 0;
    this.quote = -1; this.nextQuote = 0;
    this.target = null;         // a person to look at: {x, y (-1..1), conf (0..1), at}
    this.woke = false;
  }

  // ---- inputs: what the head protocol's face messages do ------------------------------------
  setState(name, now) {
    const i = STATES.findIndex((s) => s[0] === name);
    if (i < 0) return false;
    this.state = i; this.stateAt = now;
    this.applyColour();
    const pinned = STATES[i][3];
    if (pinned) this.applyEmote(pinned);
    else if (!this.pin) this.nextEmote = now;
    return true;
  }
  setEmote(name, now) {
    if (name.toUpperCase() === "AUTO") { this.pin = null; this.nextEmote = now; return "AUTO"; }
    const n = name.toUpperCase();
    if (!(n in EMOTES)) return null;
    this.pin = n;
    if (!this.lockedEmote()) this.applyEmote(n);
    return name;
  }
  setTheme(name) {
    const n = name.toLowerCase() === "none" ? "plain" : name.toLowerCase();
    if (!(n in THEMES)) return false;
    this.theme = n; this.applyColour();
    return true;
  }
  look(target) { this.target = target; }      // from a tracker; null to let the eyes wander

  // ---- the model ------------------------------------------------------------------------------
  linked(now) { return this.state >= 0 && now - this.stateAt < LINK_STALE_MS; }
  lockedEmote() { return this.state >= 0 ? STATES[this.state][3] : null; }
  free() { return this.state >= 0 && !STATES[this.state][3]; }

  stateColour() {
    if (this.state < 0) return NO_LINK_COLOUR;
    const [name, , base, , reserved] = STATES[this.state];
    if (reserved) return base;                               // a theme can never recolour these
    return THEMES[this.theme][name] ?? base;
  }
  applyColour(colour = this.stateColour()) {
    rgb(colour).forEach((v, k) => { if (this.col[k].target !== v) this.col[k].set(v, 0.45); });
  }
  applyEmote(name) {
    const [wm, hm, rf, lid, br, ba] = EMOTES[name];
    this.emote = name;
    const w = BODY * wm, h = BODY * hm;
    this.bodyW.set(w); this.bodyH.set(h); this.radius.set(Math.min(w, h) * rf);
    if (!this.blinking) this.lid.set(lid);
    this.browRaise.set(br); this.browAngle.set(ba);
  }

  saccade(now) {
    const roll = rnd(100);
    const amp = roll < 60 ? rnd(25) / 100 : roll < 90 ? (rnd(35) + 25) / 100 : (rnd(40) + 60) / 100;
    const ang = rnd(628) / 100;
    this.aim(Math.cos(ang) * amp, Math.sin(ang) * amp * 0.55);
    this.nextSaccade = now + 320 + rnd(1300);
    if (Math.abs(this.gazeX.target) > 0.55) this.nextBlink = now;   // big shifts take a blink
  }
  aim(tx, ty) {
    // Main-sequence timing: ~20 ms plus 2.2 ms per degree over 30 degrees of full deflection.
    const deg = Math.hypot(tx - this.gazeX.value, ty - this.gazeY.value) * 30;
    const dur = Math.min(20 + deg * 2.2, 200) / 1000;
    this.gazeX.set(tx, dur); this.gazeY.set(ty, dur);
    this.asym.set(-tx * 0.13);            // the leading eye foreshortens
  }

  tick(now, dt) {
    if (!this.woke) { this.woke = true; this.lid.set(EMOTES[this.emote][3]); this.nextBlink = now + 2600; }
    if (!this.linked(now) && this.state >= 0) { this.applyColour(NO_LINK_COLOUR); }
    else if (this.linked(now)) this.applyColour();

    // Blink: shut over 70 ms, open again after 90 ms, every 2.2-5.2 s.
    if (!this.blinking && now >= this.nextBlink) {
      this.blinking = true; this.blinkEnd = now + 90;
      this.lid.snap(this.lid.value); this.lid.set(1); this.lid.k = 900; this.lid.c = 60;
    } else if (this.blinking && now >= this.blinkEnd) {
      this.blinking = false; this.lid.k = 170; this.lid.c = 19;
      this.lid.set(EMOTES[this.emote][3]);
      this.nextBlink = now + 2200 + rnd(3000);
    }

    // Where to look: a confident person beats wandering; confidence sets how far the eyes commit.
    const t = this.target;
    if (t && now - t.at < 700) {
      const amp = GAZE_AMP_FLOOR + (1 - GAZE_AMP_FLOOR) * Math.max(0, Math.min(1, t.conf));
      const tx = Math.max(-1, Math.min(1, t.x)) * amp, ty = Math.max(-1, Math.min(1, t.y)) * amp * GAZE_V;
      if (Math.hypot(tx - this.gazeX.target, ty - this.gazeY.target) > 0.04) this.aim(tx, ty);
      this.nextSaccade = now + 600;
    } else if (now >= this.nextSaccade) this.saccade(now);

    // Emote: pinned by the state, or by a show, or cycling every 4-9 s, never repeating.
    const locked = this.lockedEmote();
    if (locked) { if (this.emote !== locked) this.applyEmote(locked); }
    else if (this.pin) { if (this.emote !== this.pin) this.applyEmote(this.pin); }
    else if (this.free() && now >= this.nextEmote) {
      const names = Object.keys(EMOTES).filter((n) => n !== this.emote);
      this.applyEmote(names[rnd(names.length)]);
      this.nextEmote = now + 4000 + rnd(5000);
    }

    // Quotes: only while the face is free, slow enough to read.
    if (!this.free()) this.quote = -1;
    else if (now >= this.nextQuote || this.quote < 0) { this.quote = (this.quote + 1) % QUOTES.length; this.nextQuote = now + 9000; }

    for (const v of [this.bodyW, this.bodyH, this.radius, this.lid, this.browRaise, this.browAngle,
                     this.asym, this.gazeX, this.gazeY, ...this.col]) v.step(dt);
  }

  // What a skin draws, per eye.
  eyes() {
    const c = this.col.map((v) => Math.round(Math.max(0, Math.min(255, v.value))));
    const colour = `rgb(${c[0]},${c[1]},${c[2]})`;
    const dim = `rgb(${c.map((v) => Math.round(v * 0.35)).join(",")})`;
    return [0, 1].map((i) => ({
      side: i === 0 ? "left" : "right",
      cx: SLOT[i], cy: EYE_Y,
      w: this.bodyW.value * (1 + (i === 0 ? -this.asym.value : this.asym.value)),
      h: this.bodyH.value, radius: this.radius.value,
      lid: Math.max(0, Math.min(1, this.lid.value)),
      browRaise: this.browRaise.value, browAngle: this.browAngle.value,
      gazeX: this.gazeX.value, gazeY: this.gazeY.value,
      colour, dim, rgb: c,
    }));
  }
  quoteText() { return this.quote >= 0 ? QUOTES[this.quote] : ""; }
  stateName() { return this.state >= 0 ? STATES[this.state][0] : "-"; }
}
