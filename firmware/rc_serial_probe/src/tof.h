// Z-height from the ToF, and the per-robot calibration that turns a distance into a height.
//
// The sensor measures a DISTANCE. What the lift needs is a HEIGHT, and the relationship
// between the two is a property of this robot's mounting -- where the sensor sits, which way
// it points, and what it is looking at. So it is discovered on the machine and stored in
// NVS, never in the repo:
//
//   TOFCAL ZERO    with the lift at its own bottom limit: this distance means height 0
//   TOFCAL TOP     with the lift at the top: fixes the SIGN, and records the travel
//   TOFCAL SHOW / CLEAR
//
// Without a ZERO the height is unknown and tofHeightMm() returns false -- which leaves the
// mast open-loop and its pinch guard inert, reported as `pinch=nosensor`. That is the
// honest state: an uncalibrated distance is not a height, and guessing one would put the
// guard's threshold somewhere arbitrary.
#pragma once
#include <Arduino.h>
#include <M5Unified.h>

// `select` points the PaHub at the ToF's channel; it is called before every transaction.
void tofBegin(m5::I2C_Class **bus, bool (*select)());
// Non-blocking: at most one I2C transaction per call, so it never stalls the 50 Hz loop.
void tofTick(uint32_t nowMs);
// Latest height in mm. False when uncalibrated, unhealthy, or stale.
bool tofHeightMm(float *mm);
// Raw distance and health, for telemetry and for calibration by eye.
bool     tofRaw(uint16_t *mm, uint8_t *status);
bool     tofCalibrated();
uint32_t tofReads();
uint32_t tofErrors();
// Publish [TOF]. Cheap: no I2C, just the last sample.
void tofPublish();
// Handles "TOFCAL ..." and returns true if it consumed the command.
bool tofCalCommand(const char *cmd);
