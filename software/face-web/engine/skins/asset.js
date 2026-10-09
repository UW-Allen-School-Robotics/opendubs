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
//                { every_s: [8, 20], hold_s: [2, 4], ease_s: 0.9 } -- a counter-spin starts at a
//                random interval in every_s, lasts a random time in hold_s, and the reversal is
//                EASED over ease_s: the right eye decelerates through zero and spins back up the
//                other way, never snapping. Composes with `mirror`, which flips the base direction.
//
// It takes the same per-eye inputs as every skin (face-engine.md, the skin contract), so a show's
// EMOTE, a blink and a tracked person read on it with no change anywhere else.

const NEUTRAL = 480;

export function makeAssetSkin(entry, baseUrl, onError) {
  const p = { size: 0.8, spin_dps: 0, follow_look: true, look: 0.3, blink: "squash", mirror: false,
              counter: null, ...(entry.params || {}) };

  // ---- the counter-spin's state -------------------------------------------------------------
  //
  // WHY AN ACCUMULATED ANGLE, AND ONLY WHEN `counter` IS SET. The spin below is `rate * t`: an
  // angle computed from absolute time, which is exact and drift-free but can only express a
  // CONSTANT rate. Flipping its sign would teleport the object to a mirrored orientation, which is
  // the snap this feature exists to avoid. A rate that changes has to be integrated instead.
  //
  // So the integrator is used only when a counter-spin is configured. Without it the original
  // expression runs untouched, so every existing library entry is bit-for-bit what it was and
  // cannot pick up accumulation drift over a long run.
  const cfg = p.counter && {
    every: p.counter.every_s || [8, 20],
    hold: p.counter.hold_s || [2, 4],
    ease: p.counter.ease_s ?? 0.9,
  };
  const pick = ([lo, hi]) => lo + Math.random() * Math.max(0, hi - lo);
  // One schedule for BOTH eyes: they must break apart together, or it reads as two objects rather
  // than one face. Hence per-skin state, advanced once a frame, not per-eye state advanced twice.
  const spinState = { t: null, angL: 0, angR: 0, cf: 1, cfTarget: 1, nextAt: null, untilAt: 0 };

  // Advance the shared schedule. Called once per frame, from whichever eye is drawn first.
  function stepSpin(t) {
    if (spinState.t === null) { spinState.t = t; spinState.nextAt = t + pick(cfg.every); return; }
    const dt = Math.max(0, Math.min(0.1, t - spinState.t));      // a tab that was hidden can hand
    spinState.t = t;                                             // back a huge dt; clamp it
    if (t >= spinState.nextAt && spinState.cfTarget === 1) {
      spinState.cfTarget = -1;
      spinState.untilAt = t + pick(cfg.hold);
    } else if (spinState.cfTarget === -1 && t >= spinState.untilAt) {
      spinState.cfTarget = 1;
      spinState.nextAt = t + pick(cfg.every);
    }
    // EASE THROUGH ZERO at a bounded rate: the full swing is 2 units (+1 to -1), so covering it in
    // ease_s seconds is 2/ease_s per second. The object therefore slows, stops and spins back up,
    // which is the whole point -- a linear ramp through zero looks like a real reversal, while a
    // step looks like a dropped frame.
    const rate = 2 / Math.max(0.05, cfg.ease);
    const d = spinState.cfTarget - spinState.cf;
    spinState.cf += Math.sign(d) * Math.min(Math.abs(d), rate * dt);
    const w = (p.spin_dps * Math.PI / 180) * dt;
    spinState.angL += w;
    spinState.angR += w * spinState.cf;
  }
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
      let spin;
      if (!cfg) {
        spin = (p.spin_dps * Math.PI / 180) * t * (p.mirror && right ? -1 : 1);
      } else {
        if (t !== spinState.t) stepSpin(t);                      // first eye of this frame
        spin = (right ? spinState.angR : spinState.angL) * (p.mirror && right ? -1 : 1);
      }
      ctx.save();
      ctx.translate(e.cx + dx, e.cy + dy);
      ctx.scale(flip, sq);
      ctx.rotate(spin);
      ctx.drawImage(source, -s / 2, -s / 2, s, s);
      ctx.restore();
      return true;
    },
  };
}
