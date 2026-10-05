// SELF-MOTION GATING: tell "the world moved" apart from "I moved".
//
// A frame difference cannot distinguish the two. If the robot drives, or the head pans, or someone
// picks the panel up, every pixel changes and the hottest cell is an artefact of our own movement
// -- so the eyes would snap to a spot chosen by the accelerometer. Two independent gates close
// that off, and they fail in different directions on purpose:
//
//   1. THE IMU, HERE. The Tab5 has its own BMI270 on the internal I2C bus, already initialised by
//      the vendor HAL at boot (HalEsp32::imu_init -> accel_gyro_bmi270_enable_sensor). Angular rate
//      above a threshold suppresses perception for a settling window. This catches motion BEFORE
//      its effect reaches a frame, including motion that moves the camera without changing much of
//      the picture -- a slow pan across a blank wall.
//
//   2. THE GLOBAL-CHANGE TEST, in motion.cpp. More than 30% of the frame changing at once is
//      discarded whatever the IMU says. This catches what the IMU cannot: a light switch, an
//      auto-exposure step, a lens being covered -- cases where the robot did not move at all.
//
// WHY NOT ASK THE StamPLC. The obvious third signal is "the drive is commanded", which the safety
// controller knows. We deliberately do not use it. The head must not need the StamPLC to decide
// where to look: the Tab5 sits on the e-stop-protected drive branch, so with the contactor open it
// has no power at all, and in every other condition a link that is down or stale would turn a
// gating input into an unexplained change in gaze behaviour. The IMU is on the same board as the
// camera and answers the actual question -- did THIS camera move -- without a network.
//
// PURE-ISH: this file includes a sensor driver header and nothing else. No LVGL, no HAL, no LVGL
// transitively (which is why it does not include hal.h for GetHAL()->updateImuData(); see
// shared/clock.h for the same argument about the clock).
#pragma once
#include <stdint.h>

void selfMotionBegin();

// One IMU burst. Call once per perception frame; cheap (a ~12-byte I2C read at 400 kHz).
void selfMotionSample(uint32_t nowMs);

// THE HOOK FOR THE HEAD'S OWN MOVEMENT, and it is honestly a hook: the pan/tilt servos
// (notes/design/tab5-face-architecture.md §1.5) are not built yet, so nothing calls this today.
// When the face drives its own head it must call this as it issues the command, because a servo
// that has been told to move has moved the camera before the IMU has integrated enough rate to
// notice -- the command is the earlier and better signal, and the IMU is the backstop.
//
// `durMs` is how long the commanded move will take; the settling window is added on top.
void selfMotionNoteHeadCommand(uint32_t nowMs, uint32_t durMs);

// True while perception should not believe a frame difference.
bool selfMotionSuppressed(uint32_t nowMs);

// Last measured angular rate magnitude in degrees per second, for the status line. Returns 0 when
// no IMU is present.
float selfMotionRateDps();

// False when the IMU never produced a reading, so the status line can say "gate: no imu" rather
// than silently reporting a gate that is not actually watching anything.
bool selfMotionHasImu();
