// THE SKIN CONTRACT — the one module a skin outside this repository may import.
//
// A skin in the private content repository is served by the dashboard and imported by the face at
// run time. It needs a helper or two from the engine, and this is the ONLY place it may take them
// from. Importing an engine file by path instead (`/face/engine/skins/mouth.js`) would make this
// repository's internal layout a public API for a repository whose CI never builds here — so a
// tidy-up in engine/skins/ would silently break content nobody can test. One re-export costs
// nothing and keeps that freedom.
//
//     import { drawMouth, DESIGN_W, DESIGN_H } from "/face/engine/skin-api.js";
//
// ADDING to this file is cheap and safe. REMOVING or changing the meaning of anything here breaks
// content that is not in this repository and is not built by this CI, so it is a coordinated
// change with whoever owns the content — see SKIN_API_VERSION below.

export { drawMouth } from "./skins/mouth.js";
export { DESIGN_W, DESIGN_H } from "./face.js";

// Bumped when something here is REMOVED or changes meaning; adding an export does not bump it. A
// content skin can read it to refuse politely rather than throw on an engine it does not know.
export const SKIN_API_VERSION = 1;

// ---- what a skin is ----------------------------------------------------------------------------
//
// A module with:
//
//     export const name = "my_skin";            // optional; the index entry's id wins either way
//     export function draw(ctx, e, t) { … }     // required
//     export function stop() { … }              // optional; called when another skin is chosen
//
// `draw` is called ONCE PER EYE PER FRAME, with the canvas already transformed into design space
// (1280 x 720, the panel's geometry). Return `false` to say "I cannot draw yet" and the default
// eyes are drawn in your place for that frame — never a blank face.
//
// `e` is that eye's inputs:
//
//     side        "left" | "right"
//     cx, cy      its centre, in design space
//     w, h        its size, already shaped by the emote and any MOVE
//     radius      the corner radius, likewise
//     lid         the UPPER lid, 0 open .. 1 shut (a blink and the emote, combined)
//     lidLower    the lower lid, the same
//     lidCurve    how the lids bow: POSITIVE droops the upper and bulges the lower (the smile)
//     browRaise   -1 .. +1          browAngle   degrees, + = inner end down
//     gazeX, gazeY  -1 .. +1, where the eyes are looking
//     sparkle     0 .. 1 — THE ONE FIELD A SKIN MAY IGNORE
//     mouth       null unless the optional mouth is on; {level, cx} when it is
//     colour, dim, rgb   the robot's STATE colour, its 35% version, and the raw triple
//
// `t` is seconds, and should be the ONLY source of time: read a clock and the same frame drawn
// twice looks different, which breaks the offline render. Use `t` and nothing else, and prefer a
// pure function of it to anything that accumulates between frames — a hidden tab hands back a huge
// gap, and a skin that integrates will drift or catch up in a rush.
//
// THREE RULES THAT ARE NOT OBVIOUS, each of which cost something to learn:
//
//   1. DRAW THE MOUTH FIRST, on the left eye's pass, before anything that might return early:
//          if (e.side === "left" && e.mouth) drawMouth(ctx, e.mouth.cx, e.mouth.level, e.colour, e.dim);
//      A skin that returns early when the lid is shut would otherwise drop the mouth on every blink.
//
//   2. CLIP TO THE EYE BODY before drawing inside it, and restore afterwards. A missing restore
//      leaks the clip onto the other eye and the brow, and half the face vanishes.
//
//   3. THE STATE COLOUR IS NOT YOURS TO IGNORE LIGHTLY. A skin may use its own palette — the
//      fireworks and the monsters do — but only because a reserved state (DEGRADED, E_STOP) draws
//      the default eyes whatever is chosen, so a skin can never be what hides a fault. Within that,
//      a skin that shows `colour` somewhere keeps the robot's state readable.
