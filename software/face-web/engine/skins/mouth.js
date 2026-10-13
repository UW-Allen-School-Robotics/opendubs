// An OPTIONAL mouth, under the eyes. Off unless the face is told otherwise, so the default face is
// exactly the face it has always been.
//
// WHY IT IS OPTIONAL, AND OFF. notes/design/face-design.md recommends NO mouth, from two findings:
// eyes on a large display with the mouth omitted tested best for conveying emotion, and the one
// study of a robot head found visual speech cues did not improve intelligibility against noisy
// audio. Brow-raises and nods delivered a measured gain for far less work. The maintainers have
// asked for a mouth anyway, which is theirs to decide -- and keeping it off by default is what lets
// both be true: the default face stays the one the research supports, and the mouth is there for
// whoever wants it. The note records this rather than contradicting what ships.
//
// SIMPLE AND GRAPHIC, deliberately. The brief was explicit about the uncanny valley, and the
// maintainers had already rejected per-emote pupil sizes for the same reason. So this is not a
// lip-sync mouth: no visemes, no phonemes, no attempt at a talker a viewer could lip-read. It is a
// rounded bar that opens, in the same rounded-rectangle language as the eyes, carrying the state
// colour like everything else. The face-design note is clear that an INACCURATE lip-sync buys
// nothing, so this does not pretend to accuracy it cannot have.
//
// It is drawn ONCE per frame, by the code skin's left-eye pass -- the skins are called per eye, and
// a mouth is not a per-eye thing. An asset skin draws no mouth at all, which is how a full-face
// still hides it with no extra mechanism.

// The band between the bottom of a neutral eye (600) and the quote line (672), in design units.
const MOUTH_Y = 636;
const WIDTH = 190;              // narrower than the gap between the eyes, so it reads as centred
const CLOSED_H = 9;             // a closed mouth is a line, not a gap
const OPEN_H = 46;              // and it never opens past the band it lives in

// `level` is 0..1, an envelope. The shape is the only thing it drives: this is one number, not a
// rig, and it stays that way.
export function drawMouth(ctx, cx, level, colour, dim) {
  const open = Math.max(0, Math.min(1, level));
  const h = CLOSED_H + (OPEN_H - CLOSED_H) * open;
  // The mouth narrows a little as it opens, which is what a real one does and what keeps a wide
  // open mouth from reading as a letterbox.
  const w = WIDTH * (1 - 0.12 * open);
  const x = cx - w / 2, y = MOUTH_Y - h / 2;
  const r = Math.min(h / 2, w / 2);

  ctx.save();
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, r) : ctx.rect(x, y, w, h);
  // Darker at the bottom, as the eyes are, so it belongs to the same face rather than sitting on it.
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, colour);
  g.addColorStop(1, dim);
  ctx.fillStyle = g;
  ctx.fill();
  ctx.restore();
}
