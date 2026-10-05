// THE ATTENTION ARBITER: one target, three sources, a stated precedence.
//
//   face (close, confident)  >  motion (any range)  >  autonomous saccades
//
// Each degrades into the next. A face fix outranks motion because it carries information motion
// cannot: not just that something moved there, but that a person is pointed at the robot -- which
// is the only honest basis for "someone is addressing me", the behaviour face-design.md ranks
// first. Motion outranks the autonomous saccade because something that moved is more interesting
// than a number from rand(). And when neither is live the face wanders on its own, which is not a
// failure mode but the resting behaviour: a robot whose eyes have stopped moving reads as broken.
//
// CONFIDENCE IS GRADED HERE, NOT FILTERED BY THE DETECTOR. esp-dl's score threshold is a `const`
// member with no setter, and forking the vendor component to move it is not on the table. It does
// not need to be: every hit the detector returns is passed in, and this file decides what the hit
// is worth. A weak, isolated hit earns a brief low-amplitude glance that the face drifts out of; a
// strong hit repeated in the same place earns a sustained look. The detector answers "is there a
// face"; the arbiter answers "how much do I believe it", and the face answers "so how far do I
// turn my eyes" -- three decisions in the three places that own them.
//
// PURE COMPUTE. No LVGL, no HAL, no FreeRTOS. The publication is a seqlock (shared/pub.h) with one
// writer, so the UI reading a target can never block behind a 35 ms inference.
#pragma once
#include <stdint.h>

enum GazeKind : uint8_t {
    GAZE_NONE = 0,
    GAZE_MOTION,
    GAZE_FACE,
};

struct GazeIntent {
    float nx, ny;         // -1..1 in the camera frame; the face maps these to pupil travel
    float confidence;     // 0..1. Scales how far the face commits -- amplitude AND dwell.
    uint32_t seq;         // changes only on a NEW target, so the face saccades once per target
    uint32_t stampMs;     // percept's own clock (shared/clock.h), never the HAL's
    uint32_t goodUntilMs; // after this the target is stale; the face resumes its own saccades
    uint8_t kind;         // GazeKind
    uint8_t pad[3];
};

void attentionReset();

// Called by the perception task once per processed frame, when motion found a target.
// `strength` is MotionResult::strength.
void attentionNoteMotion(uint32_t nowMs, float nx, float ny, float strength);

// Called when the detector returned a box. `score` is the detector's own score, unfiltered.
void attentionNoteFace(uint32_t nowMs, float nx, float ny, float score);

// Called once per frame after the notes, whether or not anything was noted: this is where cues age
// out and where the published intent is refreshed.
void attentionTick(uint32_t nowMs);

// READER SIDE, callable from any task. Returns false when there is nothing to attend to -- never
// published, aged out, or arbitrated to GAZE_NONE -- in which case the face does its own thing.
//
// The staleness test happens INSIDE here, against percept's clock, precisely so that no caller
// ever has to hold a timestamp from one clock and compare it with another. See shared/clock.h.
bool attentionGet(GazeIntent* out);

// For the status line and the self-test.
const char* attentionKindName(uint8_t kind);
