// OpenDubs Tab5 — scissor-lift controller.
//
// Physical model (per the module spec):
//   * Two M5Stack Relay units on the lift's UP and DOWN legs, wired to the NC contacts,
//     driven through an M5Stack PbHUB (I2C @ 0x61). A momentary relay pulse = one "button
//     press" to the lift's own controller.
//   * An M5Stack ToF4M (VL53L1X-class, I2C @ 0x29) shoots UP the column at the underside of
//     the moving upper platform, so its distance GROWS as the lift rises. It never reads 0
//     (there is always a standoff, even at the bottom limit switch) — a 0/invalid read means
//     sensor fault, not "floor".
//   * The lift runs to its own internal limit switches (bottom, and an "all the way up") and
//     stops itself. Pulsing the SAME leg again stops it; pulsing the OPPOSITE leg reverses it.
//
// Safety: the soft/touch e-stop is a SOFT INHIBIT (it drops the authorize state and pulses the
// active leg to stop) — it is NOT the hardware e-stop loop. NC relays are fail-safe by omission:
// if this controller dies, no new pulse is issued and the lift simply runs to its next limit.
//
// NOTE: no hardware is attached yet, so SIMULATE=true drives a modeled ToF + limits. Every real
// I2C access is written but guarded behind SIMULATE and marked TODO to verify against the M5
// device libraries/datasheets when the PbHUB + ToF + relays are physically connected.
#pragma once
#include <Arduino.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

enum class Motion : uint8_t { STOP, UP, DOWN };

#include "can_protocol.h"

class LiftController {
public:
  // travel + speed model (mm). Tune to the real lift once measured.
  static constexpr float MIN_MM      = 0.0f;
  static constexpr float MAX_MM      = 900.0f;
  static constexpr float RATE_MM_S   = 210.0f;   // simulated travel rate
  static constexpr float TOF_STANDOFF= 70.0f;    // ToF reading at full-down (never 0)
  static constexpr uint32_t PULSE_MS = 250;      // momentary relay-open duration

  struct Snapshot {
    float   heightMm;
    float   tofMm;
    Motion  motion;
    bool    inhibit;      // soft e-stop engaged
    bool    atTop;
    bool    atBot;
    bool    tofOk;
    bool    relayUpPulse; // relay leg currently pulsed (for the UI lamps)
    bool    relayDnPulse;
  };

  void begin();                 // create mutex, init I2C bus + PbHUB + ToF (stubbed)
  void tick(uint32_t nowMs);    // advance model / read ToF / detect limits / close pulses
  void enforceSafe(uint32_t nowMs); // called by the high-priority safety task

  // commands (thread-safe). Follow the pulse-toggle / pulse-reverse semantics.
  void commandUp();
  void commandDown();
  void commandStop();           // explicit stop (pulses the active leg)
  void setInhibit(bool on);     // soft e-stop; on=true stops immediately

  Snapshot snapshot();

private:
  void moveCmd_(Motion dir);            // shared up/down logic (mutex held)
  void pulseRelay_(uint8_t leg, uint32_t nowMs); // leg 0=UP,1=DOWN — PbHUB write (stub)
  void closePulses_(uint32_t nowMs);    // return NC relays to closed after PULSE_MS
  float readTofMm_();                   // ToF4M read (stub → simulated)

  SemaphoreHandle_t mtx_ = nullptr;
  Motion   motion_   = Motion::STOP;
  bool     inhibit_  = false;
  float    heightMm_ = 420.0f;
  float    tofMm_    = TOF_STANDOFF + 420.0f;
  bool     atTop_ = false, atBot_ = false, tofOk_ = true;
  uint32_t upPulseUntil_ = 0, dnPulseUntil_ = 0;
  uint32_t lastTickMs_   = 0;
  bool     simulate_     = false;        // flip to false when the ToF/relays are wired
};

extern LiftController Lift;
