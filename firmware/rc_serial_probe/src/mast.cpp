#include "mast.h"
#include <M5StamPLC.h>

// Set BOTH legs in a single transaction, preserving the other relays.
//
// Two back-to-back writePlcRelay() calls do not both land -- the second is lost, which
// showed up as "whichever leg was pulsed second does nothing". Each call is its own I2C
// transaction to the relay expander, and this bus has a documented history of dropping
// closely-spaced transactions. Writing the whole port once sidesteps it, and makes
// "both legs closed at once" -- both buttons held -- unrepresentable rather than merely
// avoided.
//
// NO contacts: energized = closed = pressed.
void ScissorRelayMast::applyRelays_(bool upOn, bool downOn)
{
    uint8_t mask = 0;
    for (uint8_t c = 0; c < 4; ++c)
        if (M5StamPLC.readPlcRelay(c)) mask |= (uint8_t)(1u << c);
    // R0 (e-stop contactor) and any spare keep whatever they currently hold.
    if (upOn)   mask |=  (uint8_t)(1u << relayUp_);   else mask &= (uint8_t)~(1u << relayUp_);
    if (downOn) mask |=  (uint8_t)(1u << relayDown_); else mask &= (uint8_t)~(1u << relayDown_);
    M5StamPLC.writePlcAllRelay(mask);
}

void ScissorRelayMast::startPulse_(bool up, uint32_t nowMs)
{
    pulseUp_  = up;
    pulsing_  = true;
    pulseEnd_ = nowMs + pulseMs_;
    applyRelays_(up, !up);          // exactly one leg closed, in one write
    // Reset the stall reference: whatever we believe now, judge it from here.
    refMs_ = nowMs;
    refHeight_ = heightMm_;
}

void ScissorRelayMast::pulseLeg(bool up, uint32_t nowMs)
{
    // Bench discovery only: pulse the relay and touch nothing else. Deliberately does NOT
    // set motion_ -- doing so made tick() see intent(STOP) != motion and immediately fire a
    // second pulse to "stop", so a single bench command produced two presses.
    startPulse_(up, nowMs);
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
        applyRelays_(false, false);
        pulsing_ = false;
        pulseDone_ = nowMs;
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

    // ---- pinch guard ----------------------------------------------------------
    // Near full-down the platform closes on whatever is under it, so a descent that
    // reaches the zone is stopped and not resumed without consent. This runs on EVERY
    // tick, before the early returns below, so the guard latches the instant the zone is
    // reached rather than whenever the next decision happens to be due.
    MastMotion want = intent_;

    // Consent by gesture: the operator releases the stick, holds it centred for the
    // dwell, and commands DOWN again. Tracked continuously, because the release that
    // matters is the one they make WHILE the guard is holding.
    if (want != MastMotion::DOWN) {
        if (!releasing_) { releasing_ = true; releaseStart_ = nowMs; }
        releaseHeldMs_ = nowMs - releaseStart_;
        // A stop spends the consent: the next descent is a new decision. Only once it has
        // actually been acted on, so a host ack issued before the command survives.
        if (consentUsed_) { consent_ = false; consentUsed_ = false; }
    } else {
        if (releasing_ && pinchHold_ && releaseHeldMs_ >= dwellMs_) {
            consent_ = true; consentMs_ = nowMs; pinchHold_ = false;
            releaseHeldMs_ = 0;         // one grant per release, not one per tick
        }
        releasing_ = false;
    }
    if (consent_ && nowMs - consentMs_ > kConsentMs) { consent_ = false; consentUsed_ = false; }

    // No height means no zone, and the guard is simply not in play — see pinchActive().
    const bool inZone = haveHeight_ && heightMm_ >= 0.0f && heightMm_ <= pinchMm_;
    if (!inZone) {
        pinchHold_ = false;             // above the zone there is nothing to consent to
    } else if (want == MastMotion::DOWN && !consent_) {
        want = MastMotion::STOP;        // stops a descent that arrives here, refuses a new one
        pinchHold_ = true;
    }

    if (pulsing_) return;               // one press at a time
    // Honour the release gap. Returning here defers rather than drops: intent is asserted
    // every tick, so whatever was wanted still happens once the gap has elapsed.
    if (nowMs - pulseDone_ < minGapMs_) return;
    if (want == motion_) return;        // already doing what was asked

    // Refuse to push further into a limit we have already detected, but always allow
    // the opposite direction so the operator can back off it.
    if (want == MastMotion::UP   && atUpper_) return;
    if (want == MastMotion::DOWN && atLower_) return;

    if (want == MastMotion::STOP) {
        // Pulse-toggle: pressing the leg that is currently running stops it.
        startPulse_(motion_ == MastMotion::UP, nowMs);
        motion_ = MastMotion::STOP;
    } else {
        // Pressing the opposite leg reverses; pressing from stopped starts.
        startPulse_(want == MastMotion::UP, nowMs);
        motion_ = want;
        atUpper_ = atLower_ = false;
        if (want == MastMotion::DOWN && inZone) consentUsed_ = true;
    }
}
