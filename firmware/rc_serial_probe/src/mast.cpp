#include "mast.h"
#include <M5StamPLC.h>
#include <Preferences.h>

// Commissioned per-robot values, kept on the robot. See the note on loadCfg() in mast.h.
void ScissorRelayMast::loadCfg()
{
    Preferences nvs;
    if (!nvs.begin("mastcfg", true)) return;
    pinchMm_  = nvs.getFloat("pinch", pinchMm_);
    dwellMs_  = nvs.getUInt("dwell", dwellMs_);
    pulseMs_  = nvs.getUInt("pulse", pulseMs_);
    minGapMs_ = nvs.getUInt("gap",   minGapMs_);
    nvs.end();
}

void ScissorRelayMast::saveCfg()
{
    Preferences nvs;
    if (!nvs.begin("mastcfg", false)) return;
    nvs.putFloat("pinch", pinchMm_);
    nvs.putUInt("dwell", dwellMs_);
    nvs.putUInt("pulse", pulseMs_);
    nvs.putUInt("gap",   minGapMs_);
    nvs.end();
}

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
    // Reset the stall reference: whatever we believe now, judge it from here — but not
    // until the controller has had time to actually respond. See kStartGraceMs.
    refMs_ = nowMs;
    refHeight_ = heightMm_;
    stallArmMs_ = nowMs + kStartGraceMs;
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
        if ((int32_t)(nowMs - stallArmMs_) < 0) {
            // Still inside the start-up grace. Keep the reference moving with us, so the
            // first JUDGED window begins where the mechanism is actually under way instead
            // of straddling its dead time. Merely delaying the judgement was not enough:
            // the window was still anchored at the pulse, so the first evaluation averaged
            // ~1.3 s of standing still with ~0.7 s of motion and read as a stall -- which is
            // how a lift moving at 10 mm/s got declared stopped, twice.
            refMs_ = nowMs;
            refHeight_ = heightMm_;
        } else if (nowMs - refMs_ > kStallCheckMs) {
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
    // Entering and leaving use different thresholds: parked just inside, sensor noise crossed
    // a single boundary repeatedly and made the indication stutter.
    if (!haveHeight_ || heightMm_ < 0.0f)                     inZone_ = false;
    else if (!inZone_ && heightMm_ <= pinchMm_)               inZone_ = true;
    else if (inZone_ && heightMm_ > pinchMm_ + kZoneHystMm)   inZone_ = false;
    const bool inZone = inZone_;

    if (!inZone) {
        pinchHold_ = false;             // above the zone there is nothing to consent to
        guardMark_ = -1.0f;             // re-arm the backstop for the next entry
    } else if (want == MastMotion::DOWN && !consent_) {
        want = MastMotion::STOP;        // stops a descent that arrives here, refuses a new one
        pinchHold_ = true;
        refuseMs_ = nowMs;
    } else if (pinchHold_) {
        // Nobody is asking to go down any more. Stop announcing a refusal that is no longer
        // happening -- and never announce one at the bottom limit, where there is nothing
        // left to refuse. The guard stays ARMED either way: consent_ is untouched, so the
        // next DOWN demand re-latches immediately.
        if (atLower_ || nowMs - refuseMs_ > kHoldShowMs) pinchHold_ = false;
    }

    // ---- backstop: act on the measurement, not on the belief ----
    // Everything above assumes we know whether the lift is running. We might not. So while
    // we are in the zone without consent, watch the height itself: if the platform is still
    // falling, press stop -- whatever motion_ says.
    //
    // Falling is the PRECONDITION, not an inference. On a pulse-toggle controller a press
    // starts a stopped lift, so pressing without evidence of motion would cause the very
    // thing the guard exists to prevent. The mark is seeded on entry and only a fall of
    // kFallMm past it (well clear of the +-4 mm sensor noise) counts.
    if (inZone && !consent_ && haveHeight_) {
        if (guardMark_ < 0.0f) {
            // Just entered the zone. Seed the mark from here and judge immediately — this is
            // the case where the belief may already be wrong, so there is nothing to wait for.
            guardMark_ = heightMm_;
            guardSettle_ = 0;
        } else if ((int32_t)(nowMs - guardSettle_) < 0) {
            // Settling after our own press: track the height instead of judging it, so the
            // deceleration we caused is not read back as fresh motion.
            guardMark_ = heightMm_;
        } else if (!pulsing_ && nowMs - pulseDone_ >= minGapMs_
                   && heightMm_ < guardMark_ - kFallMm) {
            // Still coming down. Press the running leg to stop it, then let it coast before
            // looking again -- the deceleration itself drops the height, and reading that as
            // fresh motion would press a second time and start it moving down again.
            startPulse_(false, nowMs);
            motion_ = MastMotion::STOP;
            pinchHold_ = true;
            refuseMs_ = nowMs;
            guardMark_ = heightMm_;
            guardSettle_ = nowMs + kSettleMs;
            return;
        }
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
