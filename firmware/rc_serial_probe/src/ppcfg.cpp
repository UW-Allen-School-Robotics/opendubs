#include "ppcfg.h"
#include "telem.h"
#include <Preferences.h>

static constexpr uint8_t  kAddr = 0x31;
static constexpr uint32_t kHz   = 100000;   // matches the read path: 3.3/5 V shifter, slow edges

// Register indices, from goBILDA's Arduino library.
static constexpr uint8_t kRegDeviceControl = 4;
static constexpr uint8_t kRegMmPerTick     = 14;
static constexpr uint8_t kRegXPodOffset    = 15;
static constexpr uint8_t kRegYPodOffset    = 16;

// DEVICE_CONTROL is a command register: each write carries the bits you want ACTED ON, and
// goBILDA's own library writes it repeatedly with unrelated bits clear, so unset bits mean
// "no change" rather than "off". Ordering follows their library: geometry first, then reset.
static constexpr uint32_t kCtlRecalibrateImu = 1u << 0;
static constexpr uint32_t kCtlResetPosAndImu = 1u << 1;
static constexpr uint32_t kCtlYBackward      = 1u << 2;
static constexpr uint32_t kCtlYForward       = 1u << 3;
static constexpr uint32_t kCtlXBackward      = 1u << 4;
static constexpr uint32_t kCtlXForward       = 1u << 5;

static constexpr float kTicks4Bar    = 19.89436789f;
static constexpr float kTicksSwingarm = 13.26291192f;

static m5::I2C_Class **g_bus = nullptr;
static bool (*g_select)() = nullptr;

struct PpCfg {
    float xOffsetMm = 0.0f;
    float yOffsetMm = 0.0f;
    float ticksPerMm = kTicks4Bar;   // the goBILDA 4-bar pod; confirm against the fitted pod
    bool  xReversed = false;
    bool  yReversed = false;
};
static PpCfg g_cfg;
static bool     g_commissioned = false;
static bool     g_applied = false;
static bool     g_wasPresent = false;
static uint32_t g_applyCount = 0;

// ---- I2C ---------------------------------------------------------------------------
// STOP on every path including failure. An abandoned transaction is what wedges this bus,
// and it is what the 8Servo investigation cost us weeks over.
static bool ppWrite(uint8_t reg, const uint8_t *d, size_t n)
{
    if (g_bus == nullptr || *g_bus == nullptr || !(*g_bus)->isEnabled()) return false;
    if (g_select != nullptr && !g_select()) return false;
    if (!(*g_bus)->start(kAddr, false, kHz)) { (*g_bus)->stop(); return false; }
    if (!(*g_bus)->write(reg))               { (*g_bus)->stop(); return false; }
    if (n && !(*g_bus)->write(d, n))         { (*g_bus)->stop(); return false; }
    return (*g_bus)->stop();
}

static bool writeFloat(uint8_t reg, float v)
{
    uint8_t b[4];
    memcpy(b, &v, 4);                 // little-endian, matching the read path
    return ppWrite(reg, b, 4);
}

static bool writeU32(uint8_t reg, uint32_t v)
{
    const uint8_t b[4] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24)};
    return ppWrite(reg, b, 4);
}

// ---- NVS ---------------------------------------------------------------------------
static void load()
{
    Preferences nvs;
    if (!nvs.begin("ppcfg", true)) return;
    g_commissioned    = nvs.isKey("xoff");
    g_cfg.xOffsetMm   = nvs.getFloat("xoff", g_cfg.xOffsetMm);
    g_cfg.yOffsetMm   = nvs.getFloat("yoff", g_cfg.yOffsetMm);
    g_cfg.ticksPerMm  = nvs.getFloat("tpm",  g_cfg.ticksPerMm);
    g_cfg.xReversed   = nvs.getBool("xrev",  g_cfg.xReversed);
    g_cfg.yReversed   = nvs.getBool("yrev",  g_cfg.yReversed);
    nvs.end();
}

static void save()
{
    Preferences nvs;
    if (!nvs.begin("ppcfg", false)) { tprintf("[PPCAL] NVS open FAILED\n"); return; }
    nvs.putFloat("xoff", g_cfg.xOffsetMm);
    nvs.putFloat("yoff", g_cfg.yOffsetMm);
    nvs.putFloat("tpm",  g_cfg.ticksPerMm);
    nvs.putBool("xrev",  g_cfg.xReversed);
    nvs.putBool("yrev",  g_cfg.yReversed);
    nvs.end();
    g_commissioned = true;
}

// ---- apply -------------------------------------------------------------------------
static bool apply(bool resetPose)
{
    bool ok = true;
    ok &= writeFloat(kRegMmPerTick, g_cfg.ticksPerMm);        delay(3);
    ok &= writeFloat(kRegXPodOffset, g_cfg.xOffsetMm);        delay(3);
    ok &= writeFloat(kRegYPodOffset, g_cfg.yOffsetMm);        delay(3);
    uint32_t dir = (g_cfg.xReversed ? kCtlXBackward : kCtlXForward)
                 | (g_cfg.yReversed ? kCtlYBackward : kCtlYForward);
    ok &= writeU32(kRegDeviceControl, dir);                   delay(3);
    if (resetPose) {
        // Zeroes the estimate AND re-runs the IMU zero-offset calibration, which the guide
        // says takes ~0.25 s and requires the robot to be STATIONARY. At boot it is.
        ok &= writeU32(kRegDeviceControl, kCtlResetPosAndImu);
        delay(5);
    }
    g_applied = ok;
    if (ok) g_applyCount++;
    return ok;
}

void ppCfgBegin(m5::I2C_Class **bus, bool (*select)())
{
    g_bus = bus;
    g_select = select;
    load();
    tprintf("[PPCFG] %s  x=%.1f y=%.1f tpm=%.3f xrev=%d yrev=%d\n",
            g_commissioned ? "loaded from NVS" : "NOT COMMISSIONED — defaults, pose is not trustworthy",
            g_cfg.xOffsetMm, g_cfg.yOffsetMm, g_cfg.ticksPerMm,
            g_cfg.xReversed ? 1 : 0, g_cfg.yReversed ? 1 : 0);
}

void ppCfgTick(uint32_t nowMs, bool present)
{
    static uint32_t retryMs = 0;
    if (present && !g_wasPresent) {
        // Appeared, or re-appeared. Either way it has just booted and is back on defaults.
        g_applied = false;
    }
    g_wasPresent = present;
    if (!present || g_applied) return;
    if ((int32_t)(nowMs - retryMs) < 0) return;
    retryMs = nowMs + 1000;
    if (apply(true))
        tprintf("[PPCFG] applied to device (#%lu)%s\n", (unsigned long)g_applyCount,
                g_commissioned ? "" : " — DEFAULTS, not commissioned");
}

bool ppCfgCommissioned() { return g_commissioned; }
bool ppCfgApplied()      { return g_applied; }
uint32_t ppCfgApplyCount() { return g_applyCount; }

// ---- commissioning commands ---------------------------------------------------------
static void show()
{
    tprintf("[PPCAL] %s, %s on device (applied %lu×)\n",
            g_commissioned ? "commissioned" : "NOT COMMISSIONED (defaults)",
            g_applied ? "live" : "NOT live", (unsigned long)g_applyCount);
    delay(25);
    tprintf("[PPCAL] xoff=%.1fmm (left +)  yoff=%.1fmm (fwd +)  ticks/mm=%.3f\n",
            g_cfg.xOffsetMm, g_cfg.yOffsetMm, g_cfg.ticksPerMm);
    delay(25);
    tprintf("[PPCAL] X pod %s, Y pod %s\n",
            g_cfg.xReversed ? "REVERSED" : "forward", g_cfg.yReversed ? "REVERSED" : "forward");
}

bool ppCfgCommand(const char *cmd)
{
    if (strncasecmp(cmd, "PPCAL", 5) != 0) return false;
    const char *arg = cmd + 5;
    while (*arg == ' ') arg++;

    if (!*arg || !strcasecmp(arg, "SHOW")) { show(); return true; }

    if (!strcasecmp(arg, "HELP")) {
        tprintf("[PPCAL] Follow the goBILDA setup order — direction FIRST, it invalidates the rest.\n");
        delay(25);
        tprintf("[PPCAL]  1. push the robot FORWARD: X must rise      -> PPCAL DIRX FWD|REV\n");
        delay(25);
        tprintf("[PPCAL]  2. push the robot LEFT:    Y must rise      -> PPCAL DIRY FWD|REV\n");
        delay(25);
        tprintf("[PPCAL]  3. PPCAL POD 4BAR|SWINGARM  (or TICKS <n>)\n");
        delay(25);
        tprintf("[PPCAL]  4. PPCAL OFFSETS <x_mm> <y_mm>   x: X pod sideways, LEFT +\n");
        delay(25);
        tprintf("[PPCAL]                                   y: Y pod forwards, FWD +\n");
        delay(25);
        tprintf("[PPCAL]  5. PPCAL SAVE, then spin in place: x/y should barely move.\n");
        delay(25);
        tprintf("[PPCAL] RESET (zero pose + IMU, robot must be STILL) | RECAL | SHOW\n");
        return true;
    }

    float x, y, t;
    if (sscanf(arg, "OFFSETS %f %f", &x, &y) == 2) {
        g_cfg.xOffsetMm = x; g_cfg.yOffsetMm = y;
        tprintf("[PPCAL] offsets x=%.1f y=%.1f (not saved — PPCAL SAVE)\n", x, y);
        g_applied = false; return true;
    }
    if (sscanf(arg, "TICKS %f", &t) == 1) {
        g_cfg.ticksPerMm = t;
        tprintf("[PPCAL] ticks/mm=%.3f (not saved)\n", t);
        g_applied = false; return true;
    }
    char w[16] = {0};
    if (sscanf(arg, "POD %15s", w) == 1) {
        if (!strcasecmp(w, "4BAR"))          g_cfg.ticksPerMm = kTicks4Bar;
        else if (!strcasecmp(w, "SWINGARM")) g_cfg.ticksPerMm = kTicksSwingarm;
        else { tprintf("[PPCAL] POD 4BAR|SWINGARM\n"); return true; }
        tprintf("[PPCAL] ticks/mm=%.3f (not saved)\n", g_cfg.ticksPerMm);
        g_applied = false; return true;
    }
    if (sscanf(arg, "DIRX %15s", w) == 1) {
        g_cfg.xReversed = (strcasecmp(w, "REV") == 0);
        tprintf("[PPCAL] X pod %s (not saved)\n", g_cfg.xReversed ? "REVERSED" : "forward");
        g_applied = false; return true;
    }
    if (sscanf(arg, "DIRY %15s", w) == 1) {
        g_cfg.yReversed = (strcasecmp(w, "REV") == 0);
        tprintf("[PPCAL] Y pod %s (not saved)\n", g_cfg.yReversed ? "REVERSED" : "forward");
        g_applied = false; return true;
    }
    if (!strcasecmp(arg, "SAVE"))  { save(); apply(false); show(); return true; }
    if (!strcasecmp(arg, "RESET")) {
        // Robot must be stationary: this re-measures the IMU zero offset, and a bad one
        // shows up as heading drifting while the robot sits still.
        tprintf("[PPCAL] reset pose + IMU %s\n",
                writeU32(kRegDeviceControl, kCtlResetPosAndImu) ? "sent" : "FAILED");
        return true;
    }
    if (!strcasecmp(arg, "RECAL")) {
        tprintf("[PPCAL] recalibrate IMU %s\n",
                writeU32(kRegDeviceControl, kCtlRecalibrateImu) ? "sent" : "FAILED");
        return true;
    }
    tprintf("[PPCAL] ? try PPCAL HELP\n");
    return true;
}
