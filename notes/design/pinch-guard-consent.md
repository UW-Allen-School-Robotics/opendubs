# The Z-axis pinch guard: why the descent feels inconsistent

Draft, 2026-09-22. Internal. Analysis plus a proposal; **no safety constants changed yet.**

## The report

"It's not always consistent and it seems like a *just right* flick of the stick is required to go
fully down."

## What the guard actually asks for

From `mast.cpp`, the consent path, in order:

1. The lift descends into the zone. The guard **stops it** and latches `pinchHold_`.
2. The operator must **release** — intent not DOWN — and hold that release for `dwellMs_`
   (**600 ms** default).
3. Then command DOWN again. Only now is consent granted, `pinchHold_` cleared, descent resumes.
4. **Consent is spent when the descent stops.** `consentUsed_` means the next descent through
   the zone is a fresh decision needing the whole gesture again.
5. An unused grant goes stale after `kConsentMs` (15 s).

The intent is sound and I would not weaken it: the bottom of a scissor lift is a shear point, and
"every resumed descent inside the zone is its own decision" is the right principle.

## Why it feels inconsistent

Three things stack up, and all three are invisible to the operator:

**The release has a minimum duration.** A quick flick down-up-down is under 600 ms of centred
stick, grants nothing, and feels like the robot ignored you. There is no feedback distinguishing
"not yet released long enough" from "guard is holding".

**The zone boundary moves.** Measured on opendubs2 today: the guard holds at heights between
**55 and 61 mm**, so the commissioned `pinchMm` is ~60 mm. The ToF's own noise is roughly
**±10 mm** (observed 24–42 mm spread while the lift was stationary), against `kZoneHystMm` = 10 mm
of hysteresis. The sensor noise is the same size as the hysteresis, so which side of the boundary
you are on varies run to run.

**A mid-descent stop silently re-arms the requirement.** If the lift stops for any reason inside
the zone, the gesture is needed again, with nothing announcing that.

Put together: below ~60 mm you need a deliberate 600 ms release, the threshold wanders by ±10 mm,
and the requirement can silently return. That is exactly "sometimes it works, sometimes I need
just the right flick".

## Proposal

Ordered by value per unit of risk. Nothing here weakens the guard's rule.

### 1. Tell the operator what the robot is waiting for (no safety change)

Today the only signal is the PRISM going red on hold (`PrismOverlay::MAST_HOLD`), which says
"held" but not "here is how to proceed". We now have a speaker and a screen on the head:

- **On entering hold:** a tone, and on the face "RELEASE, THEN PRESS DOWN AGAIN".
- **While releasing:** show the dwell progressing, so 600 ms is visible rather than guessed.
- **On consent granted:** a short confirming tone — the gesture gets an acknowledgement.

This alone probably resolves the complaint, because the gesture stops being a guess. It is also
the first genuinely useful thing the new audio does, and it exercises the SAFETY tier of the
behaviour API.

### 2. Make the boundary stop wandering (small, testable)

`kZoneHystMm` = 10 mm against ±10 mm of sensor noise is too tight. Widen the hysteresis to
**20–25 mm** so entering and leaving the zone are unambiguous, or filter the height (median of
the last N reads) before the comparison. Prefer the filter: it also helps the belief correction
and the backstop, which share the same number.

This changes *when* the guard engages, never *whether* it does — the entry threshold stays.

### 3. Reconsider the dwell, only with the prompt in place

600 ms is not obviously wrong; it is only wrong when invisible. With feedback, it may feel fine.
If it still frustrates, 300–400 ms with a clear prompt is likely better than 600 ms without one.
Change one thing at a time and re-test with the same operator.

### 4. Leave re-consent after a stop alone

It is the most surprising behaviour and the most defensible. Once the operator can *see* that
consent was spent, it should stop being surprising.

## What a behaviour API needs from this

A composer cannot script its way through the zone, and should not be able to. The cue sheet needs
a first-class "ask the human and wait" step: request the descent, surface the prompt, wait for
consent or time out, and report which happened. `showcase_demo.py` currently just watches the
descent stop and reports `pinch=HOLD`, which is honest but not interactive.

## Open questions

- Is the commissioned ~60 mm threshold right for this lift, or inherited? `kPinchMm` defaults to
  40 mm ("~20 mm finger + stopping distance"); this robot has ~60 mm saved in NVS.
- What is the lift's actual stopping distance from full speed at the bottom? The threshold should
  be derived from it, and I have not measured it.
- Should `MAST ACK` (the host consent path) be exposed to the dashboard at all? Today it exists
  for automated lowering; if a composer can ack its own descent, the guard's meaning changes.
