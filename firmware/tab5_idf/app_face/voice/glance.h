// A GLANCE TOWARD A VOICE: one time-boxed move that CLAIMS NOTHING. Pure compute, no HAL.
//
// `design/hri-interaction-design.md` is explicit and this file exists to obey it rather than
// re-decide it:
//
//   "So: do not build a head that continuously tracks a voice. Build one that glances toward it."
//
// The note's reasoning is worth keeping in view, because it is what makes the cheap thing also the
// right thing. Our microphone resolves left / right / centre and a level-ratio bearing is noisy.
// Continuous smooth tracking would expose that noise as jitter; discrete glances HIDE it, "because
// a glance is allowed to be approximate and then end". And the literature the note cites prefers
// the same behaviour independently: "multiple short fixations beat one long stare". So re-glancing
// is WANTED here, not something to suppress -- only continuous aiming is forbidden.
//
// ============================================================================================
// WHY THIS TAKES NO LEASE, which is the load-bearing part.
//
// pantilt.cpp arbitrates touch > RC > tracker, and that hierarchy's ownership fight cost a whole
// session to get right. A voice tracker added as a FOURTH lease-holder would be re-opening it.
//
// So a glance is not a lease and does not enter the hierarchy at all. It enters through the SAME
// per-frame door vision uses -- pantiltTick()'s (haveTarget, nx, ny, confidence) -- and it is
// offered only when VISION HAS NOTHING. Touch and RC therefore outrank it automatically, with no
// new precedence to reason about and not one line changed in pantilt.cpp.
//
// "Only when vision has nothing" is also the correct behaviour and not merely the safe one: if the
// robot can already SEE a person it is already looking at them, and twitching toward a noise would
// fight the tracker and read as broken. A glance matters precisely when someone speaks from
// OUTSIDE the camera's view, which is exactly when vision has nothing to say.
// ============================================================================================
#pragma once
#include "level.h"
#include <stdint.h>

void glanceBegin();

// Offer a bearing, normally on the `Heard` event. Returns true if this started a glance.
//
// It can REFUSE, and the refusals are the design:
//   * None    -- no bearing, nothing to glance at.
//   * Centre  -- the voice is already in front. The head does NOT move; the earcon is the whole
//                acknowledgement. Turning toward something you already face reads as a twitch.
//   * inside the refractory window -- a glance is still running or has only just ended. This is
//                what keeps "multiple short fixations" from degenerating into the continuous
//                aiming the design note forbids.
bool glanceTrigger(uint32_t nowMs, VoiceDir d);

// Is a glance steering right now. Call every frame; it expires on its own and NEVER extends.
bool glanceActive(uint32_t nowMs);

// The target to hand pantiltTick(), in percept's own -1..1 convention. Only meaningful while
// glanceActive(). ny is always 0: the microphones resolve nothing vertical, and inventing a tilt
// would be fabricating a measurement we do not have.
float glanceNx();
float glanceNy();
float glanceConfidence();

const char* glanceStatus();
