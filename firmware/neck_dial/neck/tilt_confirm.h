// A TILT SENT OVER THE NETWORK WAITS FOR SOMEBODY TO PRESS THE BUTTON.
//
// The tilt band is the fence: every target the control law produces is clamped to it, so widening
// it is the one remote command that lets the head reach somewhere it could not reach before. A
// remote TILT moves no servo by itself -- but a widened band followed by a GAZE is two remote
// commands, where the first quietly removes the limit the second would have hit. The maintainers
// chose to require a physical confirmation, and this is it.
//
// WHAT IT BUYS, precisely: commissioning now requires a person at the head. That is where you
// should be when you do it, since the whole procedure is "move the head a little, look at it,
// decide" -- and the one it protects against is a band widened from across the building, or by a
// script, or by a stale command replayed later.
//
// SERIAL IS NOT GATED. A serial TILT already means physical access to the board, so a button would
// ask the same person to prove the same thing twice.
//
// PURE COMPUTE, no GPIO and no clock, for the reason pantilt_core is: this is a safety interlock,
// it has to be right, and the board it runs on has no USB cable and no console. The identical
// translation unit is exercised on a laptop by tools/pantilt_host_test.cpp -- including the cases
// that are awkward to stage by hand, like a button already held down when the request arrives.

#pragma once
#include <cstdint>

namespace neck {

// How long the robot waits for the press. Long enough to walk round the robot, short enough that a
// forgotten request cannot be confirmed by somebody touching the Dial an hour later for an
// unrelated reason.
constexpr uint32_t kTiltConfirmMs = 15000;

enum class Confirm : uint8_t {
    None,        // nothing happened this tick
    Applied,     // the button was pressed in time: the caller writes NVS and the limits
    Expired,     // the window closed with no press: the request is dropped
};

struct Pending {
    bool     waiting = false;
    int      lo = 0, hi = 0;
    uint32_t deadline = 0;
    // THE BUTTON MUST BE RELEASED AND THEN PRESSED, and this is what makes that checkable. A
    // button already held when the request arrives must not confirm it: otherwise anything resting
    // on the Dial -- a hand, a cable, a box on the bench -- turns the interlock into a no-op, and
    // it would pass every test written with a politely-timed press.
    bool     sawRelease = false;
};

// Validate and arm. Returns false, and arms nothing, if the band is not sane -- refused rather
// than clamped, because an out-of-range request is a bug upstream and clamping hides it.
// `buttonDown` is the button's state RIGHT NOW, so a request made with the button already held
// starts out needing a release first.
bool request(Pending& p, int lo, int hi, uint32_t nowMs, bool buttonDown);

// Call every loop. Returns Applied on the confirming press, Expired when the window closes.
Confirm poll(Pending& p, uint32_t nowMs, bool buttonDown);

// Seconds left, for the reply and for NECK. 0 when nothing is waiting.
uint32_t secondsLeft(const Pending& p, uint32_t nowMs);

}  // namespace neck
