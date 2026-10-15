#include "tilt_confirm.h"

namespace neck {

bool request(Pending& p, int lo, int hi, uint32_t nowMs, bool buttonDown)
{
    if (lo >= hi || lo < 0 || hi > 180) return false;    // refused, not clamped
    p.waiting  = true;
    p.lo = lo; p.hi = hi;
    p.deadline = nowMs + kTiltConfirmMs;
    // A button already held does not count. The press has to START after the request, so the
    // release has to be seen first; see the field's comment in the header.
    p.sawRelease = !buttonDown;
    return true;
}

Confirm poll(Pending& p, uint32_t nowMs, bool buttonDown)
{
    if (!p.waiting) return Confirm::None;

    // EXPIRY IS CHECKED FIRST, so a press landing on the very tick the window closes is refused
    // rather than racing. Signed arithmetic, as everywhere else here: `nowMs - deadline` unsigned
    // is about 4.29 billion for a deadline one millisecond in the future, which would expire every
    // pending request instantly. That exact mistake was live in the GAZE lease.
    if ((int32_t)(nowMs - p.deadline) >= 0) { p.waiting = false; return Confirm::Expired; }

    if (!buttonDown) { p.sawRelease = true; return Confirm::None; }
    if (!p.sawRelease) return Confirm::None;             // still held from before the request

    p.waiting = false;
    return Confirm::Applied;
}

uint32_t secondsLeft(const Pending& p, uint32_t nowMs)
{
    if (!p.waiting) return 0;
    const int32_t left = (int32_t)(p.deadline - nowMs);
    return left <= 0 ? 0 : (uint32_t)((left + 999) / 1000);
}

}  // namespace neck
