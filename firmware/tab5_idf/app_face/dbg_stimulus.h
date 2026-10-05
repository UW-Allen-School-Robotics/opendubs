/*
 * dbg_stimulus — a synthetic RobotSnapshot publisher, so the touch UI is testable with no robot,
 * no network and nobody standing at the bench.
 *
 * WHY THIS IS NOT OPTIONAL. The technician screen's entire job is to render robot state with its
 * age, and there is no `net` yet to supply any. Without a stand-in, a bench Tab5 shows eight rows
 * of "--" and the only thing you can confirm is that the screen appears — which means the staleness
 * logic, the fault auto-raise, the per-control preconditions and the "--" versus zero distinction
 * are all unexercised, and the first time anyone sees them is on the robot.
 *
 * It is also what CLAUDE.md's bench rules demand, written for serial but general: "Anything you
 * need to trigger must be self-triggering in firmware, on a timer, under a build flag, and must
 * print its own verdict." And: "A test that needs a human at the bench is not a test. Nobody is
 * usually there, and 'no stimulus' reads identically to 'broken hardware'." A panel showing "--"
 * because no link exists is indistinguishable from a panel showing "--" because the staleness code
 * is broken, and the only way to tell them apart is to supply the stimulus.
 *
 * IT LIVES OUTSIDE ui/ ON PURPOSE. It includes face_mailbox.h and not lvgl.h, because it is
 * standing in for `net` — the architecture note's §3 rule 4, "percept/, sense/, audio/, net/ never
 * call into ui/", applies to it exactly as it will to the real thing. When `net` lands, this file
 * is deleted and nothing in ui/ changes, which is the test of whether the boundary was drawn in
 * the right place.
 *
 * Compiled only when OPENDUBS_TOUCH_DEBUG=1.
 */
#pragma once
#include <cstdint>

// Publishes one synthetic generation. Call every tick; it rate-limits itself to 20 Hz, which is
// the rate the real status stream is planned at (architecture note §2.3, "status TX at 20 Hz").
void dbgStimulusTick(uint32_t nowMs);

// What the script is doing right now, for the panel. Static buffer.
const char* dbgStimulusPhase();
