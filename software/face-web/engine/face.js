// The face's behaviour: what the Tab5 face does (firmware/tab5_idf/app_face/app_face.cpp), as a
// model with no drawing in it. A skin draws whatever this produces, so every skin blinks, emotes,
// saccades and looks where it is told in the same way.

import { Spring, Ease } from "./anim.js";
import { random, setRandom } from "./rng.js";
import { STATES, EMOTES, SHAPES, THEMES, NO_LINK_COLOUR, QUOTES } from "./vocab.js";
import { loadThemes } from "./themes.js";

export { setRandom };          // the engine's random source (rng.js); an offline render seeds it

export const DESIGN_W = 1280, DESIGN_H = 720;        // the Tab5's panel, the geometry's frame
const BODY = 480, GAP_FRAC = 0.34;
const IPD = BODY * (1 + GAP_FRAC);
export const SLOT = [DESIGN_W / 2 - IPD / 2, DESIGN_W / 2 + IPD / 2];
export const EYE_Y = DESIGN_H / 2;
const LINK_STALE_MS = 5000;
// The ARMED lease, matching the protocol's (head protocol v5): the show runner renews every few
// seconds, and if it stops the sign must clear itself rather than claim for ever that the robot is
// about to move. That is the one failure mode that matters for a sign people stand next to.
const ARMED_LEASE_MS = 3000;
// Three missed BATT refreshes (the dashboard sends one every 30 s). See chargeState().
const BATT_STALE_MS = 90000;
const GAZE_AMP_FLOOR = 0.35, GAZE_V = 0.55;
// The spoken-turn states the voice page reports. `idle` is the absence of the others.
const CONVO = ["idle", "listen", "think", "speak"];
// A BACKSTOP, not a lease: states hold until changed, and this only stops a dropped socket leaving
// the robot visibly thinking for ever. Long on purpose -- a slow model is not a fault.
const CONVO_BACKSTOP_MS = 60000;
// Turn outcomes that get NO reaction: either nothing went wrong, or the design says stay quiet.
// See convoReact() for why each one is here.
const NO_REACTION = ["ok", "not_addressed", "cut", "declined"];
// "Didn't catch that": long enough to read as a wince, short enough not to become an expression.
const REACT_MS = 700;
// HOW LONG A REPORTED TARGET IS BELIEVED. The trackers report at 5 Hz or better, so this is
// several missed reports: long enough never to flicker, short enough that the face does not keep
// staring at where somebody used to be.
//
// ONE CONSTANT, USED BY BOTH THE EYES AND THE RETICLE, which is the whole reason it is named. It
// was a bare 700 in the gaze path and I gave the reticle its own 500 -- so the box would have
// vanished while the eyes were still following, and the diagnostic would have disagreed with the
// thing it is there to explain. That is worse than no diagnostic.
const TARGET_FRESH_MS = 700;
// How far from the design edge the reticle's CENTRE may travel. skins/reticle.js clamps the box
// itself; this keeps the centre somewhere sensible before it ever gets there.
const RETICLE_MARGIN = 90;

// `?mouth=1` turns the optional mouth on for testing and for the settings page's own preview. The
// PREFERENCE lives on the robot and the page calls setMouth(); this is only a way to see it without
// one. Wrapped because the engine also runs headless in tests, where there is no location.
const Q = (() => { try { return new URLSearchParams(location.search); } catch { return new URLSearchParams(""); } })();
const MOUTH_ON = Q.get("mouth") === "1";

const rnd = (n) => Math.floor(random() * n);
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
  // A sideways glance with the lids half down -- the look, not a full turn of the eyes. A PAIR, as
  // the winks are: the choreography glances both ways (the wave, the sway), and renaming once shows
  // used a single `side_eye` would break them, so the pair is cheaper now than later.
  side_eye_l: { dur: 900, peak: 198, f: (p) => { const g = pulse(p, 0.22, 0.42); return { gx: -0.75 * g, lidL: 0.30 * g, lidR: 0.30 * g }; } },
  side_eye_r: { dur: 900, peak: 198, f: (p) => { const g = pulse(p, 0.22, 0.42); return { gx: +0.75 * g, lidL: 0.30 * g, lidR: 0.30 * g }; } },
  // Straight out at the viewer, and HELD: the one move that arrives somewhere rather than nudging.
  to_camera: { dur: 820, peak: 164, f: (p) => ({ pull: pulse(p, 0.20, 0.45) }) },
  // Look, look away, then snap back wide-eyed. The gesture is the SNAP, so that is what it reads on.
  double_take: { dur: 1150, peak: 632, f: (p) => { const a = clamp01(p / 0.45), b = clamp01((p - 0.45) / 0.55); return { gx: 0.70 * pulse(a, 0.25, 0.35) * (1 - ss(b * 2)), pull: ss(b * 2.2), scale: 0.16 * pulse(b, 0.18, 0.12) }; } },
  // The eyes widen fast and settle. A show uses it for a hit; HRI uses it as a reaction, so it is
  // deliberately short and needs nothing held afterwards.
  startle: { dur: 700, peak: 84, f: (p) => { const g = pulse(p, 0.12, 0.10); return { scale: 0.22 * g, lidL: -0.5 * g, lidR: -0.5 * g }; } },
};

// `scale` and `pull` are not new per-eye FIELDS: they change `w`, `h`, `radius` and the gaze the
// eye object already carries, so every skin gets them with no change and `sparkle` is still the only
// input a skin may ignore (face-engine.md A). That is what keeps "no new lid shapes" true -- these
// four moves are the current geometry moved differently, not a new expression.
//
// WHY `pull` EXISTS. Every other move OFFSETS the gaze, which is right for a glance: it nudges the
// eyes from wherever they are. But `to_camera` has to arrive at a PLACE -- straight out at the
// viewer -- and an offset cannot do that, because where it lands depends on where the eyes happened
// to be looking. `pull` lerps the gaze toward centre instead, so the eyes meet the viewer whether
// they were wandering left, tracking a person, or already still.
const NO_MOVE = { lidL: 0, lidR: 0, gx: 0, gy: 0, dy: 0, sparkle: 0, scale: 0, pull: 0 };

export class Face {
  constructor() {
    this.state = -1;            // index into STATES; -1 = never heard from the robot
    this.soc = null;            // pack state of charge, 0..100; null = the BMS did not say
    this.battCharging = false;  // current flowing INTO the pack
    this.battAt = -1e9;         // when BATT last arrived, for staleness
    this.stateAt = 0;
    this.emote = "NEUTRAL";
    this.pin = null;            // an EMOTE <name> pins; EMOTE AUTO releases
    this.theme = "plain";
    this.contentThemes = new Map();   // id -> colours, from the content folder via the looks document
    this.bodyW = new Spring(BODY); this.bodyH = new Spring(BODY);
    this.radius = new Spring(BODY * 0.205);
    this.lid = new Spring(1);   // shut at first: the eyes open once, as the face's first act
    this.lidLower = new Spring(0); this.lidCurve = new Spring(0);
    this.browRaise = new Spring(0); this.browAngle = new Spring(0);
    this.asym = new Spring(0);
    this.gazeX = new Ease(0); this.gazeY = new Ease(0);
    this.col = rgb(NO_LINK_COLOUR).map((v) => new Ease(v, 0.45));
    this.blinking = false; this.blinkEnd = 0;
    this.nextBlink = 0; this.nextSaccade = 0; this.nextEmote = 0;
    this.quote = -1; this.nextQuote = 0;
    this.target = null;         // a person to look at: {x, y (-1..1), conf (0..1), at, kind?}
    this.convo = "idle";        // the spoken turn: idle | listen | think | speak (setConvo)
    this.convoAt = 0;           // when it last changed; 0 = idle, and the backstop's reference
    this.reactAt = 0;           // a "didn't catch that" wince, if one is running
    this.woke = false;
    this.nextAvert = 0; this.avertUntil = 0;   // the gaze break; see tick()
    this.microX = 0; this.microY = 0; this.nextMicro = 0;
    // The optional mouth (skins/mouth.js). OFF unless something turns it on, so a face that never
    // calls setMouth draws exactly what it has always drawn.
    this.mouth = MOUTH_ON;
    this.mouthLevel = 0;        // what is drawn: smoothed, and decaying on its own
    this.mouthFeed = 0;         // the last envelope handed in
    this.talkUntil = 0;         // a manual talk, for shows and for looking at it
    // ARMED (head protocol v5): a show is loaded and starts when the operator holds the deadman.
    this.armed = null;          // null | "wait" | seconds remaining
    this.armedAt = -1e9;        // when it was last set, for the lease
    this._now = 0;              // the last tick's clock, so both seams need no `now` argument
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
    if (!(n in THEMES) && !this.contentThemes.has(n)) return false;
    this.theme = n; this.applyColour();
    return true;
  }
  /**
   * Take the content folder's themes, from the looks document. Returns the problems, for the
   * settings page to show; an empty list means every theme offered was accepted.
   *
   * IT TAKES THE DOCUMENT AND DOES ITS OWN READING, rather than being handed a map of colours,
   * and that is the point: there is exactly one door into the engine and no way to put colours
   * through it that skipped the guard (themes.js says what is refused, and why CHARGING is among
   * it). Await it before the first `THEME` can arrive.
   */
  async loadContentThemes(doc, themesBase) {
    const { themes, problems } = await loadThemes(doc, themesBase);
    this.contentThemes = themes;
    // A THEME CAN GO AWAY while it is the one showing -- the content folder is redeployed, or an
    // edit to it introduces a problem that gets it refused. Falling back to plain keeps the face
    // honest and, more to the point, keeps `stateColour` from reading colours off nothing.
    if (!(this.theme in THEMES) && !themes.has(this.theme)) { this.theme = "plain"; this.applyColour(); }
    return problems;
  }
  // From a tracker; null to let the eyes wander. `kind` ("motion" | "face" | "person") is
  // optional, carried only so the reticle can say WHICH cue this is -- the engine's own behaviour
  // has never depended on it and still does not.
  look(target) { this.target = target; }

  // WHERE THE FACE THINKS THE TARGET IS, in design coordinates, for the tracking reticle.
  //
  // THE POINT OF THIS LIVING HERE rather than in the page: it is derived from THE GAZE THAT DRIVES
  // THE EYES, after every transform the gaze goes through, so the reticle shows what the face
  // actually believes. A reticle computed in the page from the detector's box would sit happily on
  // the person all through a runaway -- in a runaway the detector is right and the MAPPING is
  // wrong, which is exactly the failure it is meant to expose. Putting it here makes that property
  // structural instead of something a caller has to remember.
  //
  // Null when there is nobody: the reticle is a diagnostic, and a box drawn over an empty room
  // would be a lie of the kind that makes a diagnostic worse than none.
  reticleTarget(now = this._now) {
    const t = this.target;
    if (!t || !Number.isFinite(t.x) || !Number.isFinite(t.y)) return null;
    if (typeof t.at === "number" && !(now - t.at < TARGET_FRESH_MS)) return null;
    // THE TARGET, NOT THE GAZE, and the difference is the whole design. I had this the other way
    // round until the maintainers said what they actually wanted from it: "the eyes SEEM to be
    // tracking but I have no idea what you're seeing or even when you're looking." Where the eyes
    // POINT is already on screen -- they are the largest thing on the face. What is invisible is
    // what the robot has DETECTED. So the box marks the detection and you judge the eyes against
    // it; a box derived from the gaze would only ever agree with the eyes and could never
    // contradict them, which makes it decoration.
    //
    // It is still not the detector's rectangle: this is `t.x`/`t.y` as the ENGINE holds them, the
    // same numbers the gaze path consumes, so the box and the eyes are fed from one value. During
    // a gaze break the two separate on purpose -- the box stays on the person while the eyes look
    // away -- and that is correct: the face still knows where you are, it is choosing not to
    // stare. A test pins that, so nobody "fixes" it later.
    const gx = Math.max(-1, Math.min(1, t.x));
    const gy = Math.max(-1, Math.min(1, t.y));
    return {
      x: DESIGN_W / 2 + gx * (DESIGN_W / 2 - RETICLE_MARGIN),
      y: DESIGN_H / 2 + gy * (DESIGN_H / 2 - RETICLE_MARGIN),
      kind: t.kind === "face" || t.kind === "person" ? t.kind : "motion",
      conf: typeof t.conf === "number" ? t.conf : 0,
    };
  }

  // ---- the optional mouth -----------------------------------------------------------------------
  // The page owns the preference and the audio; the engine owns the drawing. ONE BOOLEAN AND ONE
  // NUMBER cross that line, which is what keeps the AnalyserNode out of here entirely (the voice
  // research note, section 5: the speaking animation runs from the audio being played, locally).
  setMouth(on) { this.mouth = !!on; if (!this.mouth) { this.mouthLevel = 0; this.mouthFeed = 0; } }

  // An envelope, 0..1, from the audio the page is playing. Call it as often as you like: the level
  // is smoothed, so 20 Hz and 60 Hz look the same.
  setMouthLevel(x) { this.mouthFeed = Math.max(0, Math.min(1, Number(x) || 0)); }

  // A manual talk, for a show or for looking at it: a generic speaking rhythm for `ms`, with no
  // audio behind it. Deliberately NOT a lip-sync -- it is the same one number, moved by a timer.
  talk(ms = 1500, now = this._now) { this.talkUntil = now + Math.max(0, ms); }

  stepMouth(now, dt) {
    // THE ENVELOPE IS COMPUTED WHETHER OR NOT THE MOUTH IS DRAWN. It used to return early with the
    // mouth off, which was right when the level had only one consumer. It now has two: with the
    // mouth enabled it opens the mouth, and with the mouth OFF the same number drives the brows
    // and the nod while the robot speaks (see convo()). A face with no mouth should still look
    // like it is talking, and the honest source for that is the audio actually playing -- not a
    // second rhythm invented beside it, which would drift out of step with the words.
    //
    // Nothing is drawn as a result: eyes() still reports `mouth: null` unless it is enabled, so a
    // face with the mouth off looks exactly as it did except for the speaking movement.
    if (!this.mouth && !this.convoAt) { this.mouthLevel = 0; return; }
    // A manual talk makes its own envelope: two overlapping rates, so it does not read as a pulse.
    let want = this.mouthFeed;
    if (now < this.talkUntil) {
      const s = now / 1000;
      want = Math.max(want, 0.28 + 0.34 * Math.abs(Math.sin(s * 9.1)) + 0.22 * Math.abs(Math.sin(s * 3.7)));
    }
    // Open quickly, close slowly: speech starts faster than it stops, and a mouth that closes as
    // fast as it opens flickers. The close is also what makes a feed that STOPS look like silence
    // rather than a held vowel -- the level decays to shut on its own, with nothing to turn it off.
    const k = want > this.mouthLevel ? 22 : 7;
    this.mouthLevel += (want - this.mouthLevel) * Math.min(1, k * dt);
    if (this.mouthLevel < 0.004) this.mouthLevel = 0;
    this.mouthFeed *= Math.max(0, 1 - 6 * dt);     // the feed itself fades, so one stale call cannot hold it
  }

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

  // ---- ARMED --------------------------------------------------------------------------------
  // `state` is null, "wait", or the seconds remaining. The page owns where it comes from; the
  // engine owns what it looks like and how long it may be believed.
  setArmed(state, now = this._now) {
    this.armed = state === null || state === undefined ? null : state;
    this.armedAt = now;
  }

  // What to draw right now, LEASED. If the runner dies mid-show nothing sends OFF, so this expires
  // on its own. A countdown also keeps counting BETWEEN renewals, rather than freezing on the last
  // number it was told and reading as stuck.
  armedState(now = this._now) {
    if (this.armed === null) return null;
    const age = now - this.armedAt;
    if (age > ARMED_LEASE_MS) return null;
    if (typeof this.armed === "number") {
      const left = this.armed - age / 1000;
      return left > 0 ? left : 0;          // 0 still draws the frame: armed, just out of countdown
    }
    return this.armed;
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
  // ---- THE CONVERSATION ------------------------------------------------------------------------
  // What the robot is doing in a spoken turn: `idle`, `listen`, `think` or `speak`, from the voice
  // page. The page owns when; this owns what it looks like.
  //
  // IT IS NOT A SECOND GAZE SOURCE, and that is the design. The eyes already attend to a person the
  // tracker found. A voice gives us NO DIRECTION -- there is no direction-of-arrival here -- so a
  // "listening" look that pointed the eyes somewhere new would be inventing a speaker's position.
  // What LISTEN does instead is hold the attention the face already has: it stops the idle
  // wandering and the gaze break, and opens the lids a little. Attention, not a new heading.
  setConvo(state, now = this._now) {
    const s = String(state || "idle").toLowerCase();
    this.convo = CONVO.includes(s) ? s : "idle";
    this.convoAt = this.convo === "idle" ? 0 : now;
  }

  // A brief reaction to a turn that did not land -- the face for "say that again". Short, and it
  // does not change the conversation state: the page decides what happens next.
  //
  // THE SILENT STATUSES GET NO FACE, and that is the correction that matters here. The wire
  // contract (software/voice/wire.py) makes `not_addressed` SILENT by design: a wake that turned
  // out not to be one ends with nothing shown, which is what lets the wake threshold be tuned for
  // recall. A face that winced at every false wake would make the robot visibly twitch at
  // background noise, and a loose threshold would stop being cheap. `cut` is the same -- barge-in,
  // a show starting, or the page leaving are not failures to understand.
  //
  // `declined` gets no face either, and that one is this lane's judgement rather than the
  // contract's: the model understood perfectly and chose to refuse, and a canned decline is
  // spoken. A wince means "that did not land", so showing one would have the face say something
  // untrue about the robot's own state -- the same error as a hard-coded position or an invented
  // battery level, in a different place.
  //
  // What is left is every status where the robot could not answer: unintelligible, busy, off,
  // timeout, offline.
  convoReact(status, now = this._now) {
    if (NO_REACTION.includes(String(status))) return;
    this.reactAt = now;
  }

  // What the conversation is doing right now, or null. A RESERVED STATE WINS OUTRIGHT: DEGRADED and
  // E_STOP replace the skin and pin the emote, and a face mid-"thinking" while something is wrong
  // would be discussing the weather during a fire.
  //
  // The BACKSTOP is not the mechanism. States arrive on change and hold, as the PRISM's colour
  // does; this only stops a dropped socket leaving the robot visibly thinking for ever. It is
  // deliberately long, because a real pause for a slow model is a normal thing and cutting it short
  // would make the face lie in the other direction.
  convoState(now = this._now) {
    if (this.reserved() || !this.convoAt) return null;
    if (now - this.convoAt > CONVO_BACKSTOP_MS) return null;
    return this.convo;
  }

  // The conversation's contribution to the eyes, as an additive channel beside a MOVE's. Additive
  // so it composes with the emote, the tracker and a show's gestures instead of fighting them.
  convoOut(now = this._now) {
    const out = { gy: 0, dy: 0, brow: 0, lid: 0, squint: 0 };
    const c = this.convoState(now);

    // "Didn't catch that": a short squint, and it outlasts nothing. Allowed during any convo state
    // and on its own, because the turn it answers has already ended.
    if (!this.reserved() && this.reactAt) {
      const age = now - this.reactAt;
      if (age < REACT_MS) {
        const u = age / REACT_MS;                      // in and out, so it reads as a wince
        out.squint = Math.sin(u * Math.PI) * 0.5;
      } else { this.reactAt = 0; }
    }

    if (c === "listen") {
      // Open a touch, brows a touch up. Small: a face that widens its eyes every time somebody
      // speaks reads as alarm rather than attention.
      out.lid = -0.06; out.brow = 0.18;
    } else if (c === "think") {
      // HONEST, which was the brief. The robot is NOT looking at you while it thinks, so it looks
      // up and away and its lids come down a little -- the posture people read as working on it,
      // and the opposite of the attentive one. A face that held eye contact while a model ran
      // would be claiming to listen to a person it has stopped listening to.
      out.gy = -0.30; out.brow = -0.12; out.lid = 0.10;
    } else if (c === "speak") {
      // FROM THE AUDIO ACTUALLY PLAYING, the same envelope the mouth uses. With the mouth enabled
      // the mouth carries it and this stays small; with the mouth off this is the whole of it --
      // the brows lift and the eyes nod on the syllables, which is what makes a mouthless face
      // look like it is speaking rather than staring while sound comes out.
      const e = this.mouthLevel;
      out.brow = 0.35 * e;
      out.dy = 0.022 * e;                              // a nod, in body-heights, as a MOVE's dy is
    }
    return out;
  }

  // ---- CHARGING ------------------------------------------------------------------------------
  // The pack's state of charge, from the head protocol's BATT (agreed with the Tablet lane). The
  // page owns where it comes from; the engine owns what may be believed and for how long.
  //
  // `soc` is 0..100 or null for "the BMS did not say". NULL IS A REAL ANSWER AND IS KEPT AS ONE.
  // The Tab5's gauge shipped with `g_soc = 0.62f` and nothing ever fed it, so the band drew a
  // confident 62% that meant nothing -- a made-up number is worse than no number, because no
  // number is obviously no number. Nothing here ever invents a level.
  //
  // `charging` is whether current is flowing INTO the pack. A full pack still on the charger
  // reports 0, because the BMS cannot tell that from unplugged; it picks the WORD ("Charging" vs
  // "On charge"), never whether the gauge is shown.
  setBatt(soc, charging, now = this._now) {
    const n = typeof soc === "number" && isFinite(soc) ? soc : null;
    this.soc = n === null ? null : Math.max(0, Math.min(100, Math.round(n)));
    this.battCharging = Boolean(charging);
    this.battAt = now;
  }

  // What the gauge should show, or null for "do not draw it".
  //
  // THE STATE DECIDES WHETHER, THE MESSAGE DECIDES WHAT. The gauge appears because the robot says
  // it is CHARGING, not because a BATT arrived -- one authority for "what the robot is doing",
  // which is the same rule the rest of the face follows. A BATT with no CHARGING state draws
  // nothing, and that disagreement is better shown as nothing than as a gauge the state denies.
  //
  // A RESERVED STATE WINS, as everywhere else: DEGRADED and E_STOP replace the skin, and a charge
  // gauge drawn over a fault would be the face discussing the battery while something is wrong.
  // It cannot happen in practice -- CHARGING is itself a state, so it cannot be E_STOP too -- and
  // it is still written down, because "cannot happen" is how it happens.
  chargeState(now = this._now) {
    if (this.reserved()) return null;
    if (this.stateName() !== "CHARGING") return null;
    // STALE, so a dead feed does not freeze a number on the face for ever. The dashboard refreshes
    // every 30 s, so this is three missed refreshes: long enough never to flicker on a slow link,
    // short enough that nobody reads a half-hour-old percentage as current. The GAUGE stays --
    // the robot is still charging, the state says so -- and only the number goes.
    const fresh = this.soc !== null && (now - this.battAt) <= BATT_STALE_MS;
    return { soc: fresh ? this.soc : null, charging: this.battCharging };
  }

  // The line that goes where the idle quote goes. Null when there is nothing to say, so the page
  // can slot it into the precedence it already has for a song and a quote.
  chargeText(now = this._now) {
    const c = this.chargeState(now);
    if (!c) return null;
    // "On charge" rather than "Charged" when no current is flowing: a full pack on the charger and
    // a pack that has stopped taking current for some other reason look identical from here, and
    // only one of those is "charged". The honest word covers both.
    const word = c.charging ? "Charging" : "On charge";
    return c.soc === null ? word : `${word} ${c.soc}%`;
  }

  reserved() { return this.state >= 0 && STATES[this.state][4]; }

  stateColour() {
    if (this.state < 0) return NO_LINK_COLOUR;
    const [name, , base, , reserved] = STATES[this.state];
    if (reserved) return base;                               // a theme can never recolour these
    const colours = THEMES[this.theme] ?? this.contentThemes.get(this.theme) ?? {};
    return colours[name] ?? base;
  }
  applyColour(colour = this.stateColour()) {
    rgb(colour).forEach((v, k) => { if (this.col[k].target !== v) this.col[k].set(v, 0.45); });
  }
  applyEmote(name) {
    const [, , rf, , br, ba] = EMOTES[name];
    const shape = SHAPES[name];
    this.emote = name;
    // The SIZE comes from SHAPES, which holds it at 1.0 except where a size change IS the meaning,
    // so the eye stops changing shape to show a feeling and the lids do it instead.
    const w = BODY * shape.wm, h = BODY * shape.hm;
    this.bodyW.set(w); this.bodyH.set(h); this.radius.set(Math.min(w, h) * rf);
    if (!this.blinking) this.lid.set(shape.lu);
    this.lidLower.set(shape.ll); this.lidCurve.set(shape.lc);
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
      this.lid.set(SHAPES[this.emote].lu);       // the same resting lid applyEmote sets
      this.nextBlink = now + 2200 + rnd(3000);
    }

    // Where to look: a confident person beats wandering; confidence sets how far the eyes commit.
    const t = this.target;
    if (t && now - t.at < TARGET_FRESH_MS) {
      const amp = GAZE_AMP_FLOOR + (1 - GAZE_AMP_FLOOR) * Math.max(0, Math.min(1, t.conf));
      const tx = Math.max(-1, Math.min(1, t.x)) * amp, ty = Math.max(-1, Math.min(1, t.y)) * amp * GAZE_V;

      // ---- THE GAZE BREAK (#136, decision 5) -------------------------------------------------
      // hri-interaction-design.md: "multiple short fixations beat one long stare", and the pattern
      // it records is attract -> eye contact -> avert -> shift. The engine used to do the first two
      // and NEVER avert: `nextSaccade = now + 600` was rewritten on every frame a confident target
      // was present, so the robot held a person's gaze for as long as they stood there. That is the
      // one failure a person actually notices.
      // LISTENING SUPPRESSES THE GAZE BREAK. The break exists so the robot does not stare at a
      // person indefinitely -- right while idle, wrong mid-sentence: looking away from somebody who
      // is talking to you reads as losing interest, which is the one thing a listening face must
      // not do. It resumes the moment the turn does.
      if (this.convoState(now) === "listen") { this.nextAvert = now + 2600 + rnd(1600); }
      else if (!this.nextAvert) this.nextAvert = now + 2600 + rnd(1600);
      if (now >= this.avertUntil && now >= this.nextAvert) {
        // Away, briefly, and somewhere plausible rather than randomly: down and to one side is what
        // people do, and it reads as thought rather than distraction.
        const side = random() < 0.5 ? -1 : 1;
        this.aim(tx + side * (0.28 + rnd(18) / 100), ty + 0.16 + rnd(12) / 100);
        this.avertUntil = now + 500 + rnd(700);
        this.nextAvert = this.avertUntil + 2200 + rnd(2600);
      } else if (now >= this.avertUntil
                 && Math.hypot(tx - this.gazeX.target, ty - this.gazeY.target) > 0.04) {
        this.aim(tx, ty);                      // back to the person, by a saccade as before
      }
      this.nextSaccade = now + 600;
    } else if (now >= this.nextSaccade) {
      // The idle wander waits out a spoken turn: eyes drifting off while somebody is mid-sentence,
      // or while the robot is answering, reads as not paying attention. With no target to hold,
      // LISTEN simply keeps them still rather than pointing them anywhere it cannot know.
      const c = this.convoState(now);
      if (c === "listen" || c === "speak") this.nextSaccade = now + 400;
      else { this.saccade(now); this.nextAvert = 0; }   // nobody to avert from
    }

    // ---- MICROSACCADES -----------------------------------------------------------------------
    // Between saccades the gaze used to be perfectly still, which is the frozen quality most robot
    // eyes have. A fraction of a degree of jitter, a few times a second, is most of the difference
    // between alive and paused. Added in eyes() rather than to the target, so it cannot accumulate.
    if (now >= this.nextMicro) {
      this.microX = (random() - 0.5) * 0.016;   // ~0.25 deg at 30 deg full deflection
      this.microY = (random() - 0.5) * 0.010;
      this.nextMicro = now + 90 + rnd(220);
    }

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

    this._now = now;
    this.stepMouth(now, dt);
    this.stepMove(now);

    for (const v of [this.bodyW, this.bodyH, this.radius, this.lid, this.lidLower, this.lidCurve,
                     this.browRaise, this.browAngle,
                     this.asym, this.gazeX, this.gazeY, ...this.col]) v.step(dt);
  }

  // What a skin draws, per eye.
  eyes() {
    const m = this.mvOut;
    const cv = this.convoOut(this._now);
    const c = this.col.map((v) => Math.round(Math.max(0, Math.min(255, v.value))));
    const colour = `rgb(${c[0]},${c[1]},${c[2]})`;
    const dim = `rgb(${c.map((v) => Math.round(v * 0.35)).join(",")})`;
    return [0, 1].map((i) => ({
      side: i === 0 ? "left" : "right",
      cx: SLOT[i], cy: EYE_Y + (m.dy + cv.dy) * this.bodyH.value,
      // `scale` widens the eye the emote already shaped, and the corner radius goes with it, or a
      // widened eye reads as a squarer one rather than a bigger one.
      w: this.bodyW.value * (1 + (i === 0 ? -this.asym.value : this.asym.value)) * (1 + m.scale),
      h: this.bodyH.value * (1 + m.scale), radius: this.radius.value * (1 + m.scale),
      // A move adds to the lid rather than replacing it, so a wink during SLEEPY still shuts from
      // an already-low lid instead of snapping the eye open to wink it.
      // The conversation's lid offset and the wince ride on top of the emote's and a MOVE's, so a
      // blink, a SQUINT and a listening face compose instead of replacing one another.
      lid: clamp01(this.lid.value + (i === 0 ? m.lidL : m.lidR) + cv.lid + cv.squint * 0.30),
      lidLower: this.lidLower.value, lidCurve: this.lidCurve.value,
      browRaise: Math.max(-1, Math.min(1, this.browRaise.value + cv.brow - cv.squint * 0.5)),
      browAngle: this.browAngle.value + cv.squint * 6,
      // Likewise the gaze: a move nudges where the eyes already are. Clamped, so a look during a
      // saccade to the same side cannot leave the eye's range.
      // `pull` draws the gaze toward centre before the offset, so `to_camera` lands on the viewer
      // from wherever the eyes were, while every other move still just nudges.
      gazeX: clamp01((this.gazeX.value * (1 - m.pull) + m.gx + this.microX + 1) / 2) * 2 - 1,
      gazeY: clamp01((this.gazeY.value * (1 - m.pull) + m.gy + cv.gy + this.microY + 1) / 2) * 2 - 1,
      sparkle: m.sparkle,       // 0..1, THE ONE FIELD A SKIN MAY IGNORE (face-engine.md A)
      // NULL unless the mouth is on, so a skin's `if (e.mouth)` is false on exactly the faces that
      // have always had no mouth -- "off changes nothing" is then structural rather than a promise.
      // It is face-level, not per-eye: a code skin draws it once, on its left-eye pass.
      mouth: this.mouth ? { level: this.mouthLevel, cx: DESIGN_W / 2 } : null,
      colour, dim, rgb: c,
    }));
  }
  quoteText() { return this.quote >= 0 ? QUOTES[this.quote] : ""; }
  stateName() { return this.state >= 0 ? STATES[this.state][0] : "-"; }
}
