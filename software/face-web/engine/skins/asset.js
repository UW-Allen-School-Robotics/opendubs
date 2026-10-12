// Skins drawn from a LIBRARY ASSET rather than from code: an image (PNG or SVG) or a looping video
// with transparency (WebM VP9 with alpha), placed where each eye is. How it moves is DECLARED in the
// library entry's `params`, never written in code per asset:
//
//   size         the object's size, as a fraction of the eye's neutral 480 px      (default 0.8)
//   spin_dps     a continuous spin, degrees per second; 0 = none                   (default 0)
//   follow_look  whether it moves toward where the eyes look                       (default true)
//   look         how far, as a fraction of its size                                (default 0.3)
//   blink        "squash" — a blink, and an emote's lid, squash it vertically; or "none"
//   mirror       the right eye is the left mirrored, and spins the other way        (default false)
//   counter      occasionally break a together-spin into opposite directions        (default off)
//                A show can drive the same effect on a beat with the head protocol's SPIN, which
//                suppresses this schedule while it holds it. A SPINNING skin honours SPIN even
//                with no `counter` block, using the default ease.
//                { every_s, hold_s, ease_s } (ASSET_COUNTER_DEFAULTS below holds the defaults,
//                rather than this comment keeping a second copy of them to drift) -- a counter-spin
//                starts at a random interval in every_s, lasts a random time in hold_s, and is
//                EASED over ease_s: BOTH eyes decelerate to a standstill together, half way
//                through, and then spin back up in opposite directions -- never snapping, and
//                reading as one gesture rather than one eye misbehaving. Composes with `mirror`,
//                which flips the base direction.
//
// It takes the same per-eye inputs as every skin (face-engine.md, the skin contract), so a show's
// EMOTE, a blink and a tracked person read on it with no change anywhere else.

const NEUTRAL = 480;

// EVERY PARAM AN ASSET ENTRY MAY SET, and its default. Exported because it is the authoritative
// list: `software/choreo/library.py` refuses an entry with an unknown param key at authoring time,
// since `spinn_dps: 90` would otherwise silently do nothing at all, and a validator keeping its own
// copy of this list would drift from the engine. A test reads these keys and fails CI when the two
// disagree, so ADDING A PARAM HERE IS ENOUGH -- CI then tells library.py about it.
export const ASSET_PARAM_DEFAULTS = Object.freeze({
  size: 0.8, spin_dps: 0, follow_look: true, look: 0.3, blink: "squash", mirror: false,
  counter: null,
});

// THE `counter` BLOCK'S OWN KEYS, and their defaults. Exported for the same reason as the list
// above: library.py checks these keys too, so `counter: { eas_s: 1.2 }` is refused at authoring time
// instead of quietly doing nothing. The arrays are frozen as well -- the config below can fall back
// to them by reference, and one skin must not be able to edit another's defaults.
//
// EASE_MIN is deliberately NOT here. It is a clamp on what ease_s may be, not a key an entry may
// set; putting it in would make the validator start accepting `ease_min` as a param.
export const ASSET_COUNTER_DEFAULTS = Object.freeze({
  every_s: Object.freeze([8, 20]), hold_s: Object.freeze([2, 4]), ease_s: 0.9,
});

export function makeAssetSkin(entry, baseUrl, onError) {
  const p = { ...ASSET_PARAM_DEFAULTS, ...(entry.params || {}) };

  // ---- the counter-spin ------------------------------------------------------------------
  //
  // WHY AN ACCUMULATED ANGLE. The spin below is `rate * t`: an angle computed from absolute time,
  // exact and drift-free, but it can only express a CONSTANT rate. Flipping its sign teleports the
  // object to a mirrored orientation, which is the snap this feature exists to avoid. A rate that
  // changes has to be integrated. The integrator runs ONLY when a counter-spin is configured or a
  // show is holding one; otherwise the original expression is used untouched, so an ordinary entry
  // is bit-for-bit what it was.
  //
  // SYMMETRIC, at the Choreography lane's request, and it is not a detail. A single split factor
  // s runs 0 (together) to 1 (counter), and the two eyes take
  //
  //     left = omega * |1 - 2s|        right = omega * (1 - 2s)
  //
  //     s = 0      left +w   right +w     together
  //     s = 0.5    left  0   right  0     BOTH STOPPED  <- this is what lands on the beat
  //     s = 1      left +w   right -w     counter-rotating
  //
  // So both eyes decelerate to a standstill together and then split. The earlier version reversed
  // only the right eye, which read as one eye misbehaving rather than as one gesture -- and this
  // version also makes the SHOW TIMING exact: both eyes cross zero at the SAME instant, so "the
  // cue's time is the zero crossing" is true of the pair and not just of one of them.
  //
  // s travels at 1/ease_s per second, so a full change takes ease_s and THE ZERO CROSSING IS AT
  // ease_s/2 AFTER RECEIPT. head-protocol.md records that number; the runner sends the line early
  // by it. Do not change the traversal rate without telling the Tablet and Choreography lanes.
  const EASE_DEFAULT = ASSET_COUNTER_DEFAULTS.ease_s;
  // THE SHORTEST EASE THE SKIN WILL DO, and a number two other lanes now mirror.
  // `library.py` accepts `ease_s: 0` (it refuses only negatives and non-numbers), so an entry can
  // legitimately ask for an instant reversal -- which v3 says the wire may not have, and which
  // would be the snap this whole mechanism exists to avoid. It is clamped instead, so an ease_s
  // below this still crosses zero EASE_MIN/2 after receipt, not immediately.
  // The show side computes its lead as max(ease_s, EASE_MIN) / 2 and prints which it used, so
  // CHANGING THIS NUMBER SILENTLY MAKES EVERY CUE SHEET WRONG. Tell Choreography first.
  const EASE_MIN = 0.05;
  const cfg = p.counter && {
    every: p.counter.every_s || ASSET_COUNTER_DEFAULTS.every_s,
    hold: p.counter.hold_s || ASSET_COUNTER_DEFAULTS.hold_s,
    ease: p.counter.ease_s ?? EASE_DEFAULT,
  };
  // A SHOW MAY DRIVE A SPINNING SKIN THAT HAS NO `counter` BLOCK. Agreed with Choreography: a cue
  // must not silently depend on whether an operator configured the autonomous feature, because
  // they are different features that merely share a mechanism.
  const easeS = cfg ? cfg.ease : EASE_DEFAULT;
  const pick = ([lo, hi]) => lo + Math.random() * Math.max(0, hi - lo);

  // One schedule for BOTH eyes: they must break apart together, or it reads as two objects rather
  // than one face. Hence per-skin state advanced once a frame, not per-eye state advanced twice.
  const sp = {
    t: null, angL: 0, angR: 0,
    s: 0, sTarget: 0,          // 0 together .. 1 counter
    nextAt: null, untilAt: 0,  // the autonomous schedule
    held: false, holdUntil: null,
    pending: null,             // a SPIN line, applied on the next frame (see spin())
  };

  // The head protocol's SPIN, called by protocol.js. mode is "counter" | "together" | "auto";
  // holdS is a number or null. Recorded rather than applied, because applying it needs the frame's
  // clock and this is called from the command path -- so it takes effect on the next frame, within
  // one frame of receipt and far inside the ease.
  const MODES = ["counter", "together", "auto"];
  function spin(mode, holdS) {
    if (!p.spin_dps) return;                 // a non-spinning skin: the protocol says OK, do nothing
    // NORMALISE, THEN REFUSE WHAT IS LEFT. A mode this does not recognise used to fall through to
    // the `together` branch, so a caller that passed "COUNTER" got a spin that drew TOGETHER --
    // silently the wrong gesture, on the beat, with nothing anywhere reporting a problem. Doing
    // nothing is the better failure: the spin simply carries on as it was.
    const m = String(mode).toLowerCase();
    if (!MODES.includes(m)) return;
    sp.pending = { mode: m, holdS: typeof holdS === "number" && isFinite(holdS) ? holdS : null };
  }

  function stepSpin(t) {
    if (sp.t === null) {
      sp.t = t;
      sp.nextAt = cfg ? t + pick(cfg.every) : null;
      // CONTINUITY: start the accumulator where the free-running formula had got to, or the object
      // teleports on the frame a show first touches it. The mirror factor is applied identically in
      // both paths at draw time, so one unsigned seed serves both eyes.
      sp.angL = sp.angR = (p.spin_dps * Math.PI / 180) * t;
    }
    const dt = Math.max(0, Math.min(0.1, t - sp.t));   // a hidden tab hands back a huge dt; clamp
    sp.t = t;

    if (sp.pending) {
      const { mode, holdS } = sp.pending;
      sp.pending = null;
      if (mode === "auto") {
        // Hand the schedule back. Ease to together first; the timer picks up from there.
        sp.held = false; sp.holdUntil = null; sp.sTarget = 0;
        if (cfg) sp.nextAt = t + pick(cfg.every);
      } else {
        sp.held = true;
        sp.sTarget = mode === "counter" ? 1 : 0;
        sp.holdUntil = mode === "counter" && holdS ? t + holdS : null;
      }
    }

    if (sp.held) {
      // A hold that expires returns to together but STAYS held: the show still owns the spin
      // until it sends AUTO, so the random schedule cannot fire mid-show.
      if (sp.holdUntil !== null && t >= sp.holdUntil) { sp.sTarget = 0; sp.holdUntil = null; }
    } else if (cfg) {
      if (t >= sp.nextAt && sp.sTarget === 0) { sp.sTarget = 1; sp.untilAt = t + pick(cfg.hold); }
      else if (sp.sTarget === 1 && t >= sp.untilAt) { sp.sTarget = 0; sp.nextAt = t + pick(cfg.every); }
    }

    const rate = 1 / Math.max(EASE_MIN, easeS);       // s travels 0..1 in ease_s
    const d = sp.sTarget - sp.s;
    sp.s += Math.sign(d) * Math.min(Math.abs(d), rate * dt);

    const w = (p.spin_dps * Math.PI / 180) * dt;
    sp.angL += w * Math.abs(1 - 2 * sp.s);
    sp.angR += w * (1 - 2 * sp.s);
  }

  // ONCE INTEGRATING, ALWAYS INTEGRATING -- a LATCH, not a live test, and the difference matters.
  //
  // The two paths produce completely different angles for the same moment: `rate * t` is an
  // absolute position, while the accumulator is wherever the object has got to. Switching back
  // after a show finished would therefore TELEPORT the object, which is precisely the snap this
  // whole feature exists to avoid -- and it would happen on SPIN AUTO, at the end of every show.
  //
  // The cost of latching is accumulation drift for the rest of the session on a skin a show has
  // touched. For a spin that is invisible: the angle is used modulo a full turn. A visible jump is
  // not invisible. So the latch stays set.
  //
  // To hand the angle back cleanly one day you would have to solve rate * t == angL for t, which
  // means lying about the clock; not worth it.
  let latched = Boolean(cfg);
  const integrating = () => { if (!latched && (sp.held || sp.pending)) latched = true; return latched; };

  const url = new URL(entry.file, baseUrl).href;
  let source, ready = false, failed = null;

  if (entry.type === "image") {
    source = new Image();
    source.onload = () => { ready = true; };
    source.onerror = () => { failed = `cannot load ${entry.file}`; onError(entry.id, failed); };
    source.src = url;
  } else if (entry.type === "video") {
    source = document.createElement("video");
    Object.assign(source, { muted: true, loop: true, playsInline: true, autoplay: true, preload: "auto" });
    source.oncanplay = () => { ready = true; source.play().catch(() => {}); };
    source.onerror = () => { failed = `cannot decode ${entry.file}`; onError(entry.id, failed); };
    source.src = url;
  } else {
    throw new Error(`skin type ${entry.type} is not built yet`);
  }

  return {
    name: entry.id,
    type: entry.type,
    spin,                               // head protocol SPIN; see spin() above
    get failed() { return failed; },
    source,
    stop() { if (entry.type === "video") { source.pause(); source.removeAttribute("src"); source.load(); } },
    draw(ctx, e, t) {
      if (!ready) return false;                         // the caller draws the default instead
      const s = NEUTRAL * p.size;
      const sq = p.blink === "squash" ? Math.max(0.03, 1 - e.lid) : 1;
      const dx = p.follow_look ? e.gazeX * s * p.look : 0;
      const dy = p.follow_look ? e.gazeY * s * p.look : 0;
      const right = e.side === "right";
      const flip = p.mirror && right ? -1 : 1;
      let rot;
      if (!integrating()) {
        rot = (p.spin_dps * Math.PI / 180) * t * (p.mirror && right ? -1 : 1);
      } else {
        if (t !== sp.t) stepSpin(t);                             // first eye of this frame
        rot = (right ? sp.angR : sp.angL) * (p.mirror && right ? -1 : 1);
      }
      ctx.save();
      ctx.translate(e.cx + dx, e.cy + dy);
      ctx.scale(flip, sq);
      ctx.rotate(rot);
      ctx.drawImage(source, -s / 2, -s / 2, s, s);
      ctx.restore();
      return true;
    },
  };
}
