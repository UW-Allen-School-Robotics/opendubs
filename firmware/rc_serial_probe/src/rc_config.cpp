#include "rc_config.h"
#include <Preferences.h>
#include "telem.h"

// SBUS raw band, matching main.cpp. Defaults describe an uncalibrated transmitter.
static constexpr uint16_t kRawLo = 200, kRawMid = 1000, kRawHi = 1820;

// Historical hardcoded map, kept as the default so an uncalibrated robot behaves
// exactly as it did before this file existed:
//   CH1 ROTATE  CH2 MAST  CH3 FWD/REV  CH4 STRAFE
//   CH5 mode    CH6 deadman  CH7 mat   CH8 speed
static const RcFnCfg kDefaults[FN_COUNT] = {
    /* FN_FWD   */ {2,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_ROT   */ {0,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_STR   */ {3,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_MAST  */ {1,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_AUTH  */ {4,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_DEAD  */ {5,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_SPEED */ {7,  +1, kRawLo, kRawMid, kRawHi},
    /* FN_MAT   */ {6,  +1, kRawLo, kRawMid, kRawHi},
};

static const char *kNames[FN_COUNT] = {
    "FWD", "ROT", "STR", "MAST", "AUTH", "DEAD", "SPEED", "MAT"
};

static RcFnCfg  g_cfg[FN_COUNT];
static bool     g_calibrated = false;
static Preferences g_nvs;

// Sweep state: min/max seen per channel while the operator waggles everything.
static bool     g_sweeping = false;
static uint32_t g_sweepUntil = 0;
static uint16_t g_min[kRcChannels], g_max[kRcChannels], g_mid[kRcChannels];
static bool     g_haveMid = false;

const char *rcFnName(RcFn fn) { return (fn < FN_COUNT) ? kNames[fn] : "?"; }

bool rcFnFromName(const char *name, RcFn *out)
{
    for (uint8_t i = 0; i < FN_COUNT; ++i)
        if (!strcasecmp(name, kNames[i])) { *out = (RcFn)i; return true; }
    return false;
}

// ---------------------------------------------------------------- storage ----
static void loadDefaults()
{
    memcpy(g_cfg, kDefaults, sizeof(g_cfg));
    for (uint8_t c = 0; c < kRcChannels; ++c) { g_min[c] = kRawMid; g_max[c] = kRawMid; g_mid[c] = kRawMid; }
    g_calibrated = false;
}

void rcCfgBegin()
{
    loadDefaults();
    if (!g_nvs.begin("rccfg", true)) return;              // read-only; absent = defaults
    const size_t want = sizeof(g_cfg);
    if (g_nvs.getBytesLength("map") == want) {
        g_nvs.getBytes("map", g_cfg, want);
        g_calibrated = true;
    }
    g_nvs.end();
    tprintf("[RCCFG] %s\n", g_calibrated ? "loaded from NVS" : "no stored map — using defaults");
}

static void save()
{
    if (!g_nvs.begin("rccfg", false)) { tprintf("[RCCAL] NVS open FAILED\n"); return; }
    g_nvs.putBytes("map", g_cfg, sizeof(g_cfg));
    g_nvs.end();
    g_calibrated = true;
    tprintf("[RCCAL] saved to NVS\n");
}

bool rcCfgIsCalibrated() { return g_calibrated; }
const RcFnCfg &rcCfgGet(RcFn fn) { return g_cfg[fn]; }

// ------------------------------------------------------------------ reads ----
uint16_t rcRaw(RcFn fn, const uint16_t *ch)
{
    const RcFnCfg &c = g_cfg[fn];
    return (c.ch < 0 || c.ch >= kRcChannels) ? kRawMid : ch[c.ch];
}

float rcAxis(RcFn fn, const uint16_t *ch)
{
    const RcFnCfg &c = g_cfg[fn];
    if (c.ch < 0 || c.ch >= kRcChannels) return 0.0f;     // unassigned is inert, not surprising
    const int v = (int)ch[c.ch];
    // Scale each side of centre independently — transmitters are rarely symmetric, and
    // a stick that reaches -1.0 one way and -0.82 the other feels broken.
    float out;
    if (v >= (int)c.mid) {
        const int span = (int)c.hi - (int)c.mid;
        out = span > 0 ? (float)(v - (int)c.mid) / (float)span : 0.0f;
    } else {
        const int span = (int)c.mid - (int)c.lo;
        out = span > 0 ? -(float)((int)c.mid - v) / (float)span : 0.0f;
    }
    return constrain(out * (float)c.rev, -1.0f, 1.0f);
}

float rcUnit(RcFn fn, const uint16_t *ch)
{
    const RcFnCfg &c = g_cfg[fn];
    if (c.ch < 0 || c.ch >= kRcChannels) return 0.0f;
    const int span = (int)c.hi - (int)c.lo;
    if (span <= 0) return 0.0f;
    float u = (float)((int)ch[c.ch] - (int)c.lo) / (float)span;
    if (c.rev < 0) u = 1.0f - u;
    return constrain(u, 0.0f, 1.0f);
}

// ------------------------------------------------------------ calibration ----
void rcCalTick(const uint16_t *ch)
{
    if (!g_sweeping) return;
    for (uint8_t c = 0; c < kRcChannels; ++c) {
        if (ch[c] < g_min[c]) g_min[c] = ch[c];
        if (ch[c] > g_max[c]) g_max[c] = ch[c];
    }
    if (millis() > g_sweepUntil) {
        g_sweeping = false;
        tprintf("[RCCAL] sweep complete:\n");
        uint8_t shown = 0;
        for (uint8_t c = 0; c < kRcChannels; ++c) {
            if (g_max[c] - g_min[c] <= 50) continue;
            tprintf("   ch%-2u  %4u .. %4u  (mid %4u)\n", c + 1, g_min[c], g_max[c], g_mid[c]);
            shown++;
            delay(25);   // one datagram per line; a tight burst gets dropped
        }
        if (!shown) tprintf("   (no channel moved — is the transmitter on and bound?)\n");
        tprintf("[RCCAL] now assign functions: RCCAL SET <FWD|ROT|STR|MAST|AUTH|DEAD|SPEED|MAT>\n");
    }
}

static void showMap()
{
    tprintf("[RCCAL] map (%s)\n", g_calibrated ? "calibrated" : "DEFAULTS — not calibrated");
    for (uint8_t i = 0; i < FN_COUNT; ++i) {
        const RcFnCfg &c = g_cfg[i];
        if (c.ch < 0) tprintf("   %-5s  unassigned\n", kNames[i]);
        else tprintf("   %-5s  ch%-2d  %s  %4u/%4u/%4u\n",
                     kNames[i], c.ch + 1, c.rev < 0 ? "REV" : "   ", c.lo, c.mid, c.hi);
        delay(25);
    }
}

// Assign a function to whichever channel is furthest from its centre right now. This is
// the whole trick: the operator holds the control they mean, and the transmitter tells us
// which channel that is. The direction they were told to hold gives us reversal for free.
static void assign(RcFn fn, const uint16_t *ch)
{
    if (!g_haveMid) { tprintf("[RCCAL] run RCCAL CENTER first\n"); return; }
    int best = -1, bestDev = 0;
    for (uint8_t c = 0; c < kRcChannels; ++c) {
        const int dev = abs((int)ch[c] - (int)g_mid[c]);
        if (dev > bestDev) { bestDev = dev; best = c; }
    }
    if (best < 0 || bestDev < 150) {
        tprintf("[RCCAL] no channel moved enough (best deviation %d) — hold the control and retry\n", bestDev);
        return;
    }
    RcFnCfg &c = g_cfg[fn];
    c.ch  = (int8_t)best;
    c.mid = g_mid[best];
    // A sweep gives real endpoints; without one, fall back to the nominal band so the
    // assignment is still usable.
    c.lo  = (g_max[best] - g_min[best] > 50) ? g_min[best] : kRawLo;
    c.hi  = (g_max[best] - g_min[best] > 50) ? g_max[best] : kRawHi;
    // Operator was asked to hold the POSITIVE direction, so a negative deflection means
    // the channel is reversed.
    c.rev = ((int)ch[best] >= (int)g_mid[best]) ? +1 : -1;
    tprintf("[RCCAL] %s -> ch%d %s  (%u/%u/%u)\n",
                  kNames[fn], best + 1, c.rev < 0 ? "REVERSED" : "normal", c.lo, c.mid, c.hi);
}

bool rcCalCommand(const char *cmd, const uint16_t *ch)
{
    if (strncasecmp(cmd, "RCCAL", 5) != 0) return false;
    const char *arg = cmd + 5;
    while (*arg == ' ') arg++;

    if (!*arg || !strcasecmp(arg, "SHOW")) { showMap(); return true; }

    if (!strcasecmp(arg, "HELP")) {
        tprintf("[RCCAL] *** DO THIS WITH THE E-STOP PRESSED *** nothing needs to move to\n");
        tprintf("[RCCAL] read a transmitter, and a half-assigned map could swap an axis.\n");
        tprintf("[RCCAL] CENTER            snapshot every channel's resting position\n");
        tprintf("[RCCAL] SWEEP [secs]      move every stick/switch to its extremes\n");
        tprintf("[RCCAL] SET <fn>          hold that control in its POSITIVE direction, then send\n");
        tprintf("[RCCAL]                   fn: FWD ROT STR MAST AUTH DEAD SPEED MAT\n");
        tprintf("[RCCAL] SAVE | DEFAULTS | SHOW\n");
        tprintf("[RCCAL] positive = forward, nose-right, strafe-right, mast UP, deadman HELD\n");
        return true;
    }

    if (!strcasecmp(arg, "CENTER")) {
        for (uint8_t c = 0; c < kRcChannels; ++c) g_mid[c] = ch[c];
        g_haveMid = true;
        tprintf("[RCCAL] centre captured. Next: RCCAL SWEEP\n");
        return true;
    }

    int secs = 0;
    if (sscanf(arg, "SWEEP %d", &secs) == 1 || !strcasecmp(arg, "SWEEP")) {
        if (secs <= 0) secs = 8;
        for (uint8_t c = 0; c < kRcChannels; ++c) { g_min[c] = ch[c]; g_max[c] = ch[c]; }
        g_sweeping = true;
        g_sweepUntil = millis() + (uint32_t)secs * 1000;
        tprintf("[RCCAL] sweeping %ds — move EVERY stick and switch to both extremes\n", secs);
        return true;
    }

    char name[16] = {0};
    if (sscanf(arg, "SET %15s", name) == 1) {
        RcFn fn;
        if (!rcFnFromName(name, &fn)) { tprintf("[RCCAL] unknown function '%s'\n", name); return true; }
        assign(fn, ch);
        return true;
    }

    if (!strcasecmp(arg, "SAVE"))     { save(); showMap(); return true; }
    if (!strcasecmp(arg, "DEFAULTS")) { loadDefaults(); tprintf("[RCCAL] reverted to defaults (not saved)\n"); showMap(); return true; }

    tprintf("[RCCAL] ? try RCCAL HELP\n");
    return true;
}
