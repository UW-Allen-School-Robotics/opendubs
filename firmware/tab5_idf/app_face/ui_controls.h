/*
 * ui_controls — the technician screen and the fault screen.
 *
 * notes/design/tab5-dashboard.md, "What goes on the panel": four states in priority order, of
 * which the face is already built and the prompt layer is half-built in the Arduino firmware.
 * This file is the other two.
 *
 *   THE TECHNICIAN SCREEN is "what someone kneeling at the robot needs to diagnose it *and act*
 *   without a tablet": mode and state, the e-stop chain and contactor, battery SOC and pack
 *   voltage, the RC link, the Thor link and its age, fault flags, the IP — plus the grade-2 and
 *   grade-3 controls that belong next to those readouts, a mast jog above all. Reached by a
 *   deliberate gesture (ui_touch.cpp), times out back to the face.
 *
 *   THE FAULT SCREEN is the note's "one genuinely novel thing the panel can do that the tablet
 *   cannot, because the person who needs it is the person who walked over to the robot." It says
 *   what failed AND what clears it. The second half is the part that makes it worth building:
 *   a fault name alone sends someone to a laptop.
 *
 * EVERY READING IS DRAWN WITH ITS AGE, and that is enforced structurally rather than
 * remembered — a readout row owns three labels (key, value, age) and the one function that
 * updates it reads the group's stamp to decide all three. There is no code path that renders a
 * value without having computed its age, which is the only way the note's core argument
 * survives contact with a later feature:
 *
 *     "a native renderer holds the last values *and* the timestamp, so it can grey them out and
 *      show the age. For a surface whose whole job is to tell a human what the robot is doing, a
 *      failure mode that silently impersonates success is disqualifying."
 *
 * THREE STATES PER READING, not two. NEVER (no number has ever arrived — show "--", never a
 * zero), STALE (we had it, here is how old) and LIVE. The first is what stops a freshly booted
 * head with no link from rendering "0% SOC, 52.0 V absent, contactor open" as though measured.
 *
 * NOTHING ALLOCATES AFTER BUILD. Both screens are built once at startup and shown or hidden;
 * every label is lv_label_set_text_static() over a fixed char[] that outlives it, and a label is
 * only re-set when its rendered text actually changed (AUDIT.md invariants 6 and 8). The
 * CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 rule means an lv_label_set_text() here would churn
 * internal DRAM — the scarce resource — four times a second for a readout.
 */
#pragma once
#include <lvgl.h>
#include <cstdint>

// Build both layers as hidden children of `parent`. CALLER HOLDS THE LVGL LOCK.
void uiControlsBuild(lv_obj_t* parent);

// Show exactly one of: nothing (the face is up), the technician screen, the fault screen.
// CALLER HOLDS THE LVGL LOCK.
void uiControlsShowTech(bool on);
void uiControlsShowFault(bool on);

// Refresh whatever is visible, and re-gate every control against the live preconditions.
// CALLER HOLDS THE LVGL LOCK. Cheap when nothing is visible.
void uiControlsTick(uint32_t nowMs);

// Should the fault screen take the panel? True when a fault is present and the operator has not
// acknowledged it inside the suppression window. Lock-free: ui_touch.cpp asks before deciding.
bool uiControlsFaultPending(uint32_t nowMs);

// Deliberate mode (grade 4). A visible banner, grade-4 controls revealed, and its own idle
// timeout that is shorter than the screen's.
void uiControlsSetDeliberate(bool on, uint32_t nowMs);
bool uiControlsDeliberate();
uint32_t uiControlsDeliberateSince();

// The fault screen's TECHNICIAN button sets a one-shot flag rather than switching screens
// itself, so that arbitration stays in one place (ui_touch.cpp) and a control cannot leave the
// two layers both visible. Reading it clears it; LVGL-task only, so no synchronisation.
bool uiControlsTakeTechRequest();

// The "face in Ns" countdown, owned by ui_touch.cpp because it owns the idle timer.
void uiControlsSetRevertText(const char* s);

const char* uiControlsStatus();     // static buffer, for the panel and for a future STATUS reply
