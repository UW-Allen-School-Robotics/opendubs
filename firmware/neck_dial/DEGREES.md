# The neck's degrees are not degrees

**A decision for the maintainers. Nothing has been changed.**

The pan and tilt servos are **300°** servos. `degToUs()` maps `0..180` to `500..2500 µs`, inherited
verbatim from the Tab5, so the full pulse range sweeps the servo's full 300°:

> **one commanded degree = 1.667 real degrees**, on both axes.

Everything the firmware, the protocol, the shows and the docs call a "degree" is really 1.667 of
one. The numbers are all self-consistent, so nothing misbehaves — they simply do not mean what
they say.

## What that does to the numbers already written down

| constant | as written | really | what it is for |
|---|---|---|---|
| `kSlewDps` | 70 °/s | **117 °/s** | the slew **limit** |
| `kPanRangeDeg` | ±30 | ±50 | autonomous pan envelope |
| `kTiltRangeDeg` | ±18 | ±30 | autonomous tilt envelope |
| `kDeadbandDeg` | 1.5 | 2.5 | servo jitter at rest |
| `kTiltFallbackHalfBand` | ±10 | ±16.7 | the *deliberately tight* uncommissioned band |
| pan bounds `0..180` | ±90 | **±150** | the servo's entire travel |
| tilt band `71..150` | −19 / +60 | −31.7 / +100 | commissioned with somebody watching |

### Two of those are not bookkeeping

**The slew limit is 67% faster than its stated purpose.** `pantilt_core.h` says it exists so that
"a target jump must not become a fast head movement near someone standing within reach". It was
raised to 70 "so a show can nod on the beat" — a judgement someone made while watching a head move
at what they believed was 70 °/s. It is **117 °/s**. Whatever the units decision, this number was
chosen against a physical intent and does not meet it.

**Pan has no commissioned limit and sweeps the servo's whole 300°.** `kRcPanMin/Max` are `0..180`
because "pan was never homed, so full travel is fine for a human" — and full travel turns out to be
±150° from centre, not ±90°. Tilt was commissioned against the mechanism with a person watching;
pan never was. Nobody has established that ±150° is clear of the robot's own frame, the arms, or
the cables. The furthest it has been driven under observation is ±40 commanded (±67 real).

## The decision: should the wire carry real degrees?

**Yes — and I would do the two safety items above first, regardless of how this is decided.**

### For

- A show author, an operator and a doc all write angles that mean what they say. `GAZE 120` should
  turn the head 30°, not 50°.
- The envelopes and the slew limit were all chosen as *physical* properties. In commanded units
  they silently misstate the thing they exist to bound — which is how the slew limit came to be
  67% out without anyone noticing.
- The commissioned band is a physical limit on a mechanism. Expressed in a unit that is not
  degrees, it cannot be checked against the mechanism by anyone holding a protractor.
- Everything added from here (the chest plate, new shows, the build docs) will assume real degrees
  unless told otherwise, and will be wrong.

### Against

- **Every existing `GAZE` in every show changes meaning**, and turns the head 1.667× further than
  it did. Choreography's cue sheets are written in the old unit.
- The RC rates, the envelopes and the band all need re-expressing, including a per-robot value in
  NVS that a person measured by eye.
- `head-protocol.md` documents pan as `0..180`, agreed across lanes. Changing the unit changes the
  protocol's *meaning* without changing its *syntax* — the dangerous kind of change, because
  nothing errors.

### The hazard that shapes the migration

**A stale sender and new firmware over-drive the head.** An old `GAZE 120` means 30° of turn; read
as real degrees it is still 120, so nothing is refused and the head goes where it is told — which
is now 1.667× further than the sender intended. **The failure direction is toward the stops**, and
it is silent.

So the migration cannot rely on everyone updating together. It has to make a mismatch *impossible
to miss* or *impossible to exceed*.

## A migration that is safe at every step

Each step is independently useful and leaves the robot working.

1. **Fix the slew limit now**, in whichever unit is current, so it means what `pantilt_core.h` says
   it means. This is not part of the units decision; it is wrong either way.
2. **Commission pan**, the same way tilt was commissioned: widen a band one notch at a time with
   somebody watching, and store it in NVS beside `tiltMin`/`tiltMax`. Pan then has a fence, which
   is what makes step 4 safe.
3. **Report the units.** `NECK` and `PING` gain `deg_units=commanded|real` and `servo_travel=300`.
   A sender can then tell what it is talking to, and the Thor can refuse to send until it sees what
   it expects. Costs nothing and is reversible.
4. **Switch, with the bands already in place.** With both axes commissioned, a stale sender's
   over-driven command is clamped by the band rather than reaching the mechanism. The fence is what
   makes a mixed-version moment survivable instead of a crunch.
5. **Re-express the stored bands** in real degrees as part of the same build, and have the firmware
   refuse to start in `real` mode against a band stored in the old unit — an NVS key version, not a
   guess.

**The servo's travel belongs in NVS, not in the source.** It is a property of the fitted hardware,
exactly like the tilt band, and a robot built with 270° servos should not need a different
firmware. `servoTravelDeg`, read at boot, defaulting to the conservative 180 — a robot that has not
been told moves *less* than asked, never more.

## If the maintainers would rather not migrate

Then the honest alternative is to **stop calling them degrees.** Rename the constants and the
protocol field to something that is not a unit of angle, state the 1.667 scale in
`head-protocol.md` and in this README, and write the real figure beside every number in the table
above. That is cheap, breaks nothing, and at least stops the next person believing the slew limit
is 70 °/s.

What should not happen is leaving it as it is, with "degrees" meaning two different things
depending on which side of `degToUs()` you are standing on.
