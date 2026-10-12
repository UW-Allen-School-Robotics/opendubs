// The face's behaviour: what the Tab5 face does (firmware/tab5_idf/app_face/app_face.cpp), as a
// model with no drawing in it. A skin draws whatever this produces, so every skin blinks, emotes,
// saccades and looks where it is told in the same way.

import { Spring, Ease } from "./anim.js";
import { STATES, EMOTES, SHAPES, PLEASED_VARIANTS, THEMES, NO_LINK_COLOUR, QUOTES } from "./vocab.js";

export const DESIGN_W = 1280, DESIGN_H = 720;        // the Tab5's panel, the geometry's frame

// ---- PROTOTYPE PARAMETERS (notes/design/face-eyes-and-gaze.md) -------------------------------
// ?eyes=shape  expression through lid shape instead of scale
// ?eyes=full   the above, plus microsaccades, smooth pursuit and a mutual-gaze aversion timer
// ?quotes=0   no idle caption, so a side-by-side comparison has nothing moving to read
// ?eye=<px>    eye width            (ships: 480)
// ?ipd=<px>    interpupillary dist. (ships: 643 = 480 x 1.34)
//
// Read here, at module load, because SLOT is a module constant other files import. Guarded so the
// engine still imports under node, where the door's tests run it with no `location`.
const Q = (() => {
  try { return new URLSearchParams(location.search); } catch { return new URLSearchParams(""); }
})();
const num = (k, dflt) => { const v = parseFloat(Q.get(k)); return Number.isFinite(v) && v > 0 ? v : dflt; };

export const PROTO = Q.get("eyes") || "";            // "", "shape" or "full"

// ?emote=<NAME> pins one expression, which the side-by-side NEEDS: without it each page auto-cycles
// to a different random emote every few seconds and the two windows are never showing the same
// thing, so nothing can be compared. Prototype wiring, and the same name the protocol uses.
const PIN = (Q.get("emote") || "").toUpperCase();
const QUOTES_ON = Q.get("quotes") !== "0";     // ?quotes=0 for a clean side-by-side

// ?pleased=soft|crescent|deep swaps PLEASED's lids, so the three can be compared in three windows
// without a rebuild. Without it, the version that was reviewed is shown unchanged.
const PLEASED_V = Q.get("pleased");
const SHAPE_TABLE = PLEASED_V && PLEASED_VARIANTS[PLEASED_V]
  ? { ...SHAPES, PLEASED: PLEASED_VARIANTS[PLEASED_V] } : SHAPES;
const SHAPE_MODE = PROTO === "shape" || PROTO === "full";
const FULL_MODE  = PROTO === "full";

const BODY = num("eye", 480), GAP_FRAC = 0.34;
const IPD = num("ipd", BODY * (1 + GAP_FRAC));
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
    // The additive channels. Defaults reproduce today exactly: no lower lid, no curve, the pupil
    // at the 0.34 that was a constant in eyes.js.
    this.lidLower = new Spring(0); this.lidCurve = new Spring(0); this.pupil = new Spring(0.34);
    this.microX = 0; this.microY = 0; this.nextMicro = 0;
    this.avertUntil = 0; this.nextAvert = 0;
    this.col = rgb(NO_LINK_COLOUR).map((v) => new Ease(v, 0.45));
    this.blinking = false; this.blinkEnd = 0;
    this.nextBlink = 0; this.nextSaccade = 0; this.nextEmote = 0;
    this.quote = -1; this.nextQuote = 0;
    this.target = null;         // a person to look at: {x, y (-1..1), conf (0..1), at}
    this.woke = false;
    if (PIN) this.pin = PIN;    // see ?emote= above
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
  // The lid an emote rests at. PLEASED exists only in SHAPES, so EMOTES[name] can be undefined --
  // and the blink below needs this answer just as much as applyEmote does. It used to read
  // EMOTES[this.emote][3] directly, which threw on the first blink after PLEASED was pinned and
  // killed the render loop: the face froze mid-blink a few seconds after the page loaded. One rule
  // in one place, used by both.
  restingLid(name) {
    const shape = SHAPE_MODE ? SHAPE_TABLE[name] : null;
    if (shape) return shape.lu;
    return (EMOTES[name] ?? EMOTES.NEUTRAL)[3];
  }
  applyEmote(name) {
    const base = EMOTES[name];
    const shape = SHAPE_MODE ? SHAPE_TABLE[name] : null;
    // PLEASED exists only in SHAPES, so the baseline table may not have this name at all.
    if (!base && !shape) return;
    const [wm, hm, rf, lid, br, ba] = base ?? EMOTES.NEUTRAL;
    this.emote = name;

    // In prototype mode the SIZE comes from SHAPES, which keeps it at 1.0 for everything except
    // where a size change is the meaning -- so the eye stops changing shape to show a feeling.
    const w = BODY * (shape ? shape.wm : wm), h = BODY * (shape ? shape.hm : hm);
    this.bodyW.set(w); this.bodyH.set(h); this.radius.set(Math.min(w, h) * rf);
    if (!this.blinking) this.lid.set(this.restingLid(name));
    this.browRaise.set(br); this.browAngle.set(ba);
    if (shape) { this.lidLower.set(shape.ll); this.lidCurve.set(shape.lc); this.pupil.set(shape.pu); }
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
      this.lid.set(this.restingLid(this.emote));
      this.nextBlink = now + 2200 + rnd(3000);
    }

    // Where to look: a confident person beats wandering; confidence sets how far the eyes commit.
    const t = this.target;
    if (t && now - t.at < 700) {
      const amp = GAZE_AMP_FLOOR + (1 - GAZE_AMP_FLOOR) * Math.max(0, Math.min(1, t.conf));
      const tx = Math.max(-1, Math.min(1, t.x)) * amp, ty = Math.max(-1, Math.min(1, t.y)) * amp * GAZE_V;

      if (!FULL_MODE) {
        if (Math.hypot(tx - this.gazeX.target, ty - this.gazeY.target) > 0.04) this.aim(tx, ty);
        this.nextSaccade = now + 600;
      } else {
        // ---- MUTUAL-GAZE AVERSION (decision 3) ------------------------------------------------
        // hri-interaction-design.md: "multiple short fixations beat one long stare", and the
        // pattern it records is attract -> eye contact -> avert -> shift. Today's engine does the
        // first two and NEVER averts: `nextSaccade = now + 600` is rewritten on every frame a
        // confident target is present, so the robot holds a person's gaze for as long as they
        // stand there. That is the one failure a person actually notices.
        if (!this.nextAvert) this.nextAvert = now + 2600 + rnd(1600);
        if (now >= this.avertUntil && now >= this.nextAvert) {
          // Look away, briefly, and somewhere plausible rather than randomly: down and to one
          // side is what people do, and it reads as thought rather than distraction.
          const side = Math.random() < 0.5 ? -1 : 1;
          this.aim(tx + side * (0.28 + rnd(18) / 100), ty + 0.16 + rnd(12) / 100);
          this.avertUntil = now + 500 + rnd(700);
          this.nextAvert = this.avertUntil + 2200 + rnd(2600);
        }

        if (now >= this.avertUntil) {
          // ---- SMOOTH PURSUIT, with a catch-up saccade ---------------------------------------
          // Today a moving person produces a CHAIN OF SACCADES, because any movement over 0.04
          // re-aims. Eyes pursue a moving target smoothly and only saccade when they fall behind.
          const ex = tx - this.gazeX.value, ey = ty - this.gazeY.value;
          const err = Math.hypot(ex, ey);
          if (err > 0.22) {
            this.aim(tx, ty);                       // too far behind: catch up with a saccade
          } else if (err > 0.002) {
            const vmax = 0.9;                       // gaze units per second (~27 deg/s)
            const step = Math.min(err, vmax * dt);
            this.gazeX.snap(this.gazeX.value + (ex / err) * step);
            this.gazeY.snap(this.gazeY.value + (ey / err) * step);
            this.asym.set(-this.gazeX.value * 0.13);
          }
        }
        this.nextSaccade = now + 600;
      }
    } else if (now >= this.nextSaccade) {
      this.saccade(now);
      this.nextAvert = 0;                           // nobody to avert from
    }

    // ---- MICROSACCADES ----------------------------------------------------------------------
    // Between saccades today's gaze is perfectly still, which is the frozen quality most robot
    // eyes have. A fraction of a degree of jitter, a few times a second, is most of the difference
    // between alive and paused. Applied in eyes() rather than to the target, so it cannot
    // accumulate or fight the pursuit integrator above.
    if (FULL_MODE && now >= this.nextMicro) {
      this.microX = (Math.random() - 0.5) * 0.016;   // ~0.25 deg at 30 deg full deflection
      this.microY = (Math.random() - 0.5) * 0.010;
      this.nextMicro = now + 90 + rnd(220);
    }

    // Emote: pinned by the state, or by a show, or cycling every 4-9 s, never repeating.
    const locked = this.lockedEmote();
    if (locked) { if (this.emote !== locked) this.applyEmote(locked); }
    else if (this.pin) { if (this.emote !== this.pin) this.applyEmote(this.pin); }
    else if (this.free() && now >= this.nextEmote) {
      const table = SHAPE_MODE ? SHAPES : EMOTES;        // SHAPES adds PLEASED
      const names = Object.keys(table).filter((n) => n !== this.emote);
      this.applyEmote(names[rnd(names.length)]);
      this.nextEmote = now + 4000 + rnd(5000);
    }

    // Quotes: only while the face is free, slow enough to read.
    // ?quotes=0 keeps the idle caption off entirely. For a side-by-side comparison a line of text
    // that changes every nine seconds, differently in each window, is something to read rather than
    // something to judge. main.js needs no change: it draws whatever quoteText() returns.
    if (!QUOTES_ON || !this.free()) this.quote = -1;
    else if (now >= this.nextQuote || this.quote < 0) { this.quote = (this.quote + 1) % QUOTES.length; this.nextQuote = now + 9000; }

    for (const v of [this.bodyW, this.bodyH, this.radius, this.lid, this.browRaise, this.browAngle,
                     this.asym, this.gazeX, this.gazeY, this.lidLower, this.lidCurve, this.pupil,
                     ...this.col]) v.step(dt);
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
      gazeX: this.gazeX.value + this.microX, gazeY: this.gazeY.value + this.microY,
      // ADDITIVE, all optional. A skin written before these existed ignores them, and these
      // defaults are what eyes.js already draws -- so asset.js and ring.js need no change.
      lidUpper: Math.max(0, Math.min(1, this.lid.value)),
      lidLower: Math.max(0, Math.min(1, this.lidLower.value)),
      lidCurve: Math.max(-1, Math.min(1, this.lidCurve.value)),
      pupil: this.pupil.value,
      colour, dim, rgb: c,
    }));
  }
  quoteText() { return this.quote >= 0 ? QUOTES[this.quote] : ""; }
  stateName() { return this.state >= 0 ? STATES[this.state][0] : "-"; }
}
