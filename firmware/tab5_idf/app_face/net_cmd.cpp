#include "net_cmd.h"
#include "net.h"
#include "ui_hmi.h"
#include "pantilt.h"
#include "shared/clock.h"
#include "sim_inputs.h"

#include <cstdio>
#include <cstring>

namespace {

// Robot state as last reported. 0 stamp means "never heard from the robot at all", which is NOT
// the same as a state of zero -- see applyNoLink() in app_face.cpp for why the difference matters.
char     g_stateName[24] = "";
uint32_t g_stateStamp    = 0;

// Pending UI intents. Plain volatile ints rather than a mutex: each is a single word written by one
// task and read by one task, and a request that loses a race is simply superseded by the next line.
volatile int g_reqEmote = -2;
volatile int g_reqTheme = -1;
volatile int g_reqCue   = -1;

}  // namespace

void netSetState(const char* name)
{
    snprintf(g_stateName, sizeof(g_stateName), "%.23s", name);
    g_stateStamp = perceptNowMs();
}

bool linkState(const char** nameOut, unsigned* ageMsOut)
{
    if (g_stateStamp == 0) return false;          // never heard from the robot
    if (nameOut)  *nameOut  = g_stateName;
    if (ageMsOut) *ageMsOut = (unsigned)(perceptNowMs() - g_stateStamp);
    return true;
}

void netClearState()
{
    g_stateName[0] = 0;
    g_stateStamp   = 0;
}

bool netTakeUiReq(NetUiReq* out)
{
    const int e = g_reqEmote, t = g_reqTheme, c = g_reqCue;
    if (e == -2 && t < 0 && c < 0) return false;
    if (out) { out->emote = e; out->theme = t; out->cue = c; }
    g_reqEmote = -2; g_reqTheme = -1; g_reqCue = -1;
    return true;
}

const char* netStateName() { return g_stateName[0] ? g_stateName : "-"; }

int netHandleLine(char* rx, int n, char* tx, size_t cap)
{
    // The binary rate frame first: it is not a line, it arrives 20x a second, and it gets no reply.
    if (netPlatRcFrame(rx, n)) return 0;

    if (strcmp(rx, "PING") == 0) {
        char plat[224] = "";
        netPlatPing(plat, sizeof(plat));
        return snprintf(tx, cap, "PONG %s st=%s | %s | %s",
                        plat, netStateName(), hmiDiag(), pantiltStatus());
    }

    if (strncmp(rx, "STATE ", 6) == 0) {
        netSetState(rx + 6);
        return snprintf(tx, cap, "OK state %s", netStateName());
    }

    // ---- the verbs that only RECORD an intent ----
    if (strncmp(rx, "EMOTE ", 6) == 0) {
        const char* a = rx + 6;
        if (strcmp(a, "AUTO") == 0) {
            g_reqEmote = -1;                       // resume perception + random cycling
            return snprintf(tx, cap, "OK emote AUTO");
        }
        const int idx = faceEmoteByName(a);
        if (idx < 0) return snprintf(tx, cap, "ERR unknown emote");
        g_reqEmote = idx;
        return snprintf(tx, cap, "OK emote %s", a);
    }

    if (strncmp(rx, "THEME ", 6) == 0) {
        const int idx = faceThemeByName(rx + 6);
        if (idx < 0) return snprintf(tx, cap, "ERR unknown theme");
        g_reqTheme = idx;
        return snprintf(tx, cap, "OK theme %s", rx + 6);
    }

    if (strncmp(rx, "CUE ", 4) == 0) {
        const int idx = faceCueByName(rx + 4);
        if (idx < 0) return snprintf(tx, cap, "ERR unknown cue");
        g_reqCue = idx;
        return snprintf(tx, cap, "OK cue %s", rx + 4);
    }

    // CFG is specified but BLOCKED: it needs the theme file format (choreography 3.4a) and the
    // storage decision (3.4b). It answers honestly rather than accepting a URL it cannot honour --
    // a config command that silently does nothing is indistinguishable from a broken one.
    if (strncmp(rx, "CFG ", 4) == 0)
        return snprintf(tx, cap, "ERR not implemented -- awaiting the theme file format");

    // The twin's stand-ins. On the head this REFUSES rather than no-ops: a head that quietly
    // accepted a simulated state would be lying about where its state came from.
    if (strncmp(rx, "SIM ", 4) == 0) return simCommand(rx + 4, tx, cap);

    if (strcmp(rx, "TOF") == 0)     return snprintf(tx, cap, "%s", netPlatTof());
    if (strcmp(rx, "I2CSCAN") == 0) return snprintf(tx, cap, "%s", netPlatI2cScan());

    if (strcmp(rx, "REBOOT") == 0) {
        if (!netPlatReboot()) return snprintf(tx, cap, "ERR not on desktop");
        return snprintf(tx, cap, "OK reboot");      // the head does not come back to send this
    }

    if (strncmp(rx, "OTA ", 4) == 0) {
        if (!netPlatOta(rx + 4)) return snprintf(tx, cap, "ERR not on desktop");
        return snprintf(tx, cap, "OK ota");
    }

    return snprintf(tx, cap, "ERR unknown");
}
