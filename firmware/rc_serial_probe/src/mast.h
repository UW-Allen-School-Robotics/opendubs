// Z-axis (mast / lift) abstraction.
//
// Two mechanisms are planned and they have almost nothing in common electrically:
//
//   Scissor lift  two dry contacts into a commercial controller's up/down buttons,
//                 plus an ABSOLUTE height from a ToF sensor. Pulse-toggle: one press
//                 starts it, another stops it.
//   Lead-screw    an ESC, limit switches for homing, an encoder for position — RELATIVE
//                 feedback that means nothing until it has homed.
//
// Everything above this interface — RC handling, the safety gate, telemetry — is
// identical for both, so swapping mechanism is one line and no policy moves. A future
// mast with its own controller on CAN implements the same interface.
//
// Two decisions worth knowing:
//
//   * setIntent() always receives FULL RESOLUTION, whatever the mechanism can use. The
//     scissor controller has no speed input so its driver uses sign only, but the
//     information is not thrown away upstream, because a lead-screw build needs it. You
//     cannot recover fidelity discarded at the source.
//   * ready() means "position is trustworthy" — a valid ToF read for the scissor, HOMED
//     for a lead-screw. Same gate, different evidence, and it gives homing-before-motion
//     for free later.
#pragma once
#include <Arduino.h>

enum class MastMotion : uint8_t { STOP = 0, UP = 1, DOWN = 2 };

class MastDriver {
public:
    virtual ~MastDriver() {}

    // -1..+1. Positive is UP. Called every drive tick; the caller passes 0 whenever the
    // safety gate is not satisfied, so "stopped" is asserted continuously rather than
    // being a one-shot command that could be missed.
    virtual void setIntent(float v) = 0;
    virtual void tick(uint32_t nowMs) = 0;

    virtual bool proportional() const = 0;   // can the mechanism use the magnitude?
    virtual bool ready() const = 0;          // is position trustworthy?

    virtual MastMotion motion() const = 0;   // what the driver BELIEVES it is doing
    virtual float heightMm() const = 0;      // < 0 when unknown
    virtual bool  atUpper() const = 0;
    virtual bool  atLower() const = 0;
    virtual const char *name() const = 0;
};

// ---------------------------------------------------------------------------------
// Scissor lift: two StampPLC relays standing in for the controller's up/down buttons.
//
// Contacts are NORMALLY OPEN and a closure is a press — measured on the DIHOOL
// controller. That direction matters: de-energized then means not-pressed, so a dead
// StampPLC, a power cut or crashed firmware all produce no motion. Wired the other way,
// an unpowered robot would hold the button down.
//
// The controller is PULSE-TOGGLE, so this driver tracks what it believes the lift is
// doing and emits a pulse only on transitions. That belief can drift — a pulse the
// controller misses, or the lift reaching its own internal limit — so where a height
// source is available the driver corrects itself: commanded to move, not moving,
// therefore not moving. Without a height source it still works, but open-loop.
class ScissorRelayMast : public MastDriver {
public:
    // Height supplier: returns true and fills mm when a reading is available. Passing
    // nullptr (or a supplier that always returns false) leaves the driver open-loop —
    // usable for relay bring-up before the ToF is wired, with no belief correction.
    using HeightFn = bool (*)(float *mm);

    ScissorRelayMast(uint8_t relayUp, uint8_t relayDown, HeightFn height)
        : relayUp_(relayUp), relayDown_(relayDown), height_(height) {}

    void setIntent(float v) override;
    void tick(uint32_t nowMs) override;

    bool proportional() const override { return false; }   // controller has no speed input
    bool ready() const override { return true; }           // ToF is optional here; see note above
    MastMotion motion() const override { return motion_; }
    float heightMm() const override { return haveHeight_ ? heightMm_ : -1.0f; }
    bool  atUpper() const override { return atUpper_; }
    bool  atLower() const override { return atLower_; }
    const char *name() const override { return "scissor"; }

    // Bench helper: drive one leg directly, bypassing intent. Used to discover which
    // relay moves the lift which way, which is a per-robot wiring fact.
    void pulseLeg(bool up, uint32_t nowMs);

    // ---- pinch guard: the last stretch of descent ----
    // A scissor lift closing on its own base is a shear point, and the bottom of the
    // travel is where it gets you: everywhere else you have to put a hand INTO the
    // mechanism to be caught, but near full-down the gap simply closes on whatever is
    // resting under it. So descent stops on its own at `pinchMm` and will not resume
    // without a fresh, deliberate act. UP is never gated -- backing off a hazard must
    // always be possible, instantly.
    //
    // Consent can be given two ways, and both mean "a human decided this again":
    //   * operator  release the stick, hold it centred for `dwellMs`, command DOWN again
    //   * host      pinchAck(), for an automated lowering
    // Either way it is spent when the descent stops, so every resumed descent inside the
    // zone is its own decision, and it expires unused after kConsentMs.
    //
    // THE GUARD NEEDS A HEIGHT. With no ToF (height_ == nullptr, or a sensor that has
    // never read) there is no zone to detect and descent is unguarded -- pinchActive()
    // reports that, and it is published in telemetry so "the guard is off" is visible
    // rather than assumed.
    void setPinch(float mm, uint32_t dwellMs) { pinchMm_ = mm; dwellMs_ = dwellMs; }
    // The threshold is COMMISSIONED per robot (it depends on the lift's stopping distance and
    // the sensor's mounting), so it has to survive a reboot. A power cycle silently reverting
    // it to the compiled default would weaken the guard with nothing to show for it.
    void loadCfg();
    void saveCfg();
    float    pinchMm() const { return pinchMm_; }
    uint32_t pinchDwellMs() const { return dwellMs_; }
    bool     pinchActive() const { return haveHeight_; }   // is the guard able to work at all?
    bool     pinchHold() const { return pinchHold_; }      // descent stopped, awaiting consent
    bool     pinchConsent() const { return consent_; }
    void     pinchAck(uint32_t nowMs) { consent_ = true; consentMs_ = nowMs; pinchHold_ = false; }

    // Press duration and release gap are TUNABLE because both are properties of the
    // controller we have not measured. Defaults are deliberately conservative; use
    // `MAST PULSE <ms> [gap]` to find where the controller actually stops registering,
    // then set them from data rather than leaving guesses in the source.
    void setTiming(uint32_t pulseMs, uint32_t gapMs) { pulseMs_ = pulseMs; minGapMs_ = gapMs; }
    uint32_t pulseMs() const { return pulseMs_; }
    uint32_t gapMs()   const { return minGapMs_; }

    static constexpr uint32_t kPulseMs      = 250;   // default momentary "button press"
    // Minimum release time between presses. The controller debounces its buttons, so a
    // second press arriving too soon after the first is swallowed -- which showed up as
    // the stop pulse being ignored when the deadman was released shortly after starting a
    // move, leaving the lift travelling. A finger always leaves a gap; the code did not.
    // Deferring is safe because intent is re-asserted every tick: the pulse is delayed,
    // never dropped. (Diagnosed by Raj.)
    static constexpr uint32_t kMinGapMs     = 300;
    static constexpr float    kDeadzone     = 0.25f; // stick fraction before we act
    // Stall window. This has to be long enough that GENUINE motion clears the sensor noise
    // over it, and that sets the floor: measured on opendubs2 the lift runs at 8-20 mm/s, so
    // at its slowest it covers only ~7 mm in 900 ms -- inside the +-4 mm noise band, and the
    // old 900 ms window duly declared a moving lift stalled halfway down. Two seconds gives
    // 16 mm at the slow end, comfortably clear. The cost is that a real limit takes 2 s to
    // notice, which is fine: this is belief-keeping, not the safety path. The pinch backstop
    // does not depend on it.
    static constexpr uint32_t kStallCheckMs = 2000;
    static constexpr float    kMovedMm      = 8.0f;  // less than this over the window = not moving
    // START-UP GRACE. Measured on opendubs2: the controller takes about a second between
    // the button press and the platform visibly moving. The stall check used to start at
    // the pulse, so its first window landed entirely inside that dead time, saw ~1 mm of
    // travel, and declared a limit that was not there -- which set the belief to STOPPED
    // just as the lift got going, and then refused the re-command that would have fixed it.
    // Nothing is judged until the mechanism has had time to respond.
    static constexpr uint32_t kStartGraceMs = 2000;

    // BACKSTOP. The guard does not trust the belief. A pulse-toggle controller can be
    // running while we think it is stopped -- a missed pulse, a bench command that bypassed
    // us, a false stall -- and that is exactly the case where stopping matters most. So in
    // the zone without consent, a platform that is still measurably FALLING gets a stop
    // press, whatever we believe. Falling is required: pressing DOWN on a controller that
    // is genuinely stopped would START a descent, so evidence of motion is the precondition,
    // never an assumption.
    static constexpr float    kFallMm       = 10.0f;  // fall this far = definitely moving (noise is +-4)
    static constexpr uint32_t kSettleMs     = 900;    // after a press, let it coast before judging again
    // Zone hysteresis. Parked just inside the threshold, sensor noise (+-4 mm) crossed it
    // repeatedly and the hold indication flickered. Enter at pinchMm, leave 10 mm above it.
    static constexpr float    kZoneHystMm   = 10.0f;
    // How long a refusal keeps announcing itself once the demand stops. The red strobe means
    // "I am refusing a descent RIGHT NOW" -- latching it for as long as the lift happens to
    // sit low turns a safety signal into wallpaper, which is worse than no signal.
    static constexpr uint32_t kHoldShowMs   = 2000;

    // Pinch-guard defaults. The threshold is NOT just a finger: it has to include how far
    // the lift still travels after we press stop, which is a pulse round-trip plus the
    // controller's own ramp-down. Commission it on the robot with `MAST PINCH <mm>` --
    // lower into the zone with a block of known thickness under the platform and confirm
    // it stops with the block loose.
    static constexpr float    kPinchMm      = 40.0f;   // ~20 mm finger + stopping distance
    static constexpr uint32_t kPinchDwellMs = 600;     // stick centred this long = "released"
    static constexpr uint32_t kConsentMs    = 15000;   // an unused ack goes stale

private:
    void applyRelays_(bool upOn, bool downOn);
    void startPulse_(bool up, uint32_t nowMs);

    const uint8_t relayUp_, relayDown_;
    HeightFn      height_;

    MastMotion motion_    = MastMotion::STOP;
    MastMotion intent_    = MastMotion::STOP;
    uint32_t   pulseEnd_  = 0;
    uint32_t   pulseDone_ = 0;   // when the last pulse released
    uint32_t   pulseMs_   = kPulseMs;
    uint32_t   minGapMs_  = kMinGapMs;
    bool       pulseUp_   = false;
    bool       pulsing_   = false;

    bool  haveHeight_ = false;
    float heightMm_   = -1.0f;
    float refHeight_  = 0.0f;      // height when the current motion began
    uint32_t refMs_   = 0;
    bool  atUpper_ = false, atLower_ = false;

    float    pinchMm_       = kPinchMm;
    uint32_t dwellMs_       = kPinchDwellMs;
    bool     pinchHold_     = false;   // in the zone, descent refused
    bool     consent_       = false;   // a human has agreed to this descent
    bool     consentUsed_   = false;   // ... and it has since been acted on
    uint32_t consentMs_     = 0;
    bool     releasing_     = false;   // stick currently not commanding DOWN
    uint32_t releaseStart_  = 0;
    uint32_t releaseHeldMs_ = 0;       // how long the last release lasted
    uint32_t stallArmMs_    = 0;       // no stall judgement before this
    uint32_t refuseMs_      = 0;       // when the guard last refused a descent
    bool     inZone_        = false;   // hysteretic: see kZoneHystMm
    float    guardMark_     = -1.0f;   // height when the guard last looked; <0 = not in zone
    uint32_t guardSettle_   = 0;       // ... and when it may look again
};
