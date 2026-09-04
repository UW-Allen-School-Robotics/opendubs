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

    static constexpr uint32_t kPulseMs      = 250;   // momentary "button press"
    static constexpr float    kDeadzone     = 0.25f; // stick fraction before we act
    static constexpr uint32_t kStallCheckMs = 900;   // no height change in this while
    static constexpr float    kMovedMm      = 8.0f;  // ... this much = not actually moving

private:
    void setRelay_(uint8_t ch, bool on);
    void startPulse_(bool up, uint32_t nowMs);

    const uint8_t relayUp_, relayDown_;
    HeightFn      height_;

    MastMotion motion_    = MastMotion::STOP;
    MastMotion intent_    = MastMotion::STOP;
    uint32_t   pulseEnd_  = 0;
    bool       pulseUp_   = false;
    bool       pulsing_   = false;

    bool  haveHeight_ = false;
    float heightMm_   = -1.0f;
    float refHeight_  = 0.0f;      // height when the current motion began
    uint32_t refMs_   = 0;
    bool  atUpper_ = false, atLower_ = false;
};
