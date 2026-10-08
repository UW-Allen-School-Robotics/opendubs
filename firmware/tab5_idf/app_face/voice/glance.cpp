#include "glance.h"
#include "../shared/due.h"
#include <stdio.h>

namespace {

// ---- the tunables ---------------------------------------------------------------------------
// NAMED PLACEHOLDERS in pantilt.h's sense: defensible numbers that can only be judged by someone
// standing in front of the robot watching it turn. What each one costs if it is wrong is written
// down, because that is the part a later reader cannot reconstruct.

// HOW LONG THE HEAD HOLDS THE GLANCE. Too short and the move is a twitch nobody reads as
// intentional; too long and it stops being a glance and becomes a stare, which is the thing the
// design note rules out. The literature's "brief, repeated fixations" sit around this mark.
constexpr uint32_t kHoldMs = 900;

// THE GAP BEFORE ANOTHER GLANCE MAY START, measured from the end of the last one. These two
// constants are the whole thing standing between "multiple short fixations", which the design
// wants, and continuous aiming, which it forbids.
//
// TWO VALUES, BECAUSE A RE-GLANCE IS ONLY WORTH IT WHEN SOMETHING CHANGED. The first version of
// this had a single 600 ms gap, and the duty-cycle case caught what that actually means: a person
// talking continuously from one spot had the head steering 60% of the time, swinging over and
// coming back and swinging over again, all to re-aim at the place it was already aiming. That is
// not a fixation, it is a tic -- and it is how "repeated glances" becomes a tracker by accident.
//
// So: after glancing at a bearing, SETTLE. Only a DIFFERENT bearing -- the speaker moved, or
// somebody else spoke -- is new information worth another move, and that one is allowed quickly.
// Static speaker: one glance, then stillness. Speaker crosses the room: the head follows in
// discrete steps. Which is the behaviour the design note describes.
constexpr uint32_t kSettleMs = 2500;   // same bearing: stay put
constexpr uint32_t kChangeMs = 600;    // a new bearing: react, but not instantly

// HOW FAR TO LOOK, in percept's normalised -1..1 offset. Not 1.0: the microphones resolve a SIDE,
// not an angle, so a full-scale offset would assert a precision we do not have. pantilt applies
// its own slew limit and bounds on top, so this is a request and not a command.
constexpr float kOffset = 0.55f;

// SIGN CONVENTION, AND IT IS UNVERIFIED ON HARDWARE.
//
// percept's nx is "normalised target offset, -1..1" (pantilt.h) and this assumes POSITIVE IS
// RIGHT, matching an image x that increases rightward. Whether that survives this lens and this
// camera's mounting is a bench observation -- pantilt.h already says its own thresholds "can only
// be judged by someone standing in front of the robot watching it turn", and the sign is the same
// kind of fact. It is one constant so that a bench pass can flip it in one place.
//
// If the head glances the WRONG WAY on the robot, this is the line. Do not chase it through the
// arithmetic.
constexpr float kRightIsPositive = +1.0f;

struct State {
    bool     active;
    uint32_t until;      // when the current glance ends
    uint32_t endedAt;    // when the last glance finished; the gap is measured from here
    float    nx;
    VoiceDir dir;
    uint32_t started;    // counters, for the status line
    uint32_t refused;
};

State g;

} // namespace

void glanceBegin()
{
    g = State{};
    g.dir = VoiceDir::None;
}

bool glanceTrigger(uint32_t nowMs, VoiceDir d)
{
    // NOTHING TO GLANCE AT. A bearing below the floor is not a bearing -- level.cpp's whole
    // discipline is that "no stimulus" never reads as a direction, and it would be undone here by
    // treating None as a side.
    if (d == VoiceDir::None) { ++g.refused; return false; }

    // ALREADY FACING IT. The head does not move for a centre bearing: the earcon is the entire
    // acknowledgement, and turning toward something you already face reads as a twitch rather than
    // as attention. This is a REFUSAL, not a glance to the middle.
    if (d == VoiceDir::Centre) { ++g.refused; return false; }

    // One at a time.
    if (g.active) { ++g.refused; return false; }

    // Then: has anything CHANGED? A bearing we have just glanced at earns the long settle; a
    // different one earns the short gap, because it is actual new information.
    const uint32_t gap = (d == g.dir) ? kSettleMs : kChangeMs;
    if (!due(nowMs, g.endedAt + gap)) { ++g.refused; return false; }

    g.dir    = d;
    g.nx     = (d == VoiceDir::Right ? +kOffset : -kOffset) * kRightIsPositive;
    g.active = true;
    g.until  = nowMs + kHoldMs;
    ++g.started;
    return true;
}

bool glanceActive(uint32_t nowMs)
{
    // IT ENDS BY ITSELF AND NEVER EXTENDS. That is the whole difference between a glance and a
    // tracker: no caller can hold it open, and forgetting to call this cannot leave the head
    // committed, because the deadline is absolute rather than refreshed.
    if (g.active && due(nowMs, g.until)) {
        g.active  = false;
        g.endedAt = nowMs;
    }
    return g.active;
}

float glanceNx() { return g.nx; }

// Always level. The two microphones sit side by side, so there is no vertical information in the
// signal at all, and a non-zero tilt here would be an invented measurement.
float glanceNy() { return 0.0f; }

// DELIBERATELY MODEST, because confidence GATES THE HEAD in pantilt.cpp and not just the
// amplitude: "a weak detection costs a twitch of the eyes and never a head swing". A left/right
// bearing from a level ratio is exactly that -- real, and much weaker evidence than a face. Set
// below pantilt's head-commit threshold and the glance would never turn the head at all; set at
// 1.0 and a noisy ratio would swing it like a confirmed face. This sits in between, and it is the
// number a bench pass is most likely to want to move.
float glanceConfidence() { return 0.6f; }

const char* glanceStatus()
{
    static char buf[128];
    snprintf(buf, sizeof(buf), "glance %s dir=%s nx=%+.2f started=%lu refused=%lu",
             g.active ? "ACTIVE" : "idle", voiceDirName(g.dir), (double)g.nx,
             (unsigned long)g.started, (unsigned long)g.refused);
    return buf;
}
