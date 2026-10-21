// THE CAPTION: what the robot is doing, in words, for somebody who does not know the colour code.
//
// "Ready", "Manual control", "Running a show", "Charging 95%", "Problem: lift not responding",
// "Emergency stop". The text comes from the dashboard (head protocol `CAPTION`), because only the
// dashboard knows a fault's real reason or the pack's percentage. This file owns how it LOOKS.
//
// IT IS ALWAYS WHITE, NEVER THE STATE'S COLOUR, and that is the single most important decision
// here. The caption exists FOR THE PERSON WHO CANNOT READ THE COLOUR -- that is its whole purpose.
// Drawing "Problem: lift not responding" in DEGRADED's amber would make the words fail for exactly
// the person they were added for: if you cannot tell amber from red, amber words do not help you.
// The colour is one channel and the words are the other, and two channels that fail together are
// one channel. So the words never carry state, in any state.
//
// ON A DARK PANEL, for the same reason the camera sign has one: the caption has to be legible over
// whatever the face is doing -- amber eyes, a purple charging face, a full-face photo a show put
// up. Contrast you can reason about beats contrast you hope for. The panel is sized to the text
// rather than spanning the face, so it occludes only what it must.
//
// WHERE IT SITS, and what had to give. The bottom band of the design area is crowded:
//
//     y 600..619   the eyes' lowest reach (ALERT is the tallest emote, bodyH 518)
//     y 613..659   an open mouth
//     y 654..677   the idle quote, the song title, the charge line -- all one 24 px slot
//     y 694..710   the ARMED frame's bottom stroke
//     y 712..720   the charge gauge's band
//
// The free gap between an open mouth and the ARMED frame is THIRTY-THREE PIXELS, which at this
// panel's scale is about a 26 px font -- roughly a metre of reading distance. The brief asks for
// two to three metres, so THE GAP CANNOT HOLD WHAT WAS ASKED FOR and something had to yield.
//
// WHAT YIELDS: the quote, the song and the charge line (the caption replaces them -- it says more
// than any of them, and "Charging 95%" IS the charge line), and the MOUTH, which the panel draws
// over when both are up. What does NOT yield: the eyes (the panel starts below their lowest
// reach), the ARMED frame (it ends above it), and the charge gauge.
//
// Yielding the mouth is a judgement and worth stating plainly: the mouth is animation, and the
// caption is the robot telling a stranger what it is doing. If the maintainers would rather keep
// the mouth, CAPTION_BAND below is the one thing to change, and the caption shrinks into whatever
// is left -- it will simply be readable from closer.

const W = 1280;

// The band the caption may use: below the eyes' lowest reach, above the ARMED frame.
export const CAPTION_BAND = Object.freeze({ top: 622, bottom: 692 });

const PAD_X = 28, PAD_Y = 10;        // inside the panel
const MARGIN = 56;                   // clear of the design edges, and of the charge gauge's band
const PANEL = "rgba(8, 10, 12, 0.88)";
const RIM = "rgba(255, 255, 255, 0.14)";
const INK = "rgba(255, 255, 255, 0.96)";
const INK_FAULT = "#ffffff";

// Two tiers, by what the words are for. A fault is the one caption somebody may be reading while
// deciding whether to approach a machine, so it gets the room.
const SIZE = { normal: 38, fault: 46 };
const SIZE_FLOOR = 26;               // below this it is not worth drawing large; it wraps nothing

function font(px, fault) { return `${fault ? 650 : 500} ${px}px system-ui, -apple-system, sans-serif`; }

// MEASURED, NOT ESTIMATED, where the canvas will tell us. A caption's length is not ours to choose
// -- the protocol allows 60 characters and the dashboard writes the words -- so the width has to
// be asked for rather than assumed from an average character. The estimate is only the fallback
// for a context that cannot measure (a headless test), and it is deliberately WIDE so the fallback
// errs toward shrinking rather than toward overflowing.
function widthOf(ctx, text, px, fault) {
  ctx.font = font(px, fault);
  if (typeof ctx.measureText === "function") {
    const m = ctx.measureText(text);
    if (m && typeof m.width === "number" && m.width > 0) return m.width;
  }
  return text.length * px * 0.58;
}

// The size that fits, stepping down from the tier's own. Returns the floor if even that overflows:
// a caption slightly too wide is still readable, where one shrunk to nothing is not.
function fit(ctx, text, fault) {
  const room = W - 2 * MARGIN - 2 * PAD_X;
  for (let px = SIZE[fault ? "fault" : "normal"]; px > SIZE_FLOOR; px -= 2) {
    if (widthOf(ctx, text, px, fault) <= room) return px;
  }
  return SIZE_FLOOR;
}

/**
 * Draw the caption, in design space (1280x720), over the face.
 *
 * `text` is the line to show, or null/empty to draw nothing. `fault` picks the louder tier, for
 * DEGRADED and E_STOP. Nothing here decides WHEN to show it or what it says: the page owns the
 * setting and the protocol owns the words.
 */
export function drawCaption(ctx, text, { fault = false } = {}) {
  if (typeof text !== "string") return;
  const line = text.trim();
  if (!line) return;

  const px = fit(ctx, line, fault);
  const w = Math.min(W - 2 * MARGIN, widthOf(ctx, line, px, fault) + 2 * PAD_X);
  const h = Math.min(CAPTION_BAND.bottom - CAPTION_BAND.top, px + 2 * PAD_Y);
  // Sat on the BOTTOM of the band rather than centred in it: the caption should hug the ARMED
  // frame and leave the gap above, nearest the eyes, so a smaller caption does not drift upward
  // into the face.
  const y = CAPTION_BAND.bottom - h;
  const x = (W - w) / 2;

  ctx.save();
  ctx.fillStyle = PANEL;
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, Math.min(16, h / 2)) : ctx.rect(x, y, w, h);
  ctx.fill();
  ctx.strokeStyle = RIM; ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(x, y, w, h, Math.min(16, h / 2)) : ctx.rect(x, y, w, h);
  ctx.stroke();

  ctx.fillStyle = fault ? INK_FAULT : INK;
  ctx.font = font(px, fault);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(line, W / 2, y + h / 2 + 1);
  ctx.restore();
}
