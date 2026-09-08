// OpenDubs Tab5 — scissor-lift PROXY, and the front rangefinder.
//
// This object does NOT drive the lift. It used to: two relays on a PbHUB, pulsed directly
// from here. That made the Tab5 a second, independent owner of the lift's buttons, alongside
// the StampPLC — and the StampPLC is where the Z-axis safety lives (pinch guard, limit
// detection, belief correction against a calibrated ToF). Two owners means either can move
// the lift without the other knowing, and the guard can be walked straight past.
//
// So the split is now:
//
//   Tab5        asks. The HMI's up/down/stop buttons set a REQUEST, which the comms task
//               sends to the Thor as CAN_ID_LIFT_CMD.
//   Thor        forwards it to the StampPLC as `MAST CMD <-1..1>`.
//   StampPLC    acts, through ScissorRelayMast — the same path the RC sticks use, so the
//               pinch guard applies to every descent regardless of who asked for it.
//
// Height, motion and limits are therefore REPORTED state, not something this object knows.
// They were previously simulated here — a modelled height advanced by a rate constant — and
// displayed on the HMI as though measured. A plausible-looking fake number next to a real
// machine is worse than an honest "unknown", so unknown is what it now says.
//
// The ToF on this unit is the FRONT rangefinder (CAN_ID_TOF_FRONT). It is not the lift's
// height sensor; that one is on the StampPLC's PaHub. The old comments here claimed it shot
// up the lift column, which was never true of this build.
#pragma once
#include <Arduino.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

enum class Motion : uint8_t { STOP, UP, DOWN };

#include "can_protocol.h"

class LiftController {
public:
  // Display range for the HMI's height bar. The real travel is a per-robot commissioned
  // value on the StampPLC; this is only how the bar is scaled.
  static constexpr float MIN_MM = 0.0f;
  static constexpr float MAX_MM = 900.0f;

  struct Snapshot {
    float   heightMm;      // reported by the StampPLC; < 0 when unknown
    bool    heightKnown;
    float   tofMm;         // FRONT rangefinder, not lift height
    Motion  motion;        // reported
    bool    inhibit;       // soft e-stop engaged (refuses to send requests)
    bool    atTop;
    bool    atBot;
    bool    tofOk;
    bool    guardHold;     // StampPLC pinch guard is refusing a descent
    bool    relayUpPulse;  // "up request in flight" lamp
    bool    relayDnPulse;  // "down request in flight" lamp
  };

  void begin();
  void tick(uint32_t nowMs);
  void enforceSafe(uint32_t nowMs);

  // Requests, not commands. Thread-safe.
  void commandUp();
  void commandDown();
  void commandStop();
  void setInhibit(bool on);     // soft e-stop; on=true requests stop and refuses further asks

  // What the comms task should be telling the Thor. STOP is meaningful and must be sent:
  // silence at the far end is treated as stop too, but saying so is faster than timing out.
  Motion request();

  // Lift state as reported back by the robot. Marks the report fresh; a report older than
  // REPORT_STALE_MS makes height unknown again rather than leaving a stale number on screen.
  void reportFromRobot(float heightMm, Motion motion, bool atTop, bool atBot, bool guardHold);

  Snapshot snapshot();

  static constexpr uint32_t REPORT_STALE_MS = 1500;

private:
  bool  readTofMm_(float *mm);   // true only on a reading the sensor vouches for

  SemaphoreHandle_t mtx_ = nullptr;
  Motion   request_   = Motion::STOP;
  uint32_t requestMs_ = 0;
  bool     inhibit_   = false;

  // Reported from the StampPLC.
  Motion   motion_     = Motion::STOP;
  float    heightMm_   = -1.0f;
  bool     atTop_ = false, atBot_ = false, guardHold_ = false;
  uint32_t reportMs_   = 0;

  float    tofMm_ = -1.0f;
  bool     tofOk_ = false;
  uint32_t lastTickMs_ = 0;
};

extern LiftController Lift;
