#include "pantilt_core.h"

namespace neck {
namespace {

float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

}  // namespace

Limits limitsFrom(bool haveMin, long minDeg, bool haveMax, long maxDeg)
{
    Limits l;                                   // starts at the narrow fallback
    // NOT 0..180, and the fallback is deliberately TIGHT. The Arduino firmware clamped tilt to
    // limits it DISCOVERED with a stall-current sweep, so they are a property of the individual
    // machine. An uncommissioned robot therefore gets a narrow band around centre: being wrong in
    // the narrow direction costs a small range, being wrong in the wide direction drives the head
    // into the frame.
    if (!haveMin || !haveMax || minDeg >= maxDeg) return l;
    l.tiltMin = (float)minDeg;
    l.tiltMax = (float)maxDeg;
    l.known   = true;
    return l;
}

bool tick(State& s, const In& in, const Limits& lim)
{
    if (in.nowMs - s.lastMs < kPeriodMs) return false;
    const float dt = (float)(in.nowMs - s.lastMs) / 1000.0f;
    s.lastMs = in.nowMs;

    // FROZEN, NOT CENTRED. Returning to centre would itself be autonomous motion, and the state in
    // which we are told not to move is exactly the state in which we should not decide to move
    // somewhere nicer. With NO LINK we do not know the robot's state, and the safe reading of
    // "unknown" is "do not move".
    if (!in.motionAllowed) { s.owner = Owner::None; s.moving = false; return true; }

    // ---- ARBITRATION, highest first: touch, then RC, then a show, then the tracker -------------
    // Touch above RC because the person at the glass is aiming the screen at themselves.

    // Touch and RC use an UNSIGNED age, and that is carried over deliberately rather than copied
    // carelessly. It was wrong for GAZE and is tolerable here for two reasons the original states:
    // they integrate a RATE per tick, so losing one frame's ownership costs a fraction of a degree
    // rather than a destination; and neither zeroes its timestamp on expiry, so a stray frame
    // cannot destroy the lease.
    const bool touchOwns = (in.touchMs != 0 && (in.nowMs - in.touchMs) < kTouchHoldMs);
    if (touchOwns) {
        s.pan  = clampf(s.pan  + in.touchPan  * dt, kRcPanMin, kRcPanMax);
        s.tilt = clampf(s.tilt + in.touchTilt * dt, lim.tiltMin, lim.tiltMax);
        s.panTgt = s.pan; s.tiltTgt = s.tilt;
        s.owner = Owner::Touch; s.moving = (in.touchPan != 0 || in.touchTilt != 0);
        return true;
    }

    // RC: a human on a stick outranks the tracker. A lease, not a latch -- letting go of the stick
    // hands the head back. No slew on top of a rate the operator is already shaping.
    if (in.rcMs != 0 && (in.nowMs - in.rcMs) < kRcHoldMs) {
        s.pan  = clampf(s.pan  + in.rcPan  * dt, kRcPanMin, kRcPanMax);
        s.tilt = clampf(s.tilt + in.rcTilt * dt, lim.tiltMin, lim.tiltMax);
        s.panTgt = s.pan; s.tiltTgt = s.tilt;
        s.owner = Owner::Rc; s.moving = (in.rcPan != 0 || in.rcTilt != 0);
        return true;
    }

    // A SHOW. SIGNED AGE, NOT UNSIGNED -- this is the bug that was live on the robot and that no
    // twin could show. `nowMs` is captured once at the top of a frame, so a GAZE arriving midway
    // through stamps a LATER time than the frame it is judged against: `nowMs - gazeMs` is a small
    // NEGATIVE number, which as unsigned is about 4.29 billion and is not < the lease. The freshest
    // possible command read as the most expired one. Measured before and after: the head sat at 90
    // with the owner flapping, then moved 90->150 in 0.82 s.
    const int32_t gazeAge = (int32_t)(in.nowMs - in.gazeMs);
    const uint32_t lease  = in.gazeLease > kGazeMaxLeaseMs ? kGazeMaxLeaseMs : in.gazeLease;
    const bool gazeOwns   = (in.gazeMs != 0 && gazeAge >= 0 && gazeAge < (int32_t)lease);

    bool autonomous = false;
    if (gazeOwns) {
        // A show says WHERE to look; the slew below decides how fast the head may get there, and
        // that stays the firmware's decision rather than a caller's.
        s.panTgt  = clampf(in.gazePan,  kRcPanMin, kRcPanMax);
        s.tiltTgt = clampf(in.gazeTilt, lim.tiltMin, lim.tiltMax);
        s.owner   = Owner::Gaze;
    } else if (in.haveTarget && in.confidence >= kHeadConfidence) {
        // CONFIDENCE GATES THE HEAD, not just the amplitude: a head turn is a commitment, and
        // committing to a false positive is what makes a robot look broken. A weak detection costs
        // a twitch of the eyes and never a head swing.
        //
        // EYES LEAD, HEAD FOLLOWS: an offset inside kEyeOnly is handled by the eyes alone.
        const float ax = in.nx < 0 ? -in.nx : in.nx;
        const float ay = in.ny < 0 ? -in.ny : in.ny;
        if (ax > kEyeOnly || ay > kEyeOnly) {
            s.panTgt  = clampf(kCentreDeg + in.nx * kPanRangeDeg,  kCentreDeg - kPanRangeDeg,  kCentreDeg + kPanRangeDeg);
            s.tiltTgt = clampf(kCentreDeg + in.ny * kTiltRangeDeg, lim.tiltMin, lim.tiltMax);
            s.owner = Owner::Track;
            autonomous = true;
        } else {
            s.owner = Owner::None;
        }
    } else {
        s.owner = Owner::None;
    }

    // THE ENVELOPE CLAMP KEYS OFF THE OWNER, NOT off "is the tracker running". Keying it off the
    // tracker squeezed a SHOW's targets into the autonomous envelope -- silently, since a show that
    // asks for 150 and gets 120 still looks like it worked.
    if (autonomous) {
        s.panTgt  = clampf(s.panTgt,  kCentreDeg - kPanRangeDeg,  kCentreDeg + kPanRangeDeg);
        s.tiltTgt = clampf(s.tiltTgt, kCentreDeg - kTiltRangeDeg, kCentreDeg + kTiltRangeDeg);
    }
    // Whoever set them, the hard bounds are enforced here and not trusted to a caller.
    s.panTgt  = clampf(s.panTgt,  kRcPanMin, kRcPanMax);
    s.tiltTgt = clampf(s.tiltTgt, lim.tiltMin, lim.tiltMax);

    // ---- the shared slew ------------------------------------------------------------------------
    // A deadband first: servo jitter at rest is worse than no motion at all.
    const float dp = s.panTgt - s.pan, dtl = s.tiltTgt - s.tilt;
    const float adp = dp < 0 ? -dp : dp, adt = dtl < 0 ? -dtl : dtl;
    const float step = kSlewDps * dt;
    s.moving = false;
    if (adp > kDeadbandDeg) { s.pan  += (dp  > 0 ? 1.0f : -1.0f) * (adp < step ? adp : step); s.moving = true; }
    if (adt > kDeadbandDeg) { s.tilt += (dtl > 0 ? 1.0f : -1.0f) * (adt < step ? adt : step); s.moving = true; }
    s.pan  = clampf(s.pan,  kRcPanMin, kRcPanMax);
    s.tilt = clampf(s.tilt, lim.tiltMin, lim.tiltMax);
    return true;
}

}  // namespace neck
