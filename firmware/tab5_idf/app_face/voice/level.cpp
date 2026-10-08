#include "level.h"
#include <math.h>

namespace {

// Carried over from bench_mic/main.cpp, where they were arrived at interactively with someone
// speaking from each side. NAMED PLACEHOLDERS in the sense pantilt.h uses the term: they are a
// real measurement from a real bench, but that bench was a quiet room and this robot is not one.
// Re-check them with the motors running; do not re-invent them from scratch.
constexpr float kLeftRatio  = 1.15f;   // (l+1)/(r+1) above this reads LEFT
constexpr float kRightRatio = 0.87f;   // and below this, RIGHT

// How far above the floor a block must sit to count as signal at all. 1.6x is the bench's figure.
constexpr float kFloorMargin = 1.6f;

// Until the floor has been measured, demand a level no ordinary room will produce. An un-measured
// threshold that fails OPEN would have the robot react to its own fans on the first block after
// boot, which is worse than a second of deafness.
constexpr float kUnmeasuredThreshold = 4000.0f;

// AN ABSOLUTE FLOOR UNDER THE FLOOR, and it is not a nicety.
//
// Without it a silent input is catastrophic rather than merely useless: all-zero samples give a
// measured floor of 0, which gives a threshold of 0, and `level >= 0` is TRUE FOR EVERY BLOCK -- so
// a DEAD MICROPHONE reads as continuous speech. The robot would announce `Heard` at the first press
// and end an utterance never.
//
// Found by compiling the shim for the desktop twin, where the HAL's audioRecord() is an empty stub
// that leaves the buffer zeroed -- which is exactly what a failed i2s_read looks like on the
// device. A silent channel must read as SILENCE, which means the threshold can never be allowed to
// collapse to zero.
//
// 40 RMS on the int16 full scale is about -58 dBFS: below any room this robot will ever stand in,
// and far below speech, so it costs nothing in a real bay and turns a dead mic into silence.
constexpr float kMinThreshold = 40.0f;

// Blocks of quiet needed before the floor is worth believing. At the 32 ms block the bench used,
// 24 blocks is about three quarters of a second.
constexpr int kFloorWarmup = 24;

// The asymmetry, as per-block coefficients. Rise is slow, fall is fast; see level.h.
constexpr float kRise = 0.02f;
constexpr float kFall = 0.25f;

float track(float cur, float obs)
{
    const float a = (obs > cur) ? kRise : kFall;
    return cur + a * (obs - cur);
}

} // namespace

VoiceLevel voiceLevel(const int16_t* samples, int frames)
{
    VoiceLevel out{0.0f, 0.0f};
    if (!samples || frames <= 0) return out;

    // Accumulate in double. At 48 kHz a one-second block is 48000 squares of up to 2^30, which
    // overflows a 32-bit int and loses the top bits of a float32 mantissa long before that. The
    // bench used double here for the same reason.
    double a = 0.0, b = 0.0;
    for (int i = 0; i < frames; ++i) {
        // STRIDE 4, OFFSETS 0 AND 2. See level.h: the HAL hands back
        // [MIC-L, AEC, MIC-R, MIC-HP] and the AEC and headset slots are skipped. Reading this as
        // stride-2 stereo -- which is what the Arduino bench recorded -- would silently mix the
        // dead AEC slot into the right channel and bias every direction verdict leftward.
        const double x = samples[kVoiceChans * i + kVoiceMicL];
        const double y = samples[kVoiceChans * i + kVoiceMicR];
        a += x * x;
        b += y * y;
    }
    out.l = (float)sqrt(a / frames);
    out.r = (float)sqrt(b / frames);
    return out;
}

void voiceFloorReset(VoiceFloor* f)
{
    if (!f) return;
    f->l = 0.0f;
    f->r = 0.0f;
    f->valid = false;
    f->observed = 0;
}

void voiceFloorUpdate(VoiceFloor* f, const VoiceLevel& v)
{
    if (!f) return;
    if (f->observed == 0) {
        // Seed from the first block rather than converging up from zero, which would spend the
        // whole warm-up below the real floor and call the room signal.
        f->l = v.l;
        f->r = v.r;
    } else {
        f->l = track(f->l, v.l);
        f->r = track(f->r, v.r);
    }
    if (f->observed < kFloorWarmup) {
        ++f->observed;
        f->valid = (f->observed >= kFloorWarmup);
    }
}

float voiceFloorThreshold(const VoiceFloor& f)
{
    if (!f.valid) return kUnmeasuredThreshold;
    const float measured = (f.l + f.r) * 0.5f * kFloorMargin;
    // Never below kMinThreshold: see its comment. A dead microphone must read as silence.
    return measured > kMinThreshold ? measured : kMinThreshold;
}

const char* voiceDirName(VoiceDir d)
{
    switch (d) {
        case VoiceDir::Left:   return "LEFT";
        case VoiceDir::Right:  return "RIGHT";
        case VoiceDir::Centre: return "CENTRE";
        default:               return "NONE";
    }
}

VoiceDir voiceDirection(const VoiceLevel& v, const VoiceFloor& f)
{
    // THE FLOOR GATE COMES FIRST and it is not an optimisation. Two near-zero channels have a
    // ratio too, and it is noise -- reporting it as a direction is precisely the error that made
    // the first mic result worthless.
    if (v.mean() < voiceFloorThreshold(f)) return VoiceDir::None;

    // +1 on each side so a silent channel cannot divide by zero, and so the ratio of two tiny
    // numbers stays near 1 instead of exploding.
    const float ratio = (v.l + 1.0f) / (v.r + 1.0f);
    if (ratio > kLeftRatio)  return VoiceDir::Left;
    if (ratio < kRightRatio) return VoiceDir::Right;
    return VoiceDir::Centre;
}
