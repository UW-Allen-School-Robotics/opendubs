// THE CHARGE GAUGE: the edge of the screen filling to the pack's state of charge while the robot
// is on the charger.
//
// Before this, the robot never showed charging at all -- the dashboard sent SAFE_IDLE while it
// charged, and CHARGING's colour was a teal so close to SAFE_IDLE's green that it would not have
// registered if it had been sent. So this is a condition the face simply did not report, and the
// colour alone was never going to fix it.
//
// IT MUST READ WITHOUT COLOUR, which is the requirement that shapes everything here. A purple face
// says "charging" only to someone who has been told what purple means; a bar that is 97% of the way
// round the screen says it to anyone, including somebody who cannot tell purple from teal. So the
// INFORMATION IS THE LENGTH, the colour is only the accent, and the number is written out in words
// as well. Any one of the three alone is enough to read it.
//
// THE PERIMETER, not an arc or a bar, because it is the Tab5's idiom for the same thing (the band's
// GAUGE activity, app_face.cpp) and the two heads should not invent separate visual languages for
// one condition. It starts at the top-left and runs clockwise.
//
// IT NEVER INVENTS A LEVEL. The Tab5's gauge shipped with `g_soc = 0.62f` and nothing ever fed it,
// so the band drew a confident 62% that meant nothing at all -- which is worse than drawing no
// number, because no number is visibly no number. With an unknown or stale SOC this draws the
// outline and the bare word, and no fill: "charging, level unknown" is a thing the face can say.
//
// WHERE IT SITS. The outermost 8 px, so it clears the ARMED frame (which occupies 10..26 from each
// edge, armed.js) and the two can be on screen together without touching. The line of text is NOT
// drawn here: it goes where the idle quote goes, through the page's existing precedence, because
// the bottom of the face is already spoken for and a second line there would collide with the
// quote, a song title, or the ARMED word. `Face.chargeText()` produces it.
//
// A PURE FUNCTION of its inputs, as the other overlays are: nothing accumulates between frames.

const W = 1280, H = 720;
const THICK = 8;                  // the outermost band; the ARMED frame starts at 10
const PURPLE = "109, 48, 224";    // CHARGING, as vocab.js and the Tab5's table have it
const TRACK = "rgba(255,255,255,0.10)";   // the unfilled remainder, so the gauge has a scale
const DASH = 34, GAP = 26;        // the unknown-level ring; see drawCharge()

// The perimeter as one line, clockwise from the top-left corner: distance 0 is that corner, and
// distance PERIMETER is back at it. Everything below paints RUNS of that line, so a fill, a dash
// and a highlight are all the same operation and a partial edge is exact at any fraction.
const EDGES = [W, H, W, H];
const PERIMETER = 2 * (W + H);

// Paint `len` of the perimeter starting `from` the top-left corner, as rectangles rather than one
// stroked path so the ends land exactly where the arithmetic says.
function paintRun(ctx, from, len) {
  let d = ((from % PERIMETER) + PERIMETER) % PERIMETER, left = len;
  while (left > 0.01) {
    let e = 0, edgeStart = 0;
    while (e < 4 && d >= edgeStart + EDGES[e]) { edgeStart += EDGES[e]; e++; }
    if (e >= 4) { d = 0; continue; }                  // wrapped past the last edge: start again
    const into = d - edgeStart;                        // how far along this edge the run begins
    const take = Math.min(left, EDGES[e] - into);
    if (e === 0)      ctx.fillRect(into, 0, take, THICK);                       // top, L->R
    else if (e === 1) ctx.fillRect(W - THICK, into, THICK, take);               // right, T->B
    else if (e === 2) ctx.fillRect(W - into - take, H - THICK, take, THICK);    // bottom, R->L
    else              ctx.fillRect(0, H - into - take, THICK, take);            // left, B->T
    d += take; left -= take;
    if (d >= PERIMETER) d = 0;
  }
}

/**
 * Draw the gauge, in design space (1280x720), over the finished frame.
 *
 * `charge` is what Face.chargeState() returns: null (draw nothing), or { soc, charging } where
 * `soc` is 0..100 or NULL for "not known, or too old to believe". `t` is seconds.
 *
 * Nothing is drawn for null, and that is checked here rather than trusted to the caller: a gauge
 * left up when the robot is off the charger is a face telling a plain lie about its own hardware.
 */
export function drawCharge(ctx, charge, t = 0) {
  if (!charge) return;
  const known = typeof charge.soc === "number" && isFinite(charge.soc);
  const soc = known ? Math.max(0, Math.min(100, charge.soc)) : null;

  ctx.save();

  // The track: the whole perimeter, faint. It is what makes the filled part a FRACTION rather than
  // just a line of light -- without it, 40% and 90% both read as "some purple at the edge".
  ctx.fillStyle = TRACK;
  paintRun(ctx, 0, PERIMETER);

  if (!known) {
    // UNKNOWN: A DASHED RING, not a continuous one. The first version drew the whole perimeter in
    // the charge colour at low alpha, and a test measuring the painted area caught what that
    // really is -- a FULL ring, which reads as 100%. Drawing "I do not know the level" as the
    // picture of a full battery is the same lie as the Tab5's 0.62 placeholder, arrived at from
    // the other direction. Dashes are the indeterminate idiom and cannot be read as any level.
    const pulse = 0.34 + 0.26 * (0.5 + 0.5 * Math.sin(t * 1.6));
    ctx.fillStyle = `rgba(${PURPLE},${pulse.toFixed(3)})`;
    for (let d = 0; d < PERIMETER; d += DASH + GAP) paintRun(ctx, d, DASH);
    ctx.restore();
    return;
  }

  // The fill: one run from the top-left corner, clockwise.
  ctx.fillStyle = `rgba(${PURPLE},0.95)`;
  paintRun(ctx, 0, (soc / 100) * PERIMETER);

  // A soft travelling highlight ON THE FILLED PART ONLY, so the gauge is visibly live while the
  // level itself barely moves -- an SOC changes over minutes, and a wholly static bar reads as a
  // painted-on border. It is confined to the top edge, where it cannot be read as the level.
  if (soc > 0) {
    const sweep = ((t * 0.22) % 1) * Math.min(W, (soc / 100) * PERIMETER);
    const g = ctx.createLinearGradient(sweep - 90, 0, sweep + 90, 0);
    g.addColorStop(0, `rgba(${PURPLE},0)`);
    g.addColorStop(0.5, "rgba(255,255,255,0.35)");
    g.addColorStop(1, `rgba(${PURPLE},0)`);
    ctx.fillStyle = g;
    ctx.fillRect(Math.max(0, sweep - 90), 0, Math.min(180, W - Math.max(0, sweep - 90)), THICK);
  }

  ctx.restore();
}

// What the gauge occupies, for anyone laying something out near it.
export const CHARGE_BAND = Object.freeze({ thick: THICK, perimeter: PERIMETER });
