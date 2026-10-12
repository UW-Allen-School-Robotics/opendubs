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

// ---- MOVES: one-shot gestures a show fires on a beat (head protocol v4, `MOVE <name>`) ---------
//
// A move LAYERS ON TOP of everything the face is already doing and then goes away. It never touches
// the emote, the pin, the blink schedule or where the eyes are looking, so `EMOTE SOFT` plus
// `MOVE wink_l` is a wink *while* soft, and the face carries on afterwards exactly as it would
// have. That is what makes a move safe to fire on any beat of a show.
//
// EACH IS A PURE FUNCTION OF ITS OWN AGE, for the reason the fireworks skin is: the face can be
// drawn at any rate, or skipped while a tab is hidden, and the gesture still looks the same instead
// of drifting or catching up in a rush. It also means a move can simply be ASKED what it looks like
// at a given moment, which is how the peak offsets below are measured rather than estimated.
//
// `peak` IS A CONTRACT WITH THE SHOW RUNNER, like SPIN's ease_s/2. It is the delay from receiving
// the line to the instant the gesture reads -- the lid fully shut, the lowest point of the bob --
// so the runner sends the line early by it and the gesture lands ON the beat. The numbers here are
// the design; `tests/test_moves.mjs` measures the actual curve and fails if the two disagree by
// more than a frame, so a shape cannot be retuned without the published number following it.
// CHANGING ONE SILENTLY PUTS EVERY SHOW USING IT OFF THE BEAT. Tell Choreography.
//
// Why the shapes are not symmetric: a real lid snaps shut and opens more slowly, so `shut` closes
// in a third of the move and opens over the rest. A symmetric curve reads as a machine.

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const ss = (x) => { const c = clamp01(x); return c * c * (3 - 2 * c); };   // smoothstep
// Rises to 1 over `up`, holds for `hold`, falls over what is left. Fractions of the whole move.
const pulse = (p, up, hold) =>
  p < up ? ss(p / up) : p < up + hold ? 1 : 1 - ss((p - up - hold) / Math.max(1e-6, 1 - up - hold));

const shut = (p) => pulse(p, 0.33, 0.10);          // a lid: quick to close, slower to open

// KEEP EACH ENTRY ON ONE LINE, `dur:` BEFORE `peak:`. Two other lanes read this table as TEXT --
// the head protocol's test_head_protocol.py and the show validator's test_face_moves.py, neither of
// which can import a JS object from Python -- so wrapping an entry or swapping those two keys turns
// their suites red while changing nothing here. tests/test_moves.mjs reproduces both patterns, so
// that fails on YOUR run first, with the reason.
export const MOVES = {
  // name:   how long it lasts (ms), when it reads (ms), and what it does at age fraction p
  wink_l:  { dur: 420, peak: 139, f: (p) => ({ lidL: shut(p) }) },
  wink_r:  { dur: 420, peak: 139, f: (p) => ({ lidR: shut(p) }) },
  // Two shuts. The SECOND is the one that lands -- a double blink reads on its second beat, not
  // its first -- so the peak is deliberately not the first maximum.
  blink2:  { dur: 520, peak: 333, f: (p) => { const l = Math.max(shut(clamp01(p / 0.42)), shut(clamp01((p - 0.5) / 0.42))); return { lidL: l, lidR: l }; } },
  look_l:  { dur: 700, peak: 231, f: (p) => ({ gx: -0.85 * pulse(p, 0.33, 0.18) }) },
  look_r:  { dur: 700, peak: 231, f: (p) => ({ gx: +0.85 * pulse(p, 0.33, 0.18) }) },
  // Up, around and back. It reads at the TOP of the circle, which is where it starts to look like
  // an eye roll rather than a wander.
  roll:    { dur: 760, peak: 148, f: (p) => { const a = ss(p) * Math.PI * 2 - Math.PI / 2, r = Math.sin(Math.PI * clamp01(p * 1.15)); return { gx: Math.cos(a) * 0.7 * r, gy: Math.sin(a) * 0.55 * r }; } },
  // Down then up: the eyes drop and come back, which reads as a bounce on the beat. In eye-heights.
  bob:     { dur: 460, peak: 138, f: (p) => ({ dy: 0.17 * pulse(p, 0.30, 0.06) }) },
  sparkle: { dur: 560, peak: 124, f: (p) => ({ sparkle: pulse(p, 0.22, 0.04) }) },
};

const NO_MOVE = { lidL: 0, lidR: 0, gx: 0, gy: 0, dy: 0, sparkle: 0 };

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
    this.mv = null;             // the one move in flight: {name, t0}; see move()
    this.mvOut = NO_MOVE;       // what it contributes this frame
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

  // Head protocol v4, `MOVE <name>`: a one-shot gesture, fired on a beat by a show. The name is
  // lowercase and already validated by protocol.js; an unknown one is refused there, so reaching
  // here with one is a bug rather than a show's typo, and saying so is more use than ignoring it.
  //
  // REPLACE, NOT QUEUE. A show firing moves on consecutive beats must stay on the beat; a queue
  // would make each one wait for the last and drift further behind with every beat. Replacing
  // means the newest gesture always starts now, which is what landing on a beat requires.
  move(name, now) {
    if (!(name in MOVES)) return false;
    this.mv = { name, t0: now };
    return true;
  }

  // What the move in flight contributes right now. A pure function of its age, so a frame skipped
  // or a frame drawn late changes nothing about how it looks.
  stepMove(now) {
    if (!this.mv) { this.mvOut = NO_MOVE; return; }
    const m = MOVES[this.mv.name], age = now - this.mv.t0;
    if (age >= m.dur) { this.mv = null; this.mvOut = NO_MOVE; return; }
    this.mvOut = { ...NO_MOVE, ...m.f(clamp01(age / m.dur)) };
  }

  // ---- the model ------------------------------------------------------------------------------
  linked(now) { return this.state >= 0 && now - this.stateAt < LINK_STALE_MS; }
  lockedEmote() { return this.state >= 0 ? STATES[this.state][3] : null; }
  free() { return this.state >= 0 && !STATES[this.state][3]; }
  reserved() { return this.state >= 0 && STATES[this.state][4]; }

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

    this.stepMove(now);

    for (const v of [this.bodyW, this.bodyH, this.radius, this.lid, this.browRaise, this.browAngle,
                     this.asym, this.gazeX, this.gazeY, ...this.col]) v.step(dt);
  }

  // What a skin draws, per eye.
  eyes() {
    const m = this.mvOut;
    const c = this.col.map((v) => Math.round(Math.max(0, Math.min(255, v.value))));
    const colour = `rgb(${c[0]},${c[1]},${c[2]})`;
    const dim = `rgb(${c.map((v) => Math.round(v * 0.35)).join(",")})`;
    return [0, 1].map((i) => ({
      side: i === 0 ? "left" : "right",
      cx: SLOT[i], cy: EYE_Y + m.dy * this.bodyH.value,
      w: this.bodyW.value * (1 + (i === 0 ? -this.asym.value : this.asym.value)),
      h: this.bodyH.value, radius: this.radius.value,
      // A move adds to the lid rather than replacing it, so a wink during SLEEPY still shuts from
      // an already-low lid instead of snapping the eye open to wink it.
      lid: clamp01(this.lid.value + (i === 0 ? m.lidL : m.lidR)),
      browRaise: this.browRaise.value, browAngle: this.browAngle.value,
      // Likewise the gaze: a move nudges where the eyes already are. Clamped, so a look during a
      // saccade to the same side cannot leave the eye's range.
      gazeX: clamp01((this.gazeX.value + m.gx + 1) / 2) * 2 - 1,
      gazeY: clamp01((this.gazeY.value + m.gy + 1) / 2) * 2 - 1,
      sparkle: m.sparkle,       // 0..1, THE ONE FIELD A SKIN MAY IGNORE (face-engine.md A)
      colour, dim, rgb: c,
    }));
  }
  quoteText() { return this.quote >= 0 ? QUOTES[this.quote] : ""; }
  stateName() { return this.state >= 0 ? STATES[this.state][0] : "-"; }
}
