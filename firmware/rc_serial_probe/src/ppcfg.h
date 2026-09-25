// goBILDA Pinpoint configuration — the part we were never sending.
//
// THE PINPOINT FORGETS. Its user guide is explicit: "Device Configurations are lost on
// device power cycles, so your I2C Controller device should send the config information
// over I2C once every time the code starts." We had a read-only driver, so the device has
// been running on defaults — pod offsets of zero and whatever tick rate it starts with —
// and reporting a pose computed from them. It looked healthy the whole time, because a
// wrongly-configured Pinpoint still returns plausible numbers.
//
// So this module owns the write side, applies it at boot, and RE-applies it whenever the
// device comes back after dropping off the bus — which on this bus it does.
//
// Every value here is a physical property of an individual robot: where the pods are
// bolted, which way round they ended up, what wheel is on them. They live in NVS, never in
// the repo. Uncommissioned, tracking is wrong under rotation and nothing says so, which is
// why ppCfgCommissioned() exists and is published.
//
// Conventions, from the user guide (3110-0002-0001) — worth writing down because getting a
// sign wrong here produces smooth, believable, incorrect odometry:
//
//   X pod   tracks FORWARD travel. Its count must INCREASE when the robot moves forward.
//   Y pod   tracks SIDEWAYS travel. Its count must INCREASE when the robot moves LEFT.
//
//   xOffsetMm  how far SIDEWAYS the X pod sits from the tracking point.
//              LEFT of the point is POSITIVE, right is negative.
//   yOffsetMm  how far FORWARD the Y pod sits from the tracking point.
//              FORWARD is POSITIVE, backwards is negative.
//
//   Distance along each pod's own axis of travel does not matter — only the perpendicular
//   offset. The guide notes the most common mistake is a sign: an offset that is positive
//   when it should be negative.
//
//   ticksPerMm = encoder CPR / wheel circumference in mm.
//     goBILDA 4-bar pod (3110-0001-0002): 2000 / 100.53 = 19.894
//     goBILDA swingarm pod:                                13.263
//
// Yaw scalar is deliberately NOT written. It is factory-calibrated per device, the guide
// discourages tuning it, and on a power cycle the device reverts to its factory value
// unless someone sends one — so sending nothing is how you keep the good number.
#pragma once
#include <Arduino.h>
#include <M5Unified.h>

void ppCfgBegin(m5::I2C_Class **bus, bool (*select)());

// Call every loop with the driver's view of whether the device is answering (debounced — a
// single missed frame must not read as absent) and whether the robot is genuinely still.
// Applies the config at boot and again on every absent -> present transition, because a
// Pinpoint that has power-cycled is back to defaults and will not tell you. The pose + IMU
// zero that goes with it is deferred until `still`: taken while moving, it bakes the motion
// into the gyro offset and heading drifts with the robot parked.
void ppCfgTick(uint32_t nowMs, bool present, bool still);

bool ppCfgCommissioned();     // false = running on defaults; tracking is not trustworthy
bool ppCfgApplied();          // has the current config reached the device
uint32_t ppCfgApplyCount();   // how many times we have had to re-send it
bool ppCfgImuPending();       // a pose + IMU zero is owed; the pose is not trustworthy until it lands

// Handles "PPCAL ..." and returns true if it consumed the command.
bool ppCfgCommand(const char *cmd);
