/*
 * ui_grade — the confirmation-grade mechanism from notes/design/tab5-dashboard.md.
 *
 * That note settles the policy question and refuses to settle it as a list of allowed endpoints:
 * "the question is not *may the panel command* but *what does a given command have to cost*."
 * Four grades, with a test for assigning one. This file is the machinery those four grades are
 * made of, so that assigning a grade to a new control is one argument to gradeAdd() and not a
 * fresh invention of a gesture.
 *
 *   GRADE 1  single tap        nothing moves, nothing is energised, a mis-tap is undone by
 *                              tapping again. Fires on LVGL's CLICKED, which is already
 *                              "released over the target and not scrolled".
 *
 *   GRADE 2  hold to act       the control is a LIVE INTENT WITH AN EXPIRY. Motion continues
 *                              only while held; release, finger-slide, screen change or a
 *                              missed release event all stop it. The note is explicit that this
 *                              is the grade that earns its place: "a hold-to-move jog is safer
 *                              than a tap-to-move button, because the stop condition is the
 *                              absence of input rather than the presence of a second input."
 *
 *   GRADE 3  hold to confirm   a named action, a stated precondition, and a sustained ~1 s
 *                              gesture with visible progress. For discrete, consequential
 *                              things the panel can describe before doing.
 *
 *   GRADE 4  deliberate mode   grade 3's gesture, lengthened to 1.5 s, AND gated behind a mode
 *                              a bystander does not find. For "the one person who approached
 *                              with a job". The mode itself lives in ui_touch.cpp; this file
 *                              only refuses to fire while gradeSetEnabled() says no.
 *
 * TWO DESIGN CHOICES THAT DEPART FROM THE NOTES, both deliberate, both cheap to reverse:
 *
 *  1. PROGRESS IS A BAR, NOT A RING. Both notes say "ring". A ring is lv_arc, which renders
 *     through an arc draw task and a circle mask; AUDIT.md invariant 2 is a scar from exactly
 *     that class of cost ("lv_draw_buf_create_ex: No memory: 438x42" killed the rotated brow
 *     outright). A bar whose width is set carries identical information for one cheap rectangle
 *     write per step. If a ring is wanted later, measure it with build.sh cost first.
 *
 *  2. THE TICK IS AUTHORITATIVE, NOT THE EVENT. LVGL's RELEASED and PRESS_LOST are believed,
 *     but gradeTick() independently re-checks the live pointer state and the finger's position
 *     every tick and stops a held control that is no longer genuinely held. This is not
 *     belt-and-braces for its own sake: the Arduino firmware needed the same guard and says so
 *     in firmware/tab5_face/src/main.cpp, where the release path is reached by
 *     `td.wasReleased() || (!td.isPressed() && g_activeHold != HmiHit::NONE)`. A grade-2 control
 *     whose stop depends on one event arriving is a lift that keeps moving when it does not.
 *
 * LOCKING. Every function here is called from inside the LVGL lock, and the two ways that
 * happens are different:
 *   - gradeAdd() and gradeTick() are called by the face app, which takes the lock around them.
 *   - the event callback runs inside lv_timer_handler(), i.e. inside esp_lvgl_port's own
 *     lock/unlock pair, so it is locked BY CONSTRUCTION.
 * So NOTHING IN THIS FILE CALLS lvglLock(), and that is correct rather than an oversight.
 * (The port's mutex is recursive — xSemaphoreTakeRecursive, esp_lvgl_port.c — so a nested
 * lock would not deadlock. It would still be a lie about who owns what, and AUDIT.md's history
 * is of locks MISSING from the app task, not of locks nested in callbacks.)
 */
#pragma once
#include <lvgl.h>
#include <cstdint>

enum class Grade : uint8_t { Tap = 1, HoldToAct = 2, HoldToConfirm = 3, Deliberate = 4 };

// The two gesture lengths, named once. 1000 ms is the dashboard note's "≈1 s" for grade 3;
// 1500 ms is the architecture note §1.7's "hold-to-confirm >= 1.5 s" for granting energy, which
// is what every grade-4 control here does.
enum : uint32_t { GRADE3_HOLD_MS = 1000, GRADE4_HOLD_MS = 1500 };

// How often a held grade-2 control re-asserts. It must stay comfortably inside the intent's
// expiry (MB_MAST_EXPIRY_MS, 250 ms, in face_mailbox.h) so that one dropped republish is not a
// stop and several in a row are. Not pulled from face_mailbox.h on purpose: this file knows
// nothing about which intent a control writes, which is what lets a grade be reused.
enum : uint32_t { GRADE2_REPUBLISH_MS = 50 };

// Discrete fire — grades 1, 3 and 4. Called exactly once per completed gesture, from the LVGL
// task, under the lock. It must not block and must not do I/O: write an intent and return.
typedef void (*GradeFireFn)(int32_t arg);

// Live hold — grade 2. Called from gradeTick() while held, and ONCE MORE with held=false on the
// release/loss edge, so the handler has a single place to publish the stop.
typedef void (*GradeHoldFn)(int32_t arg, bool held, uint32_t nowMs);

// Register a control. `target` must be clickable; `progress` may be nullptr for grades 1 and 2
// (they have no progress to show) and must be a child whose width this file owns for 3 and 4.
// Returns an id for gradeSetEnabled(), or -1 if the pool is full — which is reported, not
// silent: see gradeStatus() and AUDIT.md invariant 7.
int gradeAdd(lv_obj_t* target, Grade grade, int32_t arg, GradeFireFn fire, GradeHoldFn hold,
             lv_obj_t* progress, int32_t progressFullW);

// Gate a control. A disabled control dims, refuses to begin a gesture, and aborts one in
// progress — the last part matters, because CH5 can go to OFF mid-hold and the panel must not
// complete a gesture whose precondition evaporated. ui_controls.cpp supplies the reason text.
void gradeSetEnabled(int id, bool enabled);

// Drive holds and progress. CALLER HOLDS THE LVGL LOCK. Call it once per UI tick, before
// anything reads the intents.
void gradeTick(uint32_t nowMs);

// Stop everything now: a held jog publishes its stop, a partial confirm is abandoned and its
// progress bar reset. Called on every screen change, on idle revert, and when the fault screen
// takes the panel — because "the screen went away" must mean "the lift stopped", not "the lift
// is still being asked by a control nobody can see".
void gradeAbortAll(uint32_t nowMs);

bool        gradeAnyHeld();
const char* gradeStatus();     // static buffer: pool use, overflow, what is held
