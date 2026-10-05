// Face-detection self-test. Stimulus and verdict both in firmware.
//
// WHY THIS SHAPE. Phase 1.4's remaining half was recorded as "needs the camera and a face", which
// was wrong, and wrong against a rule already written down: a test that needs a human at the bench
// is not a test, because nobody is usually there and "no stimulus" reads identically to "broken
// hardware". The fix is the one the rule prescribes -- the firmware supplies its own stimulus (a
// reference image compiled in) and states its own verdict (PASS / FAIL / INCONCLUSIVE), on a timer,
// line by line.
//
// It is also strictly BETTER than pointing the camera at a person: the input is byte-identical
// every run, so a latency figure is comparable across builds and a miss is a real regression rather
// than bad lighting. The camera only ever needed to answer "does the lens see", which is a
// different and much smaller question.
//
// Build-flagged. With OPENDUBS_PROBE_FACEDET undefined these are no-ops and esp-dl is never
// referenced, so the linker drops it and the image is unchanged -- measured, not assumed.
#pragma once

#include <stdint.h>

// Spawns the probe task. Safe to call unconditionally; compiles to nothing when the flag is off.
void faceProbeStart();
