# Eyes and gaze on the tablet: is scaling the eyes the right channel?

**The question, asked directly by the maintainers on 2026-10-08:** *"should we be just scaling the
eyes, or are there better approaches?"*

**THE DECISION: no. Stop using scale as the expressive channel and move expression into LID SHAPE.**
Keep scale for identity and for the two or three emotes where a size change genuinely is the
meaning (surprise, squint). Everything else — pleased, attentive, curious, tired, sceptical — is
carried better by the shape of the upper and lower lids, by pupil size, and by a catchlight that
behaves like a reflection instead of a decal.

This note is written against what the code actually does, not against what the earlier notes
proposed. Everything in "Where it stands" below was read out of the tree on 2026-10-09.

---

## Where it stands, verified

`software/face-web/engine/face.js` and `engine/skins/eyes.js` are the whole face. Per eye the skin
receives: `cx, cy, w, h, radius, lid, browRaise, browAngle, gazeX, gazeY, colour`.

**Expression is `EMOTES[name] = [wm, hm, rf, lid, br, ba]`** — a width multiplier, a height
multiplier, a corner-radius fraction, a lid height, a brow raise and a brow angle. So four of the
six expressive numbers are **scale**, one is a lid *height*, one is a brow.

**Gaze is a pupil translated inside a body that never moves:**

```js
const px = cx + e.gazeX * Math.max(0, (w - pw) / 2 - RIM_PAD);
```

**What is already good, and should be kept rather than redesigned:**

- **Main-sequence saccade timing.** `aim()` uses ~20 ms + 2.2 ms per degree, capped at 200 ms. That
  is a real oculomotor model and it is why the existing eyes already read better than most.
- **A blink coupled to large gaze shifts** — `if (Math.abs(this.gazeX.target) > 0.55) this.nextBlink = now`.
- **A plausible saccade amplitude distribution** — 60% small, 30% medium, 10% large.
- **Vertical gaze compressed to 55%** of horizontal (`GAZE_V`), which matches that eyes move less
  vertically than horizontally.

**What is absent:** any lower lid; any curve in the upper lid (it is `fillRect` — a straight band);
pupil-size variation (fixed at `PUPIL_FRAC = 0.34`); vergence (both eyes get the same `gazeX`);
microsaccades (the eye is perfectly still between saccades); smooth pursuit (a moving target
produces a *series of saccades*); eye–head coordination; and any limit on how long the robot will
hold a person's gaze.

---

## Why scale is the wrong channel

Three reasons, in descending order of how much they matter.

**1. Scale changes the creature, not the mood.** A human eye does not get 30% wider when its owner
is pleased — the *lids* change, the pupil changes, the aperture changes shape. Driving expression
with `w`/`h` multipliers makes each emote read as a slightly different animal rather than as the
same one feeling something. This is the core of the answer to the question.

**2. It fights the geometry the project already measured.** `face-design.md` measured the Tab5 face
against a human and found **"the dominant error is spacing, not size"** — the eyes sat at 52% of
human interpupillary distance and 3× human aperture height, and it concluded that *making the eyes
smaller earlier in this project made things worse rather than merely different.* Per-emote scaling
perturbs exactly the proportion that was found to matter.

**3. It is the least information per pixel moved.** A 30% width change repaints the whole eye to
say one thing. A 6-pixel lower-lid raise repaints a sliver and says something more specific.

### An unfinished piece of that measurement, worth deciding at the same time

`face-design.md` recommended widening the interpupillary distance to **~729 px** with a **347 px**
eye, giving an inner gap of about one eye width — the human proportion. The engine today has:

```js
const BODY = 480, GAP_FRAC = 0.34;   // IPD = 643 px, inner gap = 163 px
```

So the IPD was widened (380 → 643) but not to the recommended 729, and the eye was made *larger*
(480) rather than human-scale (347). The result is an **inner gap of 0.34 eye widths where the human
proportion is about 1.1.** The spacing error the measurement called dominant is therefore still
about two-thirds uncorrected, in the direction the note warned about.

That is not a criticism of the choice — the baby-schema argument in the same note explicitly
licenses eyes larger than human — but **large eyes and human spacing are independent dials, and only
one of them was turned.** It belongs in the same side-by-side as the lid work, because it is the
cheapest change with the largest measured effect and it is a one-line edit.

---

## What to use instead, in order of payoff per unit of work

Ordered so that the first three are a day's work and settle the question; the rest need data from
the Tablet lane's tracker.

### 1. Lid SHAPE, with a lower lid. The single biggest change.

Today: `ctx.fillRect(x - 2, y - 2, w + 4, e.lid * h + 2)` — a straight horizontal band descending
from the top, and nothing from below.

A curved upper lid plus a lower lid gives, with no change to eye size:

| expression | upper lid | lower lid | today's equivalent |
|---|---|---|---|
| pleased / "smiling eyes" | gentle curve, slight lower | **raised, convex** | eye squashed shorter |
| attentive | high, flat | neutral | eye made wider |
| sceptical | one lid lower (asymmetric) | neutral | brow angle alone |
| tired | low, heavy curve | slightly raised | eye squashed shorter |
| surprise | high, round aperture | lowered | eye scaled up — **genuinely right here** |

The "pleased" row is the one that proves the point. A smile in the eyes is a **lower-lid raise**;
it is not a smaller eye.

**The shipping vocabulary has no pleased expression at all.** The five emotes are `NEUTRAL`,
`ALERT`, `SQUINT`, `SOFT` and `SLEEPY`. `SOFT` is the nearest and reaches for it with a 12% lid and
a -7 degree brow tilt, because a lower lid is not available to it. That is the gap this channel
fills, and it is why the prototype adds a `PLEASED` - to show the channel doing something the
existing one cannot.

*(An earlier draft of this note said `HAPPY` "has to be a squashed eye". There is no `HAPPY` emote;
the squashed eyes are `SQUINT` at 0.56 height and `SLEEPY` at 0.40. The corrected claim is the
stronger one.)*

**Why this was not done on the Tab5:** `tab5-face-expressiveness.md` measured the two-eye frame at
**27.6 fps**, with the eye forced into PSRAM because the radio needs the internal SRAM, and the
whole note is an argument about where to find frames. A per-frame curved path with two lids was not
affordable. On the tablet — 90 Hz and a GPU — it is free.

### 2. ~~A catchlight fixed to the LIGHT~~ — PROPOSED, TRIED, AND REJECTED 2026-10-09

**Settled: the glint rides the pupil. Do not re-propose anchoring it.**

I proposed anchoring the highlight to the eye body so the pupil slides underneath it, on the
grounds that a real catchlight is a reflection of the room. It was built in the prototype, the
maintainers looked at it, and it reads as wrong.

**The record already said so, and was split** — which is why this reached the prototype at all:

- `firmware/tab5_idf/app_face/app_face.cpp:893`, **the shipping head**: *"CHILD OF THE PUPIL. A
  catchlight is a corneal reflection sitting over the iris and pupil, not a spot floating on the
  sclera"*, and parenting it there means it travels with the pupil and can never escape the eye.
- `firmware/tab5_face/src/face.cpp:215`, **the retired Arduino head**: *"ONE highlight, and it
  stays put … a highlight that chases the gaze is the wrong primitive for this kind of eye at
  all."*

Two heads, opposite conclusions, neither referring to the other. The shipping one governs.

**And the physics argument was overstated.** A corneal reflection does stay fixed while the pupil
moves under it — that is the basis of pupil/corneal-reflection eye tracking — but **this eye has no
cornea and no sclera.** It is a flat slab with a dark disc on it, so the reflection has nothing to
be fixed relative to. That is what "the wrong primitive for this kind of eye" meant, and it was
right.

### 3. Pupil size as an affective channel. Nearly free.

`PUPIL_FRAC` is a constant. Making it a spring driven per-emote (and later by arousal) adds a
channel that is strongly associated with interest and is completely unused today.

### 4. Microsaccades, so the eye is not frozen between saccades.

Between saccades `gazeX`/`gazeY` are static, which is the uncanny part of most robot eyes. A small
continuous jitter — fractions of a degree, a few per second — is cheap and is most of the difference
between "alive" and "paused".

### 5. Smooth pursuit, instead of a chain of saccades.

`tick()` re-aims whenever the target has moved more than 0.04, so following a walking person is a
*stutter of saccades*. Real tracking is smooth pursuit with occasional catch-up saccades. This also
matters for the camera: `head-protocol.md` says a tracker needs to know when the head is moving so
it can ignore frames taken mid-turn.

### 6. Vergence — newly possible, and it needs the Tablet lane.

Both eyes receive the same `gazeX`, so they are always parallel: the robot always looks at infinity.
With a **distance** estimate the eyes can converge on a near face, which is a strong and almost
never implemented cue. **This requires the Tablet lane's tracker to publish distance.**

### 7. Eye–head coordination, newly possible.

The eyes should lead and the head follow — `head-turn-gaze.md`'s control law, which the Tab5
firmware implements and the web engine has no equivalent of. `pantilt.cpp` already computes
`pantiltHeadOffsetX/Y` as the VOR term so the eyes give back what the head covered. The web face has
no notion of a neck at all yet; with `TRACK`/`NECK` agreed in `head-protocol.md`, it can.

### 8. A limit on mutual gaze.

`tick()` sets `nextSaccade = now + 600` for as long as a confident target is present, so the robot
will hold a person's gaze indefinitely. `hri-interaction-design.md` already rules the opposite:
**"multiple short fixations beat one long stare"**, and it records the four-phase pattern
*attract attention → make eye contact → avert → shift to the object*. The engine implements the
first two and never averts. An aversion timer is a handful of lines and prevents the one failure a
person actually notices.

---

## Alternatives, and what each costs

**A. Keep scale, add lids on top.** Cheapest in review effort; keeps every existing emote working
unchanged. Cost: two channels fighting each other — the emote table would set both a size and a lid
shape for the same feeling, and the next person would not know which to edit. It also leaves `HAPPY`
as a squashed eye.

**B. Replace the emote table wholesale with a lid-and-pupil vocabulary.** Cleanest result. Cost: it
changes the skin contract, which `CLAUDE.md`'s lane rules say is **shared with the image/video skins
and future Rive/SVG skins** and must be extended with the Tablet lane rather than forked. Every
skin would need the new fields. That is a cross-lane negotiation, not a unilateral change.

**C. (Recommended) Extend the contract additively, retune the emote table to use the new channels,
and leave scale available.** The contract gains `lidUpper`, `lidLower`, `lidCurve`, `pupil` — all
optional with defaults that reproduce today's look exactly, so the asset and ring skins keep working
untouched and an old skin is still valid. Then the emote table is rewritten to express feeling
through lids and pupil, keeping scale only where it is the meaning (surprise, squint).

---

## Prototype plan: three skins, side by side, in the laptop twin

Skins are in my lane, and the page selects them with `?skin=` already, so this needs **no change to
the Tablet lane's files**. The twin runs on the laptop:

```bash
cd software/face-web && python3 door_dev.py
```

Then open one of:

| URL | what it shows |
|---|---|
| `…/?skin=eyes&hud=1` | **today**, unchanged, as the baseline |
| `…/?skin=eyes2&hud=1` | **shape, not scale**: curved upper + lower lids, anchored catchlight, variable pupil. Same emote names, retuned |
| `…/?skin=eyes3&hud=1` | eyes2 **plus** microsaccades, smooth pursuit and an aversion timer |
| add `&ipd=729&eye=347` | the human-proportion geometry, on any of the three |

Three things make this worth watching rather than reading: the "pleased" expression is the whole
argument and cannot be described; the catchlight change is invisible in text and obvious in motion;
and the geometry question (`ipd`) is a preference that only looking can settle.

**What the prototype cannot settle:** vergence and distance-driven behaviour, because the twin has
no distance input until the Tablet lane's tracker publishes one. Those stay on paper here.

---

## What the maintainers must decide

1. **Expression channel — lids or scale?** Recommended: **option C**, lids and pupil carry feeling,
   scale kept only for surprise and squint. *Answer: "C", or name another.*
2. **Geometry — widen the eye spacing to the measured human proportion?** Recommended: **yes, try
   it in the prototype first.** The measurement calls spacing the dominant error and it is still
   two-thirds uncorrected. *Answer: "widen", "leave it", or "show me both" (the default).*
3. **Mutual-gaze aversion — may the robot stop holding someone's gaze?** Recommended: **yes**, with
   a timer. It is the one item here that changes how the robot behaves toward a person rather than
   how it looks. *Answer: yes / no.*

Nothing below item 3 needs deciding yet. Vergence and eye–head coordination both wait on data from
other lanes, and I will ask for it rather than assume it.
