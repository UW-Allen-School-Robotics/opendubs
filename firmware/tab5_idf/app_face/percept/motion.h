// MOTION SALIENCY: the primary attention cue.
//
// WHY MOTION IS PRIMARY AND NOT A FALLBACK. The face detector is structurally short-sighted. The
// model is msr_s8_v1; it takes a 160x120 input and its smallest anchor is 16 px, so the smallest
// face it can resolve is about 128 px measured in the camera's own 1280x720 pixels -- which puts a
// human face inside roughly 1.1 m. That is not a tuning problem and no threshold reaches it: it is
// the model's input resolution against the camera's field of view. A robot whose only sense of
// where people are stops at arm's length is not attending to a room.
//
// Frame differencing has none of that. It is distance-independent (what matters is how many pixels
// changed, and a person far away still moves the pixels they cover), it works across the whole
// frame, and it costs no model. What it cannot do is tell a person from a passing cart -- so it
// says WHERE, and the detector, when it fires, says WHO (see attention.h).
//
// PURE COMPUTE, NO LVGL, NO HAL, NO FREERTOS. Everything here is arithmetic over byte buffers, so
// the host self-test in selftest.h exercises the identical code path that runs on the panel.
// The structural rule from notes/design/tab5-face-architecture.md §3 -- perception may not include
// <lvgl.h> and may not touch an LVGL object -- holds here by there being nothing to touch.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "frame.h"

// The cell grid. 16 x 9 over 320 x 180 means 20 x 20 px per cell: coarse enough that a cell's
// changed-pixel count is a stable statistic rather than noise, fine enough that the resulting gaze
// target is within about 4 degrees of where the movement actually was.
constexpr int kMotionCellsX = 16;
constexpr int kMotionCellsY = 9;
constexpr int kMotionCellW = kPerceptW / kMotionCellsX;   // 20
constexpr int kMotionCellH = kPerceptH / kMotionCellsY;   // 20
constexpr int kMotionCellArea = kMotionCellW * kMotionCellH;

struct MotionResult {
    bool valid;         // a cell cleared the floor, so nx/ny/strength mean something
    bool gated;         // this frame was discarded; nothing was measured
    float nx, ny;       // -1..1, the same normalisation and the same signs as the face path
    float strength;     // 0..1, smoothed fraction of the hottest cell's pixels that are changing
    float changedFrac;  // 0..1, fraction of the WHOLE frame that changed (the global-change gate)
    int cellX, cellY;   // the hottest cell, for the status line
};

// Allocates the previous-luma plane (57,600 B) in PSRAM. Returns false if it could not.
bool motionBegin();

// Forget all history. Called after a gated frame, after self-motion, and by the self-test.
void motionReset();

// One frame. `luma` is kPerceptW x kPerceptH as written by frameSample().
//
// `suppress` means "self-motion is happening, do not believe this frame". A suppressed frame is
// still copied into the history, so that when suppression lifts the next difference is against a
// recent view of the world rather than against whatever was there before the robot moved -- which
// would otherwise produce one enormous false target at exactly the moment the robot settles.
MotionResult motionUpdate(const uint8_t* luma, bool suppress);

// The smoothed per-cell heat, kMotionCellsX * kMotionCellsY, in units of 1/16 of a changed pixel.
// For the debug overlay and the self-test only.
const uint16_t* motionHeat();
