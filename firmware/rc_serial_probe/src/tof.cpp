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

static HubI2C  *g_shim = nullptr;
static VL53L1X  g_dev;

static bool     g_ready   = false;   // init succeeded and ranging is running
static uint32_t g_nextMs  = 0;
static uint16_t g_lastMm  = 0;
static uint8_t  g_lastSt  = 255;
static uint32_t g_lastOkMs = 0;
static uint32_t g_reads = 0, g_errors = 0;

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
    g_lastMm = mm;
    g_lastSt = (uint8_t)g_dev.ranging_data.range_status;
    g_reads++;
    // Only a clean status counts as a reading. A wrong height is worse than no height: it
    // feeds the pinch guard, and a bad number there either blocks a safe descent or permits
    // an unsafe one.
    if (g_dev.ranging_data.range_status == VL53L1X::RangeValid) g_lastOkMs = nowMs;
    else g_errors++;
}

bool tofRaw(uint16_t *mm, uint8_t *status)
{
    if (mm) *mm = g_lastMm;
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
    if (mm) *mm = h < 0.0f ? 0.0f : h;   // below the bottom limit is not a real place
    return true;
}

void tofPublish()
{
    float h;
    const bool haveH = tofHeightMm(&h);
    tprintf("[TOF] rdy=%d d=%u st=%u cal=%d h=%.0f reads=%lu err=%lu\n",
            g_ready ? 1 : 0, g_lastMm, g_lastSt, g_haveZero ? 1 : 0,
            haveH ? h : -1.0f, (unsigned long)g_reads, (unsigned long)g_errors);
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
