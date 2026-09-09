#include "lift.h"
#include <VL53L0X.h>
#include <VL53L1X.h>
#include <Wire.h>

LiftController Lift;

// ---- FRONT rangefinder (screen-mounted) ------------------------------------------
// This is what the head is looking at, published as CAN_ID_TOF_FRONT. It is NOT the lift's
// height sensor — that one is on the StampPLC and arrives over UDP.
//
// Which M5 ToF unit is fitted is a per-build fact and getting it wrong is silent: the ToF4M
// is a VL53L1X with SIXTEEN-BIT registers, the older unit a VL53L0X with 8-bit ones, and a
// driver for one simply fails to init against the other. So the part is identified at run
// time from its model-ID register instead of assumed:
//   VL53L1X answers 0xEACC at 0x010F,  VL53L0X answers 0xEE at 0xC0.
static constexpr uint8_t TOF_ADDR = 0x29;   // both parts use this by default
static constexpr int     PIN_SDA  = 53;     // Tab5 Grove Port A
static constexpr int     PIN_SCL  = 54;

static VL53L0X tof0;
static VL53L1X tof1;
enum class TofPart : uint8_t { NONE, L0X, L1X };
static TofPart tofPart = TofPart::NONE;

static bool readReg16(uint16_t reg, uint16_t *out) {
  Wire.beginTransmission(TOF_ADDR);
  Wire.write((uint8_t)(reg >> 8));
  Wire.write((uint8_t)reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((uint8_t)TOF_ADDR, (uint8_t)2) != 2) return false;
  *out = ((uint16_t)Wire.read() << 8) | Wire.read();
  return true;
}

static bool readReg8(uint8_t reg, uint8_t *out) {
  Wire.beginTransmission(TOF_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((uint8_t)TOF_ADDR, (uint8_t)1) != 1) return false;
  *out = Wire.read();
  return true;
}

static void tofBegin() {
  uint16_t id16 = 0;
  uint8_t  id8  = 0;
  if (readReg16(0x010F, &id16) && id16 == 0xEACC) {
    tof1.setTimeout(500);
    if (tof1.init()) {
      tof1.setDistanceMode(VL53L1X::Long);
      tof1.setMeasurementTimingBudget(50000);
      tof1.startContinuous(50);
      tofPart = TofPart::L1X;
      Serial.println("[TOF] front: VL53L1X (ToF4M) ranging");
      return;
    }
    Serial.println("[TOF] front: VL53L1X identified but init FAILED");
    return;
  }
  if (readReg8(0xC0, &id8) && id8 == 0xEE) {
    tof0.setTimeout(500);
    if (tof0.init()) {
      tof0.startContinuous();
      tofPart = TofPart::L0X;
      Serial.println("[TOF] front: VL53L0X ranging");
      return;
    }
    Serial.println("[TOF] front: VL53L0X identified but init FAILED");
    return;
  }
  Serial.printf("[TOF] front: no known ToF at 0x%02X (id16=0x%04X id8=0x%02X)\n",
                TOF_ADDR, id16, id8);
}

// Returns false on anything the sensor does not vouch for. Deliberately does NOT fall back
// to 0.0: zero is a plausible distance, and a caller that treats it as one reads a sensor
// fault as "something is right in front of me".
bool LiftController::readTofMm_(float *mm) {
  uint16_t d = 0;
  switch (tofPart) {
    case TofPart::L1X:
      d = tof1.read(false);
      if (tof1.timeoutOccurred() || tof1.ranging_data.range_status != VL53L1X::RangeValid)
        return false;
      break;
    case TofPart::L0X: {
      // NOT readRangeContinuousMillimeters(): that BLOCKS until a sample is ready, and this
      // runs on the 50 Hz task that also drives the pan/tilt servos.
      if ((tof0.readReg(VL53L0X::RESULT_INTERRUPT_STATUS) & 0x07) == 0) return false;
      d = tof0.readReg16Bit((uint8_t)(VL53L0X::RESULT_RANGE_STATUS + 10));
      tof0.writeReg(VL53L0X::SYSTEM_INTERRUPT_CLEAR, 0x01);
      if (d == 8190 || d == 0) return false;
      break;
    }
    default:
      return false;
  }
  *mm = (float)d;
  return true;
}

void LiftController::begin() {
  mtx_ = xSemaphoreCreateMutex();
  lastTickMs_ = millis();
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  tofBegin();
  // Nothing to de-energize: this object does not actuate. The lift's relays belong to the
  // StampPLC, which holds them open unless its own driver says otherwise.
}

// ---- requests ----------------------------------------------------------------------
// Setting an intent is all that happens here. No relay, no pulse timing, no pulse-toggle
// bookkeeping: the StampPLC's driver owns all of it, including the reversal gap that this
// code used to violate by firing two pulses back-to-back with both legs briefly energized.
void LiftController::commandUp() {
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  if (!inhibit_) { request_ = Motion::UP; requestMs_ = millis(); }
  xSemaphoreGive(mtx_);
}

void LiftController::commandDown() {
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  if (!inhibit_) { request_ = Motion::DOWN; requestMs_ = millis(); }
  xSemaphoreGive(mtx_);
}

void LiftController::commandStop() {
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  request_ = Motion::STOP;
  requestMs_ = millis();
  xSemaphoreGive(mtx_);
}

void LiftController::setInhibit(bool on) {
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  inhibit_ = on;
  if (on) { request_ = Motion::STOP; requestMs_ = millis(); }   // engaging always asks for stop
  xSemaphoreGive(mtx_);
}

Motion LiftController::request() {
  if (!mtx_) return Motion::STOP;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  const Motion r = inhibit_ ? Motion::STOP : request_;
  xSemaphoreGive(mtx_);
  return r;
}

void LiftController::reportFromRobot(float heightMm, Motion motion, bool atTop, bool atBot,
                                     bool guardHold) {
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  heightMm_  = heightMm;
  motion_    = motion;
  atTop_     = atTop;
  atBot_     = atBot;
  guardHold_ = guardHold;
  reportMs_  = millis();
  xSemaphoreGive(mtx_);
}

void LiftController::tick(uint32_t nowMs) {
  if (!mtx_) return;
  lastTickMs_ = nowMs;
  float mm;
  const bool ok = readTofMm_(&mm);
  xSemaphoreTake(mtx_, portMAX_DELAY);
  // The sensor produces a sample every ~33 ms and this runs every 20 ms, so most polls
  // legitimately find nothing ready. Reporting that as "not OK" made the reading flicker.
  // Hold the last good reading and let it go STALE instead.
  if (ok) { tofMm_ = mm; tofFreshMs_ = nowMs; }
  tofOk_ = (tofFreshMs_ != 0) && (nowMs - tofFreshMs_ < TOF_STALE_MS);
  xSemaphoreGive(mtx_);
}

// The soft e-stop cannot stop the lift itself any more — it has no actuator. What it can do
// is stop ASKING, immediately and continuously, which the StampPLC reads as stop. The real
// stop remains the hardware e-stop, which drops the drive bus the lift runs on.
void LiftController::enforceSafe(uint32_t nowMs) {
  (void)nowMs;
  if (!mtx_) return;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  if (inhibit_) request_ = Motion::STOP;
  xSemaphoreGive(mtx_);
}

LiftController::Snapshot LiftController::snapshot() {
  Snapshot s{};
  if (!mtx_) return s;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  const bool fresh = (reportMs_ != 0) && (millis() - reportMs_ < REPORT_STALE_MS);
  s.heightKnown  = fresh && heightMm_ >= 0.0f;
  s.heightMm     = s.heightKnown ? heightMm_ : -1.0f;
  s.motion       = fresh ? motion_ : Motion::STOP;
  s.atTop        = fresh && atTop_;
  s.atBot        = fresh && atBot_;
  s.guardHold    = fresh && guardHold_;
  s.inhibit      = inhibit_;
  s.tofMm        = tofMm_;      // front rangefinder — a different question from heightMm
  s.tofOk        = tofOk_;
  s.tofPart      = (tofPart == TofPart::L1X) ? TOF_PART_L1X
                 : (tofPart == TofPart::L0X) ? TOF_PART_L0X : TOF_PART_NONE;
  s.relayUpPulse = (request_ == Motion::UP);
  s.relayDnPulse = (request_ == Motion::DOWN);
  xSemaphoreGive(mtx_);
  return s;
}
