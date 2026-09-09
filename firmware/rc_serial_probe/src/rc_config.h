// Per-robot RC channel configuration — which physical channel drives which function,
// which way round it is, and where its endpoints sit.
//
// WHY THIS EXISTS. The channel map used to be eight hardcoded indices, and every time a
// new mechanism appeared we asked the same question again in a new place: "which way is
// CH2 up for the mast?", "is CH6 deadman active high?", "is strafe reversed?". Those are
// all one question — how is this transmitter set up — and they belong in one place,
// answered once, by someone who does not need to know which subsystem cares.
//
// It also removes a class of per-robot value from the repo. Channel assignment and
// reversal depend on the transmitter, not on the design, so they belong on the robot.
// The defaults here reproduce the historical hardcoded map exactly, so an uncalibrated
// robot behaves as it always did.
//
// Modelled on ArduPilot's RC calibration: sweep to learn endpoints, then assign each
// function by moving its control and seeing which channel answers.
#pragma once
#include <Arduino.h>

enum RcFn : uint8_t {
    FN_FWD = 0,   // forward / reverse       axis
    FN_ROT,       // rotate                  axis
    FN_STR,       // strafe                  axis
    FN_MAST,      // Z-axis / lift           axis
    FN_AUTH,      // authority (OFF/RC/AUTO) switch, 3-position
    FN_DEAD,      // deadman                 switch, 2-position
    FN_SPEED,     // speed knob              unipolar 0..1
    // Payload mode, 3-position: OFF / Z-AXIS / PAN-TILT. Selects what the RIGHT STICK means,
    // because there are two sticks and three things that want one. Renamed IN PLACE from the
    // never-used FN_MAT: the stored map is a raw byte image of this array, so adding a
    // function would change its size and silently drop every robot back to defaults —
    // including a calibrated MAST reversal. A rename costs nothing.
    FN_PMODE,
    FN_COUNT
};

static constexpr uint8_t kRcChannels = 16;

struct RcFnCfg {
    int8_t   ch;       // 0-based channel index; -1 = unassigned
    int8_t   rev;      // +1 normal, -1 reversed
    uint16_t lo, mid, hi;
};

const char *rcFnName(RcFn fn);
bool        rcFnFromName(const char *name, RcFn *out);

// Load from NVS, falling back to the historical defaults if nothing is stored.
void rcCfgBegin();
bool rcCfgIsCalibrated();          // false = running on defaults
const RcFnCfg &rcCfgGet(RcFn fn);

// ---- reads (all take the live channel array) ----
// Bipolar axis, -1..+1, endpoint-scaled and reversal-corrected. Returns 0 if unassigned,
// so an unconfigured function is inert rather than surprising.
float    rcAxis(RcFn fn, const uint16_t *ch);
// Unipolar 0..1, for knobs.
float    rcUnit(RcFn fn, const uint16_t *ch);
// Raw channel value, for switches whose thresholds live in the caller.
uint16_t rcRaw(RcFn fn, const uint16_t *ch);

// ---- calibration ----
// Handles "RCCAL ..." and returns true if it consumed the command. Anything else is
// left alone so the caller's other commands still work.
bool rcCalCommand(const char *cmd, const uint16_t *ch);
// Call at ~50 Hz while a sweep is running so endpoints keep accumulating.
void rcCalTick(const uint16_t *ch);
