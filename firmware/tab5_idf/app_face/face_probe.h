// Face detection: a self-test, then live tracking off the camera.
//
// WHY THIS SHAPE. Phase 1.4's remaining half was recorded as "needs the camera and a face", which
// was wrong, and wrong against a rule already written down: a test that needs a human at the bench
// is not a test, because nobody is usually there and "no stimulus" reads identically to "broken
// hardware". So this runs in two stages:
//
//   STAGE 1, every boot, no camera and no human: detect on a reference image compiled in, and
//   print PASS / FAIL / INCONCLUSIVE with latency. Byte-identical input every run, so a latency
//   figure is comparable across builds and a miss is a real regression rather than bad lighting.
//
//   STAGE 2, live: detect on camera frames and publish a gaze target. This is the one that proves
//   something end to end -- stage 1 proves the MODEL, and says nothing about the camera.
//
// THE TRACKER NEVER TOUCHES LVGL. It publishes a target; the face app consumes it inside its own
// render loop, which already holds the LVGL lock. That is AUDIT.md invariant 1 satisfied by
// construction rather than by remembering -- an unlocked LVGL call from a second task corrupts
// internal state rather than failing cleanly, and that was the longest-running freeze in this app.
//
// Build-flagged. With OPENDUBS_PROBE_FACEDET undefined these are no-ops, esp-dl is never
// referenced, and the image is byte-identical -- measured, not assumed.
#pragma once

#include <stdint.h>

// Spawns the probe/tracker task. Safe to call unconditionally; compiles to nothing when off.
void faceProbeStart();

// Latest tracked face, as the face app should read it.
//   nx, ny : normalized gaze target, -1..1, already mapped and sign-corrected for the panel
//   seq    : increments per accepted detection, so the caller can tell a NEW fix from a repeat
// Returns false when nothing is being tracked (no camera, probe off, or the face was lost), in
// which case the face should resume its own autonomous saccades.
bool faceTrackGet(float* nx, float* ny, uint32_t* seq);

// A short human-readable state, drawn by the face app on the panel. Exists because a probe that
// reports only over serial left someone standing at the bench with no way to tell whether it was
// working -- and the serial port here has repeatedly been unreadable.
const char* faceProbeStatus();
