// THE ATTENTION ARBITER, ported from the Tab5's percept/attention.cpp: one target from three cues, a
// stated precedence.
//
//   face (close, confident)  >  person (any facing, several metres)  >  motion (any range)  >  wandering
//
// The Tab5 had face and motion. The tablet adds PERSON (MediaPipe's object detector, "person"): its
// face model only sees faces within about a metre, so a person standing back from the robot was
// invisible. A face still outranks a person because only a face says someone is pointed AT the robot.
// Motion is capped below the others, so it can never claim a detector's confidence.
//
// Confidence is graded here (how far the eyes commit, and how long a target lives): a weak, lone hit
// is a brief glance; a strong hit repeated in one place earns a sustained look. Every number is the
// Tab5's (person's are the face's, one step lower), a reasoned guess to tune here, not a measurement.
// Pure: the caller passes the time.

export const KIND = { NONE: "none", MOTION: "motion", PERSON: "person", FACE: "face" };

const SCORE_FLOOR = 0.30, SCORE_FULL = 0.90, REPS_FULL = 3;
const FACE = { base: 0.15, score: 0.55, reps: 0.30, dwellMin: 500, dwellMax: 2400 };
const PERSON = { base: 0.12, score: 0.50, reps: 0.28, dwellMin: 450, dwellMax: 2000 };
const LINK_MS = 700, LINK_DIST = 0.25;          // two hits are the same person: close in time and space
const MOTION_STR_FLOOR = 0.06, MOTION_STR_FULL = 0.40, MOTION_CONF_CAP = 0.70;
const MOTION_DWELL_MIN = 350, MOTION_DWELL_MAX = 1200;
const RETARGET = 0.10;                          // smaller moves keep the same target (no re-saccade)

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const ramp = (v, lo, hi) => (hi - lo < 1e-6 ? (v >= hi ? 1 : 0) : clamp01((v - lo) / (hi - lo)));

export function detectorConfidence(c, k) {
  return clamp01(k.base + k.score * ramp(c.quality, SCORE_FLOOR, SCORE_FULL) + k.reps * clamp01((c.reps - 1) / (REPS_FULL - 1)));
}
export function motionConfidence(c) { return clamp01(ramp(c.quality, MOTION_STR_FLOOR, MOTION_STR_FULL) * MOTION_CONF_CAP); }

export function dwellFor(kind, conf) {
  if (kind === KIND.FACE) return FACE.dwellMin + (FACE.dwellMax - FACE.dwellMin) * conf;
  if (kind === KIND.PERSON) return PERSON.dwellMin + (PERSON.dwellMax - PERSON.dwellMin) * conf;
  return MOTION_DWELL_MIN + (MOTION_DWELL_MAX - MOTION_DWELL_MIN) * (conf / MOTION_CONF_CAP);
}

const empty = () => ({ live: false, nx: 0, ny: 0, quality: 0, stamp: 0, reps: 0 });

export class Attention {
  constructor() { this.reset(); this.seq = 0; }

  reset() { this.face = empty(); this.person = empty(); this.motion = empty(); this.cur = { kind: KIND.NONE, nx: 0, ny: 0, seq: this.seq || 0 }; }

  noteMotion(now, nx, ny, strength) { Object.assign(this.motion, { live: true, nx, ny, quality: strength, stamp: now, reps: 1 }); }
  noteFace(now, nx, ny, score) { this._linked(this.face, now, nx, ny, score); }
  notePerson(now, nx, ny, score) { this._linked(this.person, now, nx, ny, score); }

  _linked(c, now, nx, ny, score) {
    const linked = c.live && now - c.stamp <= LINK_MS && Math.abs(nx - c.nx) < LINK_DIST && Math.abs(ny - c.ny) < LINK_DIST;
    Object.assign(c, { live: true, nx, ny, quality: score, stamp: now, reps: linked ? c.reps + 1 : 1 });
  }

  /** The target now: {kind, nx, ny, confidence, seq, goodUntil}. */
  tick(now) {
    // Cues age out against their own dwell: a face holds its claim longer by construction.
    if (this.face.live && now - this.face.stamp > dwellFor(KIND.FACE, detectorConfidence(this.face, FACE))) this.face.live = false;
    if (this.person.live && now - this.person.stamp > dwellFor(KIND.PERSON, detectorConfidence(this.person, PERSON))) this.person.live = false;
    if (this.motion.live && now - this.motion.stamp > dwellFor(KIND.MOTION, motionConfidence(this.motion))) this.motion.live = false;
    // Precedence by rule, not by comparing confidences: they measure different things.
    let next;
    if (this.face.live) next = { kind: KIND.FACE, nx: this.face.nx, ny: this.face.ny, confidence: detectorConfidence(this.face, FACE) };
    else if (this.person.live) next = { kind: KIND.PERSON, nx: this.person.nx, ny: this.person.ny, confidence: detectorConfidence(this.person, PERSON) };
    else if (this.motion.live) next = { kind: KIND.MOTION, nx: this.motion.nx, ny: this.motion.ny, confidence: motionConfidence(this.motion) };
    else next = { kind: KIND.NONE, nx: 0, ny: 0, confidence: 0 };
    if (next.kind === KIND.NONE) { next.seq = this.seq; next.goodUntil = now; }
    else {
      const moved = Math.abs(next.nx - this.cur.nx) > RETARGET || Math.abs(next.ny - this.cur.ny) > RETARGET;
      next.seq = next.kind !== this.cur.kind || moved || this.cur.kind === KIND.NONE ? ++this.seq : this.cur.seq;
      next.goodUntil = now + dwellFor(next.kind, next.confidence);
    }
    this.cur = next;
    return next;
  }
}
