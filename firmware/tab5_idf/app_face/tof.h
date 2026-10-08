#pragma once
#include <stdint.h>

// The FRONT ToF on Grove Port A at 0x29 -- a VL53L0X, confirmed on the robot 2026-10-06 by its
// model ID (0xEE at register 0xC0). NOT the lift's height sensor, which lives on the StamPLC.
//
// Identified rather than assumed, because the other candidate part is a VL53L1X with SIXTEEN-BIT
// registers and a driver for one silently fails against the other.
void tofBegin();

// Returns false on anything the sensor does not vouch for, and deliberately does NOT fall back to
// 0: zero is a plausible distance, so a caller that treats it as one reads a sensor fault as
// "something is right in front of me". That wording is the Arduino firmware's and the reasoning
// still holds.
bool tofReadMm(float* mm);

const char* tofStatus();
// Raw register dump, for diagnosing "ranging but no sample". See tof.cpp.
const char* tofRegs();
// One short phrase for the panel: "no sensor", "no sample", "out of range" or "weak return".
const char* tofReason();
