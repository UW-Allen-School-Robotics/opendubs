#include "dbg_stimulus.h"
#include "touch_ui_config.h"
#include "face_mailbox.h"
#include <cstdio>
#include <cstring>

#if OPENDUBS_TOUCH_UI && OPENDUBS_TOUCH_DEBUG

namespace {

// Eight phases of six seconds, so one full pass is 48 s and a 30-minute soak walks it ~37 times.
// Each phase exercises exactly one thing the UI could get wrong, and the phase name is on the
// panel so a glance says which case is on screen — that is the "print its own verdict" half of
// the bench rule, delivered to the only channel this head reliably has.
constexpr uint32_t kPhaseMs = 6000;
constexpr int      kPhases  = 8;

const char* kPhaseName[kPhases] = {
    "1/8 healthy, CH5 OFF        — jog must be DISABLED, reason 'CH5 is OFF'",
    "2/8 CH5 MANUAL, all fresh   — jog ENABLED: this is the window to hold it",
    "3/8 battery+thor go quiet   — those two rows must grey and show a growing age",
    "4/8 at the bottom limit     — MAST DOWN disabled, 'at the bottom limit'",
    "5/8 fault: PDH brown-out    — fault screen must take the panel, FAULT CLEAR enabled",
    "6/8 fault: e-stop latched   — ADVANCED then re-arm gate should pass (CH5 OFF, no weld)",
    "7/8 fault: contactor welded — fatal: FAULT CLEAR must be DISABLED, text says service",
    "8/8 clear, contactor open   — jog disabled 'contactor open', software stop 'already'",
};

RobotSnapshot g_s{};
uint32_t      g_next = 0;
char          g_phase[160] = "dbg: stimulus not started";
bool          g_init = false;

inline bool due(uint32_t now, uint32_t at) { return (int32_t)(now - at) >= 0; }

}  // namespace

void dbgStimulusTick(uint32_t nowMs)
{
    if (!due(nowMs, g_next)) return;
    g_next = nowMs + 50;                       // 20 Hz, the planned real status rate

    const int phase = (int)((nowMs / kPhaseMs) % kPhases);

    if (!g_init) {
        g_init = true;
        std::memset(&g_s, 0, sizeof(g_s));
        // DELIBERATELY NOT STAMPED AT BOOT: MB_NET stays invalid for the first 12 s so the IP row
        // shows "no data" rather than an empty string that could pass for a value. That is the
        // three-state distinction the whole design rests on, and it only ever happens at boot —
        // which means it is the one case a long soak would never reach again.
    }

    // Always-fresh groups.
    g_s.stateIdx = (phase == 1 || phase == 3) ? 2 : 1;
    std::snprintf(g_s.stateName, sizeof(g_s.stateName), "%s",
                  (phase == 4 || phase == 6) ? "DEGRADED"
                  : (phase == 5) ? "E_STOP"
                  : (g_s.stateIdx == 2 ? "MANUAL" : "SAFE_IDLE"));
    mbStamp(&g_s, MB_MODE, nowMs);

    g_s.estopButton        = false;
    g_s.contactorEnergized = (phase != 7);
    g_s.contactorWelded    = (phase == 6);
    g_s.motionEnabled      = g_s.contactorEnergized && !g_s.contactorWelded;
    mbStamp(&g_s, MB_ESTOP, nowMs);

    g_s.rcLive     = true;
    g_s.rcDeadman  = (phase == 1);
    g_s.rcAuthority = (phase == 1 || phase == 3) ? MB_AUTH_MANUAL : MB_AUTH_OFF;
    mbStamp(&g_s, MB_RC, nowMs);

    g_s.liftHeightMm  = (phase == 0) ? -1 : (int16_t)(120 + (nowMs / 100) % 600);
    g_s.liftMotion    = 0;
    g_s.liftAtBot     = (phase == 3);
    g_s.liftAtTop     = false;
    g_s.liftGuardHold = (phase == 3);
    mbStamp(&g_s, MB_LIFT, nowMs);

    switch (phase) {
        case 4: g_s.faultBits = MB_FAULT_PDH_BROWNOUT; break;
        case 5: g_s.faultBits = MB_FAULT_ESTOP_LATCHED; break;
        case 6: g_s.faultBits = MB_FAULT_CONTACTOR_WELDED; break;
        default: g_s.faultBits = 0; break;
    }
    mbStamp(&g_s, MB_FAULTS, nowMs);

    // THE STALENESS PHASE. Two groups simply stop being stamped, which is exactly what a dead
    // link looks like — the values stay in the struct and only the stamp stops moving. If the UI
    // greys them and counts up, the argument in tab5-dashboard.md is implemented; if they keep
    // reading as live, it is not, and that is the failure the native-vs-streamed decision was
    // made to avoid.
    if (phase != 2) {
        g_s.socPct = (uint8_t)(55 + (nowMs / 1000) % 40);
        g_s.packDv = (uint16_t)(498 + (nowMs / 700) % 30);
        g_s.packDa = (int16_t)(-40 + (int)((nowMs / 300) % 120));
        mbStamp(&g_s, MB_BATTERY, nowMs);
        mbStamp(&g_s, MB_THOR, nowMs);
    }

    if (nowMs > 12000) {
        std::snprintf(g_s.ip, sizeof(g_s.ip), "192.168.88.57");
        mbStamp(&g_s, MB_NET, nowMs);
    }

    mbSnapshotPublish(g_s);
    std::snprintf(g_phase, sizeof(g_phase), "dbg: %s", kPhaseName[phase]);
}

const char* dbgStimulusPhase() { return g_phase; }

#else

void dbgStimulusTick(uint32_t) {}
const char* dbgStimulusPhase() { return ""; }

#endif
