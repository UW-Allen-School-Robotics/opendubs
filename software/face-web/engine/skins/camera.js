// A CAMERA IS ON: the sign that says so, drawn over everything the face is doing.
//
// It exists because the maintainers made it the CONDITION of letting the tablet's cameras be robot
// sensors: a visible indicator on the face whenever any camera is open, tracking or streaming. So
// this is a consent signal, not decoration, and it is held to the standard that implies -- the one
// failure that matters is a person not noticing it, and every choice below is made against that.
//
// IT IS AN OVERLAY, like armed.js and for the same reason: a reserved state (DEGRADED, E_STOP)
// replaces the skin, and a show can be mid-skin or showing a full-face photo. None of that may be
// allowed to hide the fact that a camera is open, and none of it should be broken by the sign
// either. The caller draws it after the ARMED sign and before the BRIGHT dimming, so it sits on top
// of the finished frame and changes nothing underneath. (It dims with the face on purpose: an
// indicator that stayed bright while the screen went dark would be the only lit thing in the room.)
//
// GREEN, THOUGH NO OTHER SIGN HERE REUSES A HUE. armed.js picked violet precisely because no robot
// state uses it, and SAFE_IDLE's eyes are already green (0x2ecc58). The convention wins anyway:
// every phone and laptop in the room marks a live camera with a green dot, and a consent signal has
// to be understood by someone who has never seen this robot, with nothing explained to them. So the
// separation is carried by FORM instead, which armed.js's own rule allows -- a small hard dot and
// white capitals on a near-opaque dark pill, at the top centre, against SAFE_IDLE's large soft
// green slabs where the eyes are. They cannot be mistaken for each other at any distance.
// If the maintainers would rather it were a hue nothing else uses, DOT and PILL below are the only
// two lines to change.
//
// WHERE IT GOES, and why not the top-right corner. The brows own the top of the face: an ALERT brow
// reaches y = -68, off the top of the design area (armed.js records this), and the right brow spans
// x 743..1180. A badge at the top right either sits under a brow or is squeezed into the ~100 px
// outside it, too small for a word. What IS free at every height is the CORRIDOR BETWEEN THE BROWS,
// x 537..743 -- the left brow ends at 537, the right starts at 743 -- and above the eye bodies,
// which start at y 101 even at the largest emote. The sign lives there: brow-free, eye-free, clear
// of the ARMED frame's top stroke (y 10..26) and nowhere near an edge it could be clipped against.
//
// A PULSE THAT NEVER GOES OUT. The dot breathes and a halo expands out of it, which draws the eye
// and proves the sign is live rather than painted on. Its alpha never falls below DOT_FLOOR: anyone
// glancing at the trough of a pulse that faded to nothing would see no indicator at all, which is
// exactly the failure this is here to prevent. A test samples the whole cycle for it.
//
// A PURE FUNCTION OF `cams` AND `t`, as fireworks.js is: nothing accumulates between frames, so a
// skipped frame or a hidden tab shows what a frame that ran would have shown.

const W = 1280;

// The corridor between the brows (x 537..743), above the eye bodies (y >= 101 at the largest
// emote). The pill is inset inside it, so nothing is near an edge of anything.
const CX = W / 2;
const PILL_W = 196, PILL_H = 68, PILL_Y = 26, PILL_R = 34;

const DOT = "34, 226, 102";          // the live-camera green; see above before changing it
const PILL = "rgba(10, 12, 14, 0.90)";   // near-opaque: the sign must read over a white photo
const RING = "rgba(255, 255, 255, 0.22)";

const DOT_R = 9, DOT_FLOOR = 0.72;   // the pulse never dims past this; a test holds it to that
const PULSE_HZ = 1.1;                // about one breath a second, the rate every device uses

const TITLE = "CAMERA ON";
const TITLE_PX = 24, SIDE_PX = 15;
// The text is laid out from a FIXED estimate rather than measureText, because the layout has to be
// the same on the tablet, in a laptop twin and in a headless test, and because a sign whose width
// depends on the font the device happened to load is a sign that can overflow its pill.
const TITLE_W = TITLE.length * TITLE_PX * 0.62;
const GAP = 12;
const GROUP_W = DOT_R * 2 + GAP + TITLE_W;

/** Which cameras, in the words a person would use. `screen` faces the way the robot does. */
function sideLabel(cams) {
  if (cams.screen && cams.back) return "front + rear";
  return cams.screen ? "front" : "rear";
}

/**
 * Draw the sign, in design space (1280x720), over the finished frame.
 *
 * `cams` is { screen, back } -- the tablet's screen-side camera (which faces the way the robot
 * faces, so "front") and its back-side one ("rear"). `t` is seconds, for the pulse.
 *
 * NOTHING IS DRAWN WHEN NO CAMERA IS OPEN, and that is checked here rather than trusted to the
 * caller. Both directions are failures with real consequences: a sign that is missing while a
 * camera records is the one this feature exists to prevent, and a sign left up while every camera
 * is shut teaches people to ignore it, which costs the same thing a week later.
 */
export function drawCameraOn(ctx, cams, t = 0) {
  if (!cams || (!cams.screen && !cams.back)) return;

  const pillX = CX - PILL_W / 2;
  ctx.save();

  // The pill: near-opaque, so a full-face photo underneath cannot swallow the sign.
  ctx.fillStyle = PILL;
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(pillX, PILL_Y, PILL_W, PILL_H, PILL_R)
                : ctx.rect(pillX, PILL_Y, PILL_W, PILL_H);
  ctx.fill();
  ctx.strokeStyle = RING; ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.roundRect ? ctx.roundRect(pillX, PILL_Y, PILL_W, PILL_H, PILL_R)
                : ctx.rect(pillX, PILL_Y, PILL_W, PILL_H);
  ctx.stroke();

  // The dot, and the halo breathing out of it. `phase` runs 0..1 and nothing carries over.
  const phase = (t * PULSE_HZ) % 1;
  const dotX = CX - GROUP_W / 2 + DOT_R;
  const dotY = PILL_Y + 26;

  const halo = DOT_R + 11 * phase;
  ctx.strokeStyle = `rgba(${DOT},${(0.38 * (1 - phase)).toFixed(3)})`;
  ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.arc(dotX, dotY, halo, 0, Math.PI * 2); ctx.stroke();

  const lit = DOT_FLOOR + (1 - DOT_FLOOR) * (0.5 + 0.5 * Math.sin(t * PULSE_HZ * Math.PI * 2));
  ctx.fillStyle = `rgba(${DOT},${lit.toFixed(3)})`;
  ctx.beginPath(); ctx.arc(dotX, dotY, DOT_R, 0, Math.PI * 2); ctx.fill();

  // The words. White, because white on near-black is what carries across a room; the green is the
  // convention and does the work up close, so it does not also have to be the thing you read.
  ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "rgba(255,255,255,0.96)";
  ctx.font = `600 ${TITLE_PX}px system-ui, sans-serif`;
  ctx.fillText(TITLE, dotX + DOT_R + GAP, dotY + 8);

  // Which camera, under it: the operator's question, where it cannot crowd the one everybody else
  // is asking.
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(255,255,255,0.72)";
  ctx.font = `${SIDE_PX}px system-ui, sans-serif`;
  ctx.fillText(sideLabel(cams), CX, PILL_Y + PILL_H - 14);

  ctx.restore();
}

// For the tests and for anyone laying something out near it: what this occupies, in design space.
export const CAMERA_SIGN_BOX = Object.freeze({
  x: CX - PILL_W / 2, y: PILL_Y, w: PILL_W, h: PILL_H,
});
export const CAMERA_DOT_FLOOR = DOT_FLOOR;
