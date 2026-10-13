// ARMED: a show is loaded and will start when the operator holds the deadman. Drawn OVER whatever
// the face is doing, including a full-face photo, because the people it is for are standing near a
// robot that is about to move and must not have to guess.
//
// WHY IT IS AN OVERLAY AND NOT A STATE. A reserved robot state (DEGRADED, E_STOP) replaces the skin
// -- main.js forces the default eyes so "something is wrong" looks the same everywhere. ARMED must
// not do that: a show can be mid-skin, and blanking it would both break the show and make ARMED
// look like a fault. So this draws on top of the finished frame and changes nothing underneath.
//
// THE THREE CONSTRAINTS, and what each one ruled out:
//
//   Unmistakable across a room -> a full-width frame plus a word, not a small corner badge. The
//   frame is the part that reads at distance; the word is what makes it unambiguous up close.
//
//   Distinct from E_STOP and DEGRADED -> those are RED (0xff4d4d) and AMBER (0xf3b64a), and they
//   recolour the EYES. This is violet, a hue no robot state uses, and it is a frame around the face
//   rather than a colour of it. Different hue AND different form, so it cannot be half-confused
//   with either at a glance. (The `halloween` theme uses a dark plum for AUTONOMOUS; that is an eye
//   colour, not a frame, and the form keeps them apart.)
//
//   Never reads as the robot already moving -> nothing travels. No sweep around the border, no
//   chase, no spinner: anything that moves ALONG a path reads as the machine running. The frame
//   breathes in place instead, which is the standby idiom, and a countdown counts DOWN to a start
//   that has not happened yet.
//
// Placement is forced by what is already on the face. The top band is not free -- an ALERT brow
// reaches y = -68, off the top of the design area -- so nothing can live up there. What IS free is
// the 163 px channel between the eyes' inner edges (x 558..722) and the strip below the quote line
// (y > 690). The countdown goes in the channel, the word goes in the strip.

const W = 1280, H = 720;
const INSET = 10, THICK = 16;        // the frame, in design units
const VIOLET = "198, 92, 255";       // not a state colour, and far from red and amber
// BOTH THE NUMERAL AND THE WORD LIVE IN THE CHANNEL BETWEEN THE EYES, stacked. The word was at
// y 702 and that was wrong: the frame's bottom stroke spans 694..710, so a 40 px label centred
// there crossed the frame and reached y 722 -- past the canvas's own 720 edge, and visibly clipped
// in a window where the design area fills the height (reported by the Tablet lane from a real page;
// my tests had only checked it cleared the quote and the mouth, never that it was INSIDE the frame).
//
// The gap between an open mouth (659) and the frame's inner edge (694) is 35 px, too tight for a
// label worth reading. The channel between the eyes is at least 173 px wide and the full height,
// free of everything, so both go there and nothing lives near an edge that can be clipped.
const NUM_Y = 312;                   // the countdown, above
const WORD_Y = 438;                  // the word, below it
const NUM_CH = 163;                  // the channel between the eyes; a numeral must fit in it

// `armed` is null (nothing drawn), "wait" (no countdown), or a number of seconds remaining.
// `t` is seconds, for the breathing.
export function drawArmed(ctx, armed, t) {
  if (armed === null || armed === undefined) return;

  // Breathing, not travelling: the whole frame changes together, so nothing moves along it.
  const breath = 0.62 + 0.38 * (0.5 + 0.5 * Math.sin(t * 3.2));

  ctx.save();
  ctx.strokeStyle = `rgba(${VIOLET},${breath.toFixed(3)})`;
  ctx.lineWidth = THICK;
  ctx.strokeRect(INSET + THICK / 2, INSET + THICK / 2, W - 2 * INSET - THICK, H - 2 * INSET - THICK);

  ctx.fillStyle = `rgba(${VIOLET},${Math.min(1, breath + 0.2).toFixed(3)})`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  // The countdown, in the channel between the eyes. Sized to FIT that channel: two digits at this
  // weight are about 134 px, and anything wider would run under the eyes it is sitting between.
  if (typeof armed === "number" && isFinite(armed) && armed > 0) {
    const text = String(Math.ceil(armed));
    let px = 132;
    ctx.font = `bold ${px}px system-ui, sans-serif`;
    const w = ctx.measureText ? (ctx.measureText(text).width || 0) : 0;
    if (w > NUM_CH) { px = Math.max(48, Math.floor(px * (NUM_CH / w))); ctx.font = `bold ${px}px system-ui, sans-serif`; }
    ctx.fillText(text, W / 2, NUM_Y);
  }

  // The word, always: the frame alone says "something", this says which something.
  ctx.font = "bold 40px system-ui, sans-serif";
  ctx.fillText("ARMED", W / 2, WORD_Y);
  ctx.restore();
}
