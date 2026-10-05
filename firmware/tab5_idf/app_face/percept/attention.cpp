#include "attention.h"
#include "../shared/clock.h"
#include "../shared/due.h"
#include "../shared/pub.h"
#include <math.h>

namespace {

// ---- grading. EVERY NUMBER HERE IS A DESIGN CHOICE, NOT A MEASUREMENT ----------------------
// They are reasoned from what each cue can and cannot know, and none has been seen against a real
// person. notes/design/tab5-face-architecture.md records the bench observation that replaces each.

// FACE. A hit is worth more when the detector is sure AND when it keeps happening in the same
// place. `score` below kScoreFloor contributes nothing extra; at kScoreFull it contributes all of
// its share. The repeat term is the half that matters most: a single frame's hit at a high score is
// still a single frame, and the model's own false positives do not usually recur in one spot.
constexpr float kScoreFloor = 0.30f;
constexpr float kScoreFull = 0.90f;
constexpr int kRepsFull = 3;            // three consecutive linked hits is "really there"
constexpr float kFaceConfBase = 0.15f;  // a lone weak hit: a flicker of a glance, nothing more
constexpr float kFaceConfScore = 0.55f;
constexpr float kFaceConfReps = 0.30f;

// Two hits are the same face if they arrive close in time and close in space. 700 ms is longer than
// the 500 ms slow-gate period so consecutive gated runs can link; 0.25 normalised is about 40 cm at
// 1 m, i.e. a person shifting their weight stays the same person.
constexpr uint32_t kFaceLinkMs = 700;
constexpr float kFaceLinkDist = 0.25f;

// MOTION. Strength is the smoothed fraction of the hottest cell that is changing. A person crossing
// a 20x20 px cell changes most of it; a curtain or a reflection changes a little of it. The cap at
// kMotionConfCap is deliberate: motion must never be able to claim the confidence of a face, so
// that even when the arbiter has only motion to go on the face commits less of its gaze to it.
constexpr float kMotionStrFloor = 0.06f;
constexpr float kMotionStrFull = 0.40f;
constexpr float kMotionConfCap = 0.70f;

// DWELL IS THE SAME NUMBER AS BELIEF. "A weak hit is a brief low-amplitude glance; a strong,
// repeated hit earns a sustained look" is one graded quantity, not two: confidence scales how far
// the eyes go (the face's job, see app_face.cpp) and how long the target stays live (here). A fix
// that has gone stale is the signal for the face to resume wandering, so a weak fix simply expires
// quickly and the eyes drift off it, which is what a glance looks like.
constexpr uint32_t kFaceDwellMin = 500, kFaceDwellMax = 2400;
constexpr uint32_t kMotionDwellMin = 350, kMotionDwellMax = 1200;

// How far a target must move before it counts as a NEW target rather than the same one drifting.
// Below this the seq is held, so the face does not fire a fresh saccade every frame at a centroid
// that is jittering by a pixel -- which would read as a malfunction rather than as attention.
// 0.10 normalised is 16 px at 320 wide, i.e. most of one cell.
constexpr float kReTarget = 0.10f;

struct Cue {
    float nx, ny;
    float quality;      // score for a face, strength for motion
    uint32_t stampMs;
    int reps;
    bool live;
};

Cue g_face = {};
Cue g_motion = {};
Published<GazeIntent> g_pub;
GazeIntent g_cur = {};
uint32_t g_seq = 0;

inline float clamp01(float v)
{
    return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v);
}

inline float ramp(float v, float lo, float hi)
{
    // Guarded divisor: the band's expf divisor was one added activity away from a NaN propagating
    // into a style value, and this one feeds a gaze amplitude.
    if (hi - lo < 1e-6f) return v >= hi ? 1.0f : 0.0f;
    return clamp01((v - lo) / (hi - lo));
}

float faceConfidence(const Cue& c)
{
    const float s = ramp(c.quality, kScoreFloor, kScoreFull);
    const int reps = c.reps > kRepsFull ? kRepsFull : c.reps;
    const float r = (float)reps / (float)kRepsFull;
    return clamp01(kFaceConfBase + kFaceConfScore * s + kFaceConfReps * r);
}

float motionConfidence(const Cue& c)
{
    return clamp01(ramp(c.quality, kMotionStrFloor, kMotionStrFull) * kMotionConfCap);
}

uint32_t dwellFor(uint8_t kind, float conf)
{
    if (kind == GAZE_FACE)
        return kFaceDwellMin + (uint32_t)((float)(kFaceDwellMax - kFaceDwellMin) * conf);
    // Motion's confidence is capped below 1, so normalise within its own range or a strong motion
    // target would never reach its own maximum dwell.
    const float t = clamp01(conf / kMotionConfCap);
    return kMotionDwellMin + (uint32_t)((float)(kMotionDwellMax - kMotionDwellMin) * t);
}

}  // namespace

void attentionReset()
{
    g_face = Cue{};
    g_motion = Cue{};
    g_cur = GazeIntent{};
    // g_seq is NOT reset. The face compares seq to the last one it acted on; restarting the counter
    // could reproduce a value it has already seen and swallow a real retarget.
}

void attentionNoteMotion(uint32_t nowMs, float nx, float ny, float strength)
{
    g_motion.nx = nx;
    g_motion.ny = ny;
    g_motion.quality = strength;
    g_motion.stampMs = nowMs;
    g_motion.reps = 1;
    g_motion.live = true;
}

void attentionNoteFace(uint32_t nowMs, float nx, float ny, float score)
{
    const bool linked = g_face.live && !due(nowMs, g_face.stampMs + kFaceLinkMs) &&
                        fabsf(nx - g_face.nx) < kFaceLinkDist &&
                        fabsf(ny - g_face.ny) < kFaceLinkDist;
    g_face.reps = linked ? (g_face.reps + 1) : 1;
    g_face.nx = nx;
    g_face.ny = ny;
    g_face.quality = score;
    g_face.stampMs = nowMs;
    g_face.live = true;
}

void attentionTick(uint32_t nowMs)
{
    // AGE CUES OUT AGAINST THEIR OWN DWELL, so a cue that stopped being refreshed stops competing.
    // A face holds its claim longer than motion by construction, because its dwell is longer --
    // which is the precedence stated once, in one number, rather than twice.
    if (g_face.live) {
        const uint32_t d = dwellFor(GAZE_FACE, faceConfidence(g_face));
        if (due(nowMs, g_face.stampMs + d)) g_face.live = false;
    }
    if (g_motion.live) {
        const uint32_t d = dwellFor(GAZE_MOTION, motionConfidence(g_motion));
        if (due(nowMs, g_motion.stampMs + d)) g_motion.live = false;
    }

    GazeIntent next = {};
    next.stampMs = nowMs;

    if (g_face.live) {
        // FACE OUTRANKS MOTION UNCONDITIONALLY when it is live. Not "if it is more confident" --
        // the two confidences measure different things and are not comparable, and a close face is
        // the thing we most want the robot to look at even when a cart is rattling past behind it.
        next.kind = GAZE_FACE;
        next.nx = g_face.nx;
        next.ny = g_face.ny;
        next.confidence = faceConfidence(g_face);
    } else if (g_motion.live) {
        next.kind = GAZE_MOTION;
        next.nx = g_motion.nx;
        next.ny = g_motion.ny;
        next.confidence = motionConfidence(g_motion);
    } else {
        next.kind = GAZE_NONE;
    }

    if (next.kind == GAZE_NONE) {
        next.seq = g_seq;                 // no retarget; the face sees "nothing" and wanders
        next.goodUntilMs = nowMs;
    } else {
        const bool kindChanged = (next.kind != g_cur.kind);
        const bool moved = fabsf(next.nx - g_cur.nx) > kReTarget ||
                           fabsf(next.ny - g_cur.ny) > kReTarget;
        if (kindChanged || moved || g_cur.kind == GAZE_NONE) {
            next.seq = ++g_seq;
        } else {
            // SAME TARGET, REFRESHED. The seq is held so the face does not re-saccade, but
            // goodUntilMs moves forward so the target stays live while the cue keeps being seen.
            next.seq = g_cur.seq;
        }
        next.goodUntilMs = nowMs + dwellFor(next.kind, next.confidence);
    }

    g_cur = next;
    g_pub.publish(next);
}

bool attentionGet(GazeIntent* out)
{
    GazeIntent v;
    if (!g_pub.read(&v)) return false;
    if (v.kind == GAZE_NONE) return false;
    // THE STALENESS TEST LIVES HERE, against percept's own clock. The caller never holds a
    // perception timestamp, so a caller on a different clock cannot get this wrong -- and that is
    // the whole reason shared/clock.h exists instead of one include of hal.h.
    if (due(perceptNowMs(), v.goodUntilMs)) return false;
    *out = v;
    return true;
}

const char* attentionKindName(uint8_t kind)
{
    switch (kind) {
        case GAZE_FACE: return "face";
        case GAZE_MOTION: return "motion";
        default: return "none";
    }
}
