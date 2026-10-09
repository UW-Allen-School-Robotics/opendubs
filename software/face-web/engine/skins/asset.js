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
//
// It takes the same per-eye inputs as every skin (face-engine.md, the skin contract), so a show's
// EMOTE, a blink and a tracked person read on it with no change anywhere else.

const NEUTRAL = 480;

export function makeAssetSkin(entry, baseUrl, onError) {
  const p = { size: 0.8, spin_dps: 0, follow_look: true, look: 0.3, blink: "squash", mirror: false, ...(entry.params || {}) };
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
      const spin = (p.spin_dps * Math.PI / 180) * t * (p.mirror && right ? -1 : 1);
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
