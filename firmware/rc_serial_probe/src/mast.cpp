#include "mast.h"
#include <M5StamPLC.h>

void ScissorRelayMast::setRelay_(uint8_t ch, bool on)
{
    M5StamPLC.writePlcRelay(ch, on);   // NO contacts: energized = closed = pressed
}

void ScissorRelayMast::startPulse_(bool up, uint32_t nowMs)
{
    pulseUp_  = up;
    pulsing_  = true;
    pulseEnd_ = nowMs + kPulseMs;
    setRelay_(up ? relayUp_ : relayDown_, true);
    // Reset the stall reference: whatever we believe now, judge it from here.
    refMs_ = nowMs;
    refHeight_ = heightMm_;
}

void ScissorRelayMast::pulseLeg(bool up, uint32_t nowMs)
{
    startPulse_(up, nowMs);
    motion_ = up ? MastMotion::UP : MastMotion::DOWN;
}

void ScissorRelayMast::setIntent(float v)
{
    if (v >  kDeadzone)      intent_ = MastMotion::UP;
    else if (v < -kDeadzone) intent_ = MastMotion::DOWN;
    else                     intent_ = MastMotion::STOP;
}

void ScissorRelayMast::tick(uint32_t nowMs)
{
    // Close any pulse that has run its course before deciding anything new — the
    // controller reads a momentary press, so the contact must not stay closed.
    if (pulsing_ && nowMs >= pulseEnd_) {
        setRelay_(pulseUp_ ? relayUp_ : relayDown_, false);
        pulsing_ = false;
    }

    // ---- height, and the belief correction it enables ----
    if (height_ != nullptr) {
        float mm;
        if (height_(&mm)) {
            haveHeight_ = true;
            heightMm_ = mm;
        }
    }

    if (haveHeight_ && motion_ != MastMotion::STOP && !pulsing_) {
        // Commanded to move but the height is not changing => we are not moving. Either
        // the controller reached its own internal limit, or it never saw our pulse. Both
        // mean our belief is wrong, and a wrong belief inverts pulse-toggle semantics:
        // the operator's next command would be read as the opposite of what they meant.
        if (nowMs - refMs_ > kStallCheckMs) {
            if (fabsf(heightMm_ - refHeight_) < kMovedMm) {
                if (motion_ == MastMotion::UP) atUpper_ = true; else atLower_ = true;
                motion_ = MastMotion::STOP;
            } else {
                // Genuinely moving: clear the limit flags and re-arm the window.
                atUpper_ = atLower_ = false;
            }
            refMs_ = nowMs;
            refHeight_ = heightMm_;
        }
    }

    if (pulsing_) return;               // one press at a time
    if (intent_ == motion_) return;     // already doing what was asked

    // Refuse to push further into a limit we have already detected, but always allow
    // the opposite direction so the operator can back off it.
    if (intent_ == MastMotion::UP   && atUpper_) return;
    if (intent_ == MastMotion::DOWN && atLower_) return;

    if (intent_ == MastMotion::STOP) {
        // Pulse-toggle: pressing the leg that is currently running stops it.
        startPulse_(motion_ == MastMotion::UP, nowMs);
        motion_ = MastMotion::STOP;
    } else {
        // Pressing the opposite leg reverses; pressing from stopped starts.
        startPulse_(intent_ == MastMotion::UP, nowMs);
        motion_ = intent_;
        atUpper_ = atLower_ = false;
    }
}
