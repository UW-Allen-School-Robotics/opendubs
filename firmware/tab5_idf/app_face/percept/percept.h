// THE PERCEPTION TASK: the only thing in percept/ that has a thread.
//
// WHAT IT IS ALLOWED TO BE, stated as the shape of this header rather than as a rule somebody has
// to remember:
//
//   * It is HANDED a pixel buffer. It does not go and get one. Acquiring the camera means a vendor
//     HAL call that takes an lv_obj_t*, so acquisition belongs to the face app, which owns LVGL;
//     perception is given the pointer and only ever reads through it. That one inversion is what
//     makes "perception never touches LVGL" structural: there is no LVGL symbol reachable from
//     this directory to touch.
//
//   * It publishes NUMBERS (attention.h, via a seqlock) and writes PIXEL BUFFERS (the debug
//     preview, which the face app points a canvas at). It creates no object, takes no lock the UI
//     wants, and nothing it does can make the face wait.
//
//   * It runs at priority 2 PINNED TO CPU1. Pinning is the protection, not the priority: a task
//     pinned to CPU1 cannot preempt the animation on CPU0 whatever its number is, whereas a
//     priority ladder only holds until somebody adds a task to it. A priority-2 UNPINNED
//     perception task preempting the priority-1 animation task on its own core is a failure this
//     project has already had.
//
//   * It is ONE task, replacing face_probe's one task -- not an additional one. That matters: with
//     esp-dl linked, creating one more FreeRTOS task has already asserted in xPortCheckValidTCBMem
//     because a TCB must sit in internal DRAM and there was not enough left
//     (firmware/tab5_idf/idf_component.yml records it). Its stack is in PSRAM, so it uses LESS
//     internal DRAM than the task it replaces.
#pragma once
#include <stdint.h>
#include "attention.h"

// `src` is a live RGB565 frame buffer, srcW x srcH, row stride srcW pixels, that someone else keeps
// overwriting -- today the vendor camera task's PPA output. percept only reads it, and tolerates
// reading across a frame boundary. Returns false if the buffers or the task could not be created,
// in which case perceptStatus() says why and the face simply never gets a target.
bool perceptStart(const uint16_t* src, int srcW, int srcH);

bool perceptRunning();

// The attention target, or false when there is nothing to attend to. Thin wrapper over
// attentionGet() so callers need one include.
bool perceptGaze(GazeIntent* out);

// Tell the gate that the head is about to move under its own command. See selfmotion.h -- nothing
// calls this yet because the pan/tilt servos are not built; it is here so that when they are, the
// gate is already wired and a commanded move cannot be mistaken for the world moving.
void perceptNoteHeadCommand(uint32_t durMs);

// ---- debug instruments. Declared unconditionally; null/empty when compiled out. --------------
// The face app owns every pixel that reaches the panel, so these hand it a buffer and a sequence
// number and it decides when to invalidate.
const uint16_t* perceptPreview();   // RGB565, kPerceptW x kPerceptH, or nullptr
int perceptPreviewW();
int perceptPreviewH();
uint32_t perceptFrameSeq();         // increments per processed frame
const char* perceptStatus();        // one short line; always NUL-terminated, never nullptr
