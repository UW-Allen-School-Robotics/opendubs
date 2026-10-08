# Turning to look: head motion as the gaze output

Written 2026-10-05, after noticing an omission in my own framing rather than a bug in the code.

## The omission

Face tracking currently terminates at the eyes. The perception layer produces an attention target,
the face maps it to pupil offset, and that is the end of the chain. **But the robot has a pan/tilt
head, and nothing closes the loop from "I see a person" to "turn toward them."**

In the shipping Arduino firmware `TURN L/R` is a command *from outside* and `PANTILT` is a D-pad
mode for an operator. The head moves when a human or the Thor says so. It has never moved because
the robot noticed someone. On the IDF side even that much does not exist.

**Why this matters more than it sounds.** At room distance, an eye 480 px across shifting by a few
dozen pixels is a small change on a panel someone is not studying. A head that *turns* is the
signal — it is visible peripherally, from across a room, and it is the thing people actually read
as attention. Eye-only tracking is the least legible part of the gaze system doing the most work.

It also explains a result we already have: the detector's ~1.1 m range looked like the binding
problem, but a person at 3 m would not reliably notice eye-only tracking even with a perfect
detector. Range and legibility are separate limits and we had only been treating one.

## The control law: eyes lead, head follows

This is the "gaze leads motion" item from early in the project, and it is not a stylistic choice —
it is how gaze actually works, and copying it is what makes the motion read as deliberate rather
than mechanical.

```
target offset (normalised, from the attention layer)
        |
        +--> |offset| <= kEyeOnly ............ eyes only. The head does not move for a glance.
        |
        +--> |offset| >  kEyeOnly ............ eyes saccade FIRST (they are fast and already do
                                               this), then the head begins to turn, and as the
                                               head carries the target toward centre the eyes
                                               RE-CENTRE by the amount the head has covered.
```

Three properties fall out, and each is a thing we would otherwise have had to invent:

- **The eyes always arrive first.** A saccade is 20–200 ms; a servo is far slower. Moving them
  together makes the head look like it is dragging the eyes. Moving the eyes first and letting the
  head catch up is both correct and cheaper to implement.
- **The re-centring is the vestibulo-ocular reflex**, which `hri-interaction-design.md` already
  flags as the highest-value non-obvious gaze behaviour on a mobile base. Same plumbing: eye
  position = target − head position. Once the head moves, VOR is not an extra feature, it is the
  subtraction already in the expression.
- **Confidence grading already decides commitment.** The attention layer grades a weak hit as a
  glance and a strong repeated one as a sustained look. Map that directly: **a glance never moves
  the head.** A head turn is a commitment, and committing to a false positive is what makes a robot
  look broken. The detector's uncertainty should cost a twitch of the eyes, never a head swing.

## What has to be decided by measurement, not argument

Every number below is a placeholder with the observation that replaces it. None should be shipped
as reasoned values.

| parameter | placeholder | what settles it |
|---|---|---|
| `kEyeOnly` — offset below which the head never moves | 0.25 | the angle at which eye-only tracking stops reading as "looking at me" from ~2 m. A person judges this in one pass. |
| head slew rate | slow | the fastest motion that does not read as a lunge to someone standing within reach. Err slow; this is a robot near people. |
| re-target hysteresis | — | how much the target must move before the head re-commits, or it will hunt between two people |
| settle deadband | — | servo jitter at rest is worse than no motion at all |
| VOR gain | 1.0 nominal | measured against the IMU: too low and the eyes drift during a turn, too high and they overshoot backwards |

## Safety, which is not a footnote here

**Head motion is physical motion near people**, and it is the first autonomous motion this robot
would make that is *triggered by noticing a person* rather than commanded. That deserves more care
than a servo write.

- **Slew-rate limited in firmware**, not just in the command stream. A target jump must never
  become a fast swing.
- **Bounded travel**, enforced at the lowest level. `opendubs2-arm-envelope` records that the
  shoulder can strike the head; the head's own limits must be hard, not a convention in a caller.
- **It must stop when the robot does.** Autonomous head motion while the base is e-stopped is the
  wrong signal entirely — a stopped robot that is still looking around reads as not-stopped.
  The IMU/self-motion gating already in the perception layer gives us the input.
- **Eyes-only is the safe degraded mode.** If pan/tilt is absent, out of range, or disagrees with
  its feedback, the gaze system should fall back to eye-only and say so, not stop tracking.

## Interface

The seam exists: the perception branch already carries `perceptNoteHeadCommand()` with no caller.
The attention layer should emit a **head intent** (a target angle plus a commitment level) rather
than servo positions, and a head module owns the slew limiting, the bounds and the feedback. That
keeps perception pure, and it matches how the touch UI emits intents rather than opening sockets.

Compatibility: the existing `TURN L/R` and `PANTILT` commands must keep working. An operator
command outranks autonomous gaze, and taking manual control should suppress tracking for a
timeout rather than fighting it.

## Status

**Designable and buildable now; verifiable only on the robot.** The bench Tab5 has no pan/tilt
assembly, so the control law, the slew limiter, the bounds and the fallback can all be written and
unit-tested on the host — the perception branch already demonstrates that pattern with
`build.sh percept` — but nothing about the motion's *feel*, which is the whole point, can be judged
without the head.

The honest sequencing: write it, host-test the law against synthetic targets, and leave the
thresholds as named placeholders until someone can stand in front of the robot and watch it turn.
