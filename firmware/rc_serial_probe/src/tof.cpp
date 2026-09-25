#include "tof.h"
#include "telem.h"
#include "vl53l1x/VL53L1X.h"
#include <Preferences.h>

static constexpr uint8_t  kTofI2CAddr = 0x29;
static constexpr uint32_t kTofHz      = 400000;
static constexpr uint32_t kPeriodMs   = 50;    // sensor's continuous-ranging period
static constexpr uint32_t kPollMs     = 40;    // how often we ASK if a sample is ready
static constexpr uint32_t kStaleMs    = 500;   // a height older than this is not a height
static constexpr uint32_t kRetryMs    = 2000;  // re-init cadence when the sensor is absent

// BEAM-BREAK REJECTION. A hand passing through the beam returns a short range, so the
// reported height collapses instantly and recovers just as fast. Downstream that is
// indistinguishable from the lift falling — and it drove the lift, because the pinch
// backstop read it as a runaway and pressed a leg to "stop" it.
//
// The lift physically travels at roughly 8-20 mm/s. Anything implying a wild multiple of
// that did not happen, so it is discarded rather than believed. The bound is deliberately
// generous: this rejects the impossible, not merely the surprising.
static constexpr float    kMaxMmPerSec = 300.0f;
static constexpr float    kJumpFloorMm = 15.0f;   // always allow at least this (sensor noise)
// ... but never reject forever. If the mechanism really did move while the beam was
// obstructed, the first clear reading is a big step and would be rejected in perpetuity.
// After this long with nothing accepted, take the next sample as truth and re-sync.
static constexpr uint32_t kResyncMs   = 1500;

static HubI2C  *g_shim = nullptr;
static VL53L1X  g_dev;

static bool     g_ready   = false;   // init succeeded and ranging is running
static uint32_t g_nextMs  = 0;
static uint16_t g_lastMm  = 0;
static uint8_t  g_lastSt  = 255;
static uint32_t g_lastOkMs = 0;
static uint32_t g_reads = 0, g_errors = 0, g_rejects = 0;
// Last RAW distance, whatever its status — diagnostics only ([TOF] d=, tofRaw()). The height
// source is g_lastMm, which only ever takes a sample the sensor called valid.
static uint16_t g_lastRawMm = 0;
// No part of this machine is 1.5 m up. A height above this means the reading or the zero is
// wrong, and a wrong height is worse than none: it feeds the pinch guard.
static constexpr float kMaxHeightMm = 1500.0f;

// ---- calibration (per robot, NVS) ----
static bool     g_haveZero = false;
static uint16_t g_zeroMm   = 0;      // distance reading with the lift at its bottom limit
static bool     g_haveTop  = false;
static uint16_t g_topMm    = 0;
static Preferences g_nvs;

static void calLoad()
{
    if (!g_nvs.begin("tofcal", true)) return;
    g_haveZero = g_nvs.isKey("zero");
    g_zeroMm   = g_nvs.getUShort("zero", 0);
    g_haveTop  = g_nvs.isKey("top");
    g_topMm    = g_nvs.getUShort("top", 0);
    g_nvs.end();
}

static void calSave()
{
    if (!g_nvs.begin("tofcal", false)) { tprintf("[TOFCAL] NVS open FAILED\n"); return; }
    if (g_haveZero) g_nvs.putUShort("zero", g_zeroMm); else g_nvs.remove("zero");
    if (g_haveTop)  g_nvs.putUShort("top",  g_topMm);  else g_nvs.remove("top");
    g_nvs.end();
}

// Height is signed distance from the bottom-limit reading. The SIGN is not assumed: which
// way the number moves as the lift rises depends on where the sensor is mounted and what it
// is aimed at, so it comes from the TOP capture. With no TOP we assume rising distance =
// rising height, and say so, because a silently inverted axis is worse than an unknown one.
static int sign()
{
    if (!g_haveTop) return +1;
    return (g_topMm >= g_zeroMm) ? +1 : -1;
}

void tofBegin(m5::I2C_Class **bus, bool (*select)())
{
    static HubI2C shim(bus, kTofI2CAddr, select, kTofHz);
    g_shim = &shim;
    g_dev.setBus(g_shim);
    g_dev.setTimeout(50);      // bounded: a hung sensor must not take the control loop with it
    calLoad();
    tprintf("[TOF] %s\n", g_haveZero ? "calibration loaded from NVS"
                                     : "NOT CALIBRATED — run TOFCAL ZERO at the bottom limit");
}

// One transaction per call, at most. Init is retried in the background rather than done once
// at boot, so plugging the sensor in (or power-cycling it) brings it back with no reflash.
void tofTick(uint32_t nowMs)
{
    if (g_shim == nullptr || (int32_t)(nowMs - g_nextMs) < 0) return;

    if (!g_ready) {
        g_nextMs = nowMs + kRetryMs;
        if (!g_dev.init()) return;
        g_dev.setDistanceMode(VL53L1X::Long);       // the lift's travel is longer than Short covers
        g_dev.setMeasurementTimingBudget(kPeriodMs * 1000);
        g_dev.startContinuous(kPeriodMs);
        g_ready = true;
        tprintf("[TOF] sensor initialised — continuous ranging at %lums\n", (unsigned long)kPeriodMs);
        return;
    }

    g_nextMs = nowMs + kPollMs;
    if (!g_dev.dataReady()) return;
    const uint16_t mm = g_dev.read(false);          // false = do not block waiting
    if (g_dev.timeoutOccurred()) {
        g_errors++;
        g_ready = false;                            // fall back to re-init; the bus may have wedged
        g_nextMs = nowMs + kRetryMs;
        return;
    }
    g_lastSt = (uint8_t)g_dev.ranging_data.range_status;
    g_lastRawMm = mm;
    g_reads++;

    // A sample the sensor did not call valid NEVER becomes the height. It used to fall past the
    // gate below (which only tests valid samples) and get stored anyway, so a single error
    // reading of 65535 became a height of 65535 - zero, and tofHeightMm() served that for up to
    // kStaleMs. Seen in service as a 64260 mm "height" — to the pinch guard, a lift nowhere near
    // the bottom. Holding the last good height until it goes stale is the honest answer.
    if (g_dev.ranging_data.range_status != VL53L1X::RangeValid) {
        g_errors++;
        return;
    }

    // Plausibility gate, applied before anything downstream sees the value.
    if (g_lastOkMs != 0) {
        const uint32_t dtMs = nowMs - g_lastOkMs;
        const float allow = kJumpFloorMm + kMaxMmPerSec * (float)dtMs / 1000.0f;
        if (fabsf((float)mm - (float)g_lastMm) > allow) {
            g_rejects++;
            if ((nowMs - g_lastOkMs) < kResyncMs) return;      // discard: nothing moved that fast
            // Obstructed long enough that our reference is untrustworthy. Accept and re-seed
            // rather than reject for ever.
            tprintf("[TOF] re-sync after %lums of rejected samples\n", (unsigned long)dtMs);
        }
    }
    // Only a clean status gets this far, so this is both the newest reading and a good one.
    g_lastMm = mm;
    g_lastOkMs = nowMs;
}

bool tofRaw(uint16_t *mm, uint8_t *status)
{
    if (mm) *mm = g_lastRawMm;   // raw, including bad-status samples — this is the diagnostic view
    if (status) *status = g_lastSt;
    return g_ready;
}

bool tofCalibrated() { return g_haveZero; }
uint32_t tofReads()  { return g_reads; }
uint32_t tofErrors() { return g_errors; }

bool tofHeightMm(float *mm)
{
    if (!g_haveZero || g_lastOkMs == 0) return false;
    if (millis() - g_lastOkMs > kStaleMs) return false;
    const float h = (float)((int)g_lastMm - (int)g_zeroMm) * (float)sign();
    if (h > kMaxHeightMm) return false;  // impossible: bad reading or a bad zero, not a height
    if (mm) *mm = h < 0.0f ? 0.0f : h;   // below the bottom limit is not a real place
    return true;
}

void tofPublish()
{
    float h;
    const bool haveH = tofHeightMm(&h);
    // rej = samples discarded as physically impossible. A hand through the beam shows up
    // here; a climbing count with the lift still means something is interrupting it.
    tprintf("[TOF] rdy=%d d=%u st=%u cal=%d h=%.0f reads=%lu err=%lu rej=%lu\n",
            g_ready ? 1 : 0, g_lastRawMm, g_lastSt, g_haveZero ? 1 : 0,
            haveH ? h : -1.0f, (unsigned long)g_reads, (unsigned long)g_errors,
            (unsigned long)g_rejects);
}

static void calShow()
{
    tprintf("[TOFCAL] %s  zero=%u%s top=%u%s sign=%+d\n",
            g_haveZero ? "calibrated" : "NOT CALIBRATED",
            g_zeroMm, g_haveZero ? "" : "(unset)",
            g_topMm,  g_haveTop  ? "" : "(unset)", sign());
    delay(25);
    if (g_haveZero && g_haveTop)
        tprintf("[TOFCAL] travel = %d mm\n", abs((int)g_topMm - (int)g_zeroMm));
    float h;
    tprintf("[TOFCAL] now: d=%u st=%u  height=%s\n", g_lastMm, g_lastSt,
            tofHeightMm(&h) ? String(h, 0).c_str() : "unknown");
}

bool tofCalCommand(const char *cmd)
{
    if (strncasecmp(cmd, "TOFCAL", 6) != 0) return false;
    const char *arg = cmd + 6;
    while (*arg == ' ') arg++;

    if (!*arg || !strcasecmp(arg, "SHOW")) { calShow(); return true; }

    if (!strcasecmp(arg, "HELP")) {
        tprintf("[TOFCAL] ZERO   lift at its BOTTOM limit — this distance becomes height 0\n");
        delay(25);
        tprintf("[TOFCAL] TOP    lift at the TOP — fixes the sign and records the travel\n");
        delay(25);
        tprintf("[TOFCAL] SAVE | CLEAR | SHOW\n");
        return true;
    }

    const bool zero = !strcasecmp(arg, "ZERO");
    if (zero || !strcasecmp(arg, "TOP")) {
        // Refuse to capture a reading the sensor itself does not vouch for. A calibration
        // taken from a bad sample is silently wrong forever after.
        if (!g_ready)                         { tprintf("[TOFCAL] sensor not ready\n"); return true; }
        if (g_lastSt != VL53L1X::RangeValid)  { tprintf("[TOFCAL] last read status=%u (not valid) — aim/light check\n", g_lastSt); return true; }
        if (millis() - g_lastOkMs > kStaleMs) { tprintf("[TOFCAL] no fresh reading\n"); return true; }
        if (zero) { g_zeroMm = g_lastMm; g_haveZero = true; }
        else      { g_topMm  = g_lastMm; g_haveTop  = true; }
        tprintf("[TOFCAL] %s = %u mm captured (not saved yet — TOFCAL SAVE)\n",
                zero ? "ZERO" : "TOP", g_lastMm);
        return true;
    }

    if (!strcasecmp(arg, "SAVE"))  { calSave(); tprintf("[TOFCAL] saved to NVS\n"); calShow(); return true; }
    if (!strcasecmp(arg, "CLEAR")) { g_haveZero = g_haveTop = false; calSave(); tprintf("[TOFCAL] cleared\n"); return true; }

    tprintf("[TOFCAL] ? try TOFCAL HELP\n");
    return true;
}
