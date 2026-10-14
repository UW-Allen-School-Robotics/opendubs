// THE NECK'S CONTROL LAW, as pure compute: no I2C, no NVS, no FreeRTOS, no Arduino. The identical
// translation unit compiles for the Dial and for a laptop, which is the pattern the Tab5's
// `percept/` and `voice/` already use and the reason their arithmetic could be checked without the
// board. That matters more here than there: this is being written with the Dial unplugged, and
// "it compiles" is not a result.
//
// PORTED FROM firmware/tab5_idf/app_face/pantilt.cpp, deliberately keeping its constants and the
// reasons for them. Everything below that looks like a judgement call was one, made by someone
// watching a real head move, and re-deriving it from scratch would throw that away:
//
//   * BOUNDS ARE ENFORCED HERE, not left as a convention in a caller.
//   * SLEW IS LIMITED HERE, in firmware, not in the command stream -- a target jump must not
//     become a fast head movement near someone standing within reach.
//   * FROZEN, NOT CENTRED, when motion is not allowed. Returning to centre is itself autonomous
//     motion, and the state in which we are told not to move is exactly the state in which we
//     should not decide to move somewhere nicer.
//   * TILT LIMITS ARE PER-ROBOT and come from NVS. An UNCOMMISSIONED robot gets a deliberately
//     NARROW band around centre, not the full sweep -- the wrong way round would let an unknown
//     machine drive its head into its own frame.
//
// What this file does NOT do, on purpose: talk to the 8Servo, read NVS, or know what a degree
// becomes in microseconds. The caller does that, so this stays testable.

#pragma once
#include <cstdint>

namespace neck {

// ---- the constants, carried over with their reasons ------------------------------------------
constexpr float kCentreDeg      = 90.0f;
constexpr float kPanRangeDeg    = 30.0f;   // hard bound, degrees either side of centre
constexpr float kTiltRangeDeg   = 18.0f;   // smaller: the head has less room vertically
constexpr float kRcPanMin       = 0.0f;    // pan was never homed, so full travel is fine for a human
constexpr float kRcPanMax       = 180.0f;
constexpr float kSlewDps        = 70.0f;   // raised from 25 so a show can nod on the beat
constexpr float kDeadbandDeg    = 1.5f;    // servo jitter at rest is worse than no motion
constexpr uint32_t kPeriodMs    = 20;      // 50 Hz, matching the Arduino control task
constexpr uint32_t kRcHoldMs    = 600;     // RC's lease, matching the Arduino's kManualHoldMs
constexpr uint32_t kTouchHoldMs = 600;     // the same lease for on-face touch
constexpr uint32_t kGazeHoldMs    = 600;   // a show's default lease
constexpr uint32_t kGazeMaxLeaseMs = 1000; // capped, so a sender that dies cannot hold the head
constexpr float kEyeOnly        = 0.25f;   // offset below which the head never moves
constexpr float kHeadConfidence = 0.60f;   // below this a detection costs eyes only, never a turn
constexpr float kTiltFallbackHalfBand = 10.0f;   // the uncommissioned band, +/- around centre

enum class Owner : uint8_t { None, Touch, Rc, Gaze, Track };

struct Limits {
    float tiltMin = kCentreDeg - kTiltFallbackHalfBand;
    float tiltMax = kCentreDeg + kTiltFallbackHalfBand;
    bool  known   = false;        // false = the narrow fallback, not a commissioned robot
};

// Everything a tick needs to know. The caller fills it; nothing here reads a clock or a device.
struct In {
    uint32_t nowMs = 0;
    bool motionAllowed = false;   // NO LINK means unknown, and unknown means do not move
    // on-face touch (a rate, deg/s), highest priority
    float touchPan = 0, touchTilt = 0; uint32_t touchMs = 0;
    // RC sticks (a rate, deg/s)
    float rcPan = 0, rcTilt = 0;   uint32_t rcMs = 0;
    // a show's GAZE (an absolute target, degrees) with its own lease
    float gazePan = kCentreDeg, gazeTilt = kCentreDeg;
    uint32_t gazeMs = 0, gazeLease = kGazeHoldMs;
    // the local tracker: a normalised offset and a confidence
    bool haveTarget = false; float nx = 0, ny = 0, confidence = 0;
};

struct State {
    float pan = kCentreDeg, tilt = kCentreDeg;        // the commanded angle
    float panTgt = kCentreDeg, tiltTgt = kCentreDeg;  // where it is heading
    Owner owner = Owner::None;
    bool moving = false;
    uint32_t lastMs = 0;
};

// Apply NVS-read limits. `known == false` keeps the narrow fallback: a robot whose tilt range was
// never measured must not be given the full sweep.
Limits limitsFrom(bool haveMin, long minDeg, bool haveMax, long maxDeg);

// One control step. Returns false if it was too soon (the 50 Hz gate) and nothing changed.
bool tick(State& s, const In& in, const Limits& lim);

}  // namespace neck
