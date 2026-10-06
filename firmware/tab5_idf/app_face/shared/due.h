// WRAP-SAFE DEADLINE TEST, in one place at last.
//
// `now > deadline` inverts when the 32-bit millisecond counter wraps at ~49 days of uptime, after
// which every timer either fires continuously or never again. AUDIT.md invariant 5 exists because
// that bug shipped twice in cues.cpp, and in one of the two places it was UNRECOVERABLE: the early
// return happened before the deadline was advanced, so the repeating fault announcement would never
// have fired again.
//
// This header exists because face_probe.cpp's copy carried the note "third copy in this directory,
// which is one too many: a fourth should become a shared header". The fourth was about to be
// written, so here it is. Subtracting first and THEN signing is what makes the wrap vanish: the
// difference is small and correct across the rollover, while `now < at` is not.
#pragma once
#include <stdint.h>

inline bool due(uint32_t now, uint32_t at)
{
    return (int32_t)(now - at) >= 0;
}
