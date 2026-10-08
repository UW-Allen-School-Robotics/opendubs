// A TEST THAT NEEDS NOBODY AT THE BENCH.
//
// CLAUDE.md: "A test that needs a human at the bench is not a test. Nobody is usually there, and
// 'no stimulus' reads identically to 'broken hardware'. Make the firmware produce its own stimulus
// and its own verdict, and have it say INCONCLUSIVE rather than blame the hardware."
//
// So the perception pipeline generates its own stimulus: a synthetic moving blob is drawn into a
// luma plane and fed through the identical motion.cpp that processes camera frames, and the test
// asserts that the hottest cell follows the blob. The gates get the same treatment -- a frame where
// everything changed must be discarded, a static scene must produce no target -- and so does the
// arbiter's precedence, which is pure logic over a clock we supply.
//
// WHAT THIS CAN AND CANNOT PROVE. It proves the arithmetic: the differencing, the cell accumulation,
// the centroid, the thresholds relative to each other, the precedence, the confidence grading and
// the retarget hysteresis. It cannot prove anything about a real scene -- whether 16/255 is the
// right pixel threshold for this sensor's noise, whether 6% of a cell is the right floor for a
// person at 4 m, or whether the sign conventions are right for this lens. Those need the board and
// are listed as such in notes/design/tab5-face-architecture.md.
//
// It runs in two places and the SAME code is the test in both:
//   * on the host, via `./build.sh percept`, which is the only verification available to someone
//     without the hardware;
//   * on the device in a debug build, with its verdict in the panel status line.
#pragma once
#include <stdint.h>

// Writes a one-line verdict (PASS/FAIL plus the failing case) into `out`, which must be at least
// 96 bytes. Returns true on PASS. Allocates nothing beyond what motionBegin() already owns.
bool perceptSelfTest(char* out, int outLen);

// Longer form for the host harness: calls each case and prints a line per case through the supplied
// sink, so a failure says WHICH assertion broke rather than just that one did.
typedef void (*SelfTestSink)(const char* line);
bool perceptSelfTestVerbose(SelfTestSink sink);
