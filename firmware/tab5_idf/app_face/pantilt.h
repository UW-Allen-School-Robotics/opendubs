// HEAD MOTION: the gaze output that actually turns the head.
//
// Ported from firmware/tab5_face/src/main.cpp (the 8Servo write path) and built to the control law
// in notes/design/head-turn-gaze.md. The omission that note opens with was real: perception moved
// the EYES and nothing closed the loop from "I see a person" to "turn toward them", even though the
// head has had pan/tilt all along. At room distance a 480 px eye shifting a few dozen pixels is a
// small change on a panel nobody is studying; a head that turns is unmistakable.
//
// EYES LEAD, HEAD FOLLOWS, which is how gaze works in people and is also the cheaper thing to
// build. A gaze offset inside kEyeOnly is handled by the eyes alone -- the head does not move for a
// glance. Past that the head commits, and because the head carries the target toward centre the
// eyes give back what the head covered (headOffsetX/Y, the VOR term).
//
// CONFIDENCE GATES THE HEAD, NOT JUST THE AMPLITUDE. A head turn is a commitment and committing to
// a false positive is what makes a robot look broken, so a weak detection costs a twitch of the
// eyes and never a head swing.
//
// SAFETY, which the design note is explicit is not a footnote:
//   - Slew-rate limited HERE, in firmware, not in the command stream. A target jump cannot become
//     a fast head movement near someone standing within reach.
//   - Bounds are hard and enforced HERE, not left as a convention in a caller.
//   - It stops when the robot stops. Autonomous head motion while the base is e-stopped is the
//     wrong behaviour, and with NO LINK we do not know the robot's state -- so the safe reading of
//     "unknown" is "do not move", not "assume idle".
//   - Eyes-only is the safe degraded mode: if the 8Servo does not answer, everything here
//     self-disables and the face behaves exactly as it did before.
//
// The thresholds below are NAMED PLACEHOLDERS, as the design note requires. They can only be
// judged by someone standing in front of the robot watching it turn.
#pragma once

#include <stdint.h>

// Probes the 8Servo. Safe to call unconditionally and on a bench with no head: if nothing answers
// at 0x25 everything here becomes a no-op and pantiltPresent() returns false.
void pantiltBegin();

// False when the 8Servo did not answer, i.e. eyes-only degraded mode.
bool pantiltPresent();

// One step. Rate-limited internally to the servo update period, so call it every frame.
//
//   motionAllowed  false freezes the head where it is. Pass false whenever the robot's state is
//                  E_STOP or DEGRADED, and whenever the state is UNKNOWN (no StamPLC link).
//   haveTarget     whether nx/ny/confidence describe a live target at all.
//   nx, ny         normalised target offset, -1..1, the same convention percept publishes.
//   confidence     0..1. Below kHeadConfidence the head does not commit.
void pantiltTick(uint32_t nowMs, bool motionAllowed, bool haveTarget, float nx, float ny,
                 float confidence);

// How much of the offset the HEAD has taken on, normalised like nx/ny. The eyes subtract this so
// the two do not both aim at the target and overshoot it together. Zero when absent or centred.
float pantiltHeadOffsetX();
float pantiltHeadOffsetY();

// For the panel and the UDP reply. Stable buffer.
const char* pantiltStatus();
