#include "lift.h"
#include <Wire.h>

LiftController Lift;

// ---- I2C addresses (verify against the M5 device libs when hardware is attached) ----
static constexpr uint8_t PBHUB_ADDR = 0x61;   // M5Stack PbHUB
static constexpr uint8_t TOF_ADDR   = 0x29;   // M5Stack ToF4M (VL53L1X default)
// PbHUB channel/pin the relay control lines land on. Relay unit = one digital line, HIGH=coil on.
static constexpr uint8_t RELAY_UP_CH = 0;     // PbHUB CH0
static constexpr uint8_t RELAY_DN_CH = 1;     // PbHUB CH1

// PbHUB "digital write" register scheme is a placeholder — replace with the M5 PbHub call
// (e.g. Unit_PbHub::digitalWrite(ch, pin, level)) once the unit is on the bus.  [TODO-HW]
static void pbhubDigitalWrite(uint8_t ch, uint8_t level) {
  Wire.beginTransmission(PBHUB_ADDR);
  Wire.write(0x40 + ch);          // TODO-HW: confirm PbHUB register map
  Wire.write(level ? 1 : 0);
  Wire.endTransmission();
}

void LiftController::begin() {
  mtx_ = xSemaphoreCreateMutex();
  lastTickMs_ = millis();
  if (!simulate_) {
    // TODO-HW: Wire.begin(sda,scl) on the Tab5 Grove Port A pins; init PbHUB + ToF4M ranging.
    // Wire.begin(G53, G54); ... tof.begin(); tof.startContinuous();
    // Ensure both relays start CLOSED (NC, no pulse) so nothing moves on boot.
    pbhubDigitalWrite(RELAY_UP_CH, 0);
    pbhubDigitalWrite(RELAY_DN_CH, 0);
  }
}

// The relay is momentary: open (trigger) the NC contact, then closePulses_() releases it.
void LiftController::pulseRelay_(uint8_t leg, uint32_t nowMs) {
  if (leg == 0) upPulseUntil_ = nowMs + PULSE_MS; else dnPulseUntil_ = nowMs + PULSE_MS;
  if (!simulate_) pbhubDigitalWrite(leg == 0 ? RELAY_UP_CH : RELAY_DN_CH, 1);  // open NC → trigger
  Serial.printf("[LIFT] pulse %s leg\n", leg == 0 ? "UP" : "DOWN");
}

void LiftController::closePulses_(uint32_t nowMs) {
  if (upPulseUntil_ && nowMs >= upPulseUntil_) { upPulseUntil_ = 0; if (!simulate_) pbhubDigitalWrite(RELAY_UP_CH, 0); }
  if (dnPulseUntil_ && nowMs >= dnPulseUntil_) { dnPulseUntil_ = 0; if (!simulate_) pbhubDigitalWrite(RELAY_DN_CH, 0); }
}

float LiftController::readTofMm_() {
  if (simulate_) return tofMm_;                // sim: tofMm_ is advanced in tick()
  // TODO-HW: return tof.read();  (0/timeout => tofOk_=false upstream)
  return tofMm_;
}

// pulse-toggle / pulse-reverse: pressing a leg while already going that way STOPS; otherwise GO.
void LiftController::moveCmd_(Motion dir) {
  if (inhibit_) return;
  uint32_t now = millis();
  if (motion_ == dir) { pulseRelay_(dir == Motion::UP ? 0 : 1, now); motion_ = Motion::STOP; }
  else                { pulseRelay_(dir == Motion::UP ? 0 : 1, now); motion_ = dir; }
}

void LiftController::commandUp()   { if (!mtx_) return; xSemaphoreTake(mtx_, portMAX_DELAY); moveCmd_(Motion::UP);   xSemaphoreGive(mtx_); }
void LiftController::commandDown() { if (!mtx_) return; xSemaphoreTake(mtx_, portMAX_DELAY); moveCmd_(Motion::DOWN); xSemaphoreGive(mtx_); }

void LiftController::commandStop() {
  if (!mtx_) return; xSemaphoreTake(mtx_, portMAX_DELAY);
  uint32_t now = millis();
  if (motion_ == Motion::UP)   pulseRelay_(0, now);
  if (motion_ == Motion::DOWN) pulseRelay_(1, now);
  motion_ = Motion::STOP;
  xSemaphoreGive(mtx_);
}

void LiftController::setInhibit(bool on) {
  if (!mtx_) return; xSemaphoreTake(mtx_, portMAX_DELAY);
  inhibit_ = on;
  if (on && motion_ != Motion::STOP) {                 // stop immediately on engage
    uint32_t now = millis();
    if (motion_ == Motion::UP) pulseRelay_(0, now); else pulseRelay_(1, now);
    motion_ = Motion::STOP;
  }
  xSemaphoreGive(mtx_);
}

void LiftController::tick(uint32_t nowMs) {
  if (!mtx_) return;
  float dt = (nowMs - lastTickMs_) / 1000.0f; lastTickMs_ = nowMs;
  if (dt < 0) dt = 0; if (dt > 0.1f) dt = 0.1f;
  xSemaphoreTake(mtx_, portMAX_DELAY);

  if (simulate_) {                                     // model travel + limit auto-stop
    if (motion_ == Motion::UP)   { heightMm_ += RATE_MM_S * dt; if (heightMm_ >= MAX_MM) { heightMm_ = MAX_MM; motion_ = Motion::STOP; } }
    else if (motion_ == Motion::DOWN) { heightMm_ -= RATE_MM_S * dt; if (heightMm_ <= MIN_MM) { heightMm_ = MIN_MM; motion_ = Motion::STOP; } }
    tofMm_ = TOF_STANDOFF + heightMm_;
    tofOk_ = true;
  } else {
    // TODO-HW: real ranging. height = tof - standoff; if commanded-moving but tof stops
    // changing (or the "all the way up" limit line asserts) => at a limit => motion_=STOP.
    tofMm_ = readTofMm_();
    tofOk_ = (tofMm_ > 1.0f);
    heightMm_ = tofMm_ - TOF_STANDOFF;
  }
  atBot_ = heightMm_ <= MIN_MM + 0.5f;
  atTop_ = heightMm_ >= MAX_MM - 0.5f;

  closePulses_(nowMs);
  xSemaphoreGive(mtx_);
}

// High-priority safety pass: belt-and-suspenders — if inhibited yet still moving, force stop.
void LiftController::enforceSafe(uint32_t nowMs) {
  if (!mtx_) return; xSemaphoreTake(mtx_, portMAX_DELAY);
  if (inhibit_ && motion_ != Motion::STOP) {
    if (motion_ == Motion::UP) pulseRelay_(0, nowMs); else pulseRelay_(1, nowMs);
    motion_ = Motion::STOP;
  }
  xSemaphoreGive(mtx_);
}

LiftController::Snapshot LiftController::snapshot() {
  Snapshot s{};
  if (!mtx_) return s;
  xSemaphoreTake(mtx_, portMAX_DELAY);
  uint32_t now = millis();
  s.heightMm = heightMm_; s.tofMm = tofMm_; s.motion = motion_; s.inhibit = inhibit_;
  s.atTop = atTop_; s.atBot = atBot_; s.tofOk = tofOk_;
  s.relayUpPulse = upPulseUntil_ && now < upPulseUntil_;
  s.relayDnPulse = dnPulseUntil_ && now < dnPulseUntil_;
  xSemaphoreGive(mtx_);
  return s;
}
