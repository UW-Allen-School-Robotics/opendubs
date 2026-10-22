#include "hold.h"

namespace neck {

Knob knobFeed(KnobGesture& g, int counts, uint32_t nowMs)
{
    if (counts == 0) return Knob::None;
    // A run that has gone on too long, or a turn back the other way, starts again from here.
    const bool reversed = (g.acc > 0 && counts < 0) || (g.acc < 0 && counts > 0);
    if (g.acc == 0 || reversed || nowMs - g.startMs > kKnobWindowMs) { g.acc = 0; g.startMs = nowMs; }
    g.acc += counts;
    const int need = kKnobDetents * kKnobCountsPerDetent;
    if (g.acc >= need || g.acc <= -need) {
        const bool up = g.acc > 0;
        g.acc = 0;
        return up == kKnobUpHolds ? Knob::Hold : Knob::Release;
    }
    return Knob::None;
}

void holdAt(Hold& h, float pan, float tilt, HoldBy by)
{
    h.phase = HoldPhase::On;
    h.by = by;
    h.pan = pan; h.tilt = tilt;
    h.releaseAt = 0;
}

bool holdRelease(Hold& h, uint32_t nowMs)
{
    if (h.phase != HoldPhase::On) return false;
    h.phase = HoldPhase::Releasing;
    // Never 0, which reads as "unset" everywhere else in this firmware.
    h.releaseAt = nowMs + kReleaseAnnounceMs;
    if (h.releaseAt == 0) h.releaseAt = 1;
    return true;
}

bool holdTick(Hold& h, uint32_t nowMs)
{
    // A SIGNED difference, for the reason pantilt_core's GAZE lease is signed: millis() wraps.
    if (h.phase != HoldPhase::Releasing || (int32_t)(nowMs - h.releaseAt) < 0) return false;
    h.phase = HoldPhase::Off;
    h.by = HoldBy::None;
    h.releaseAt = 0;
    return true;
}

const char* holdPhaseName(const Hold& h)
{
    return h.phase == HoldPhase::On ? "on" : h.phase == HoldPhase::Releasing ? "releasing" : "off";
}

const char* holdByName(const Hold& h)
{
    return h.by == HoldBy::Knob ? "knob" : h.by == HoldBy::Remote ? "remote" : "-";
}

}  // namespace neck
