#include "net_cmd.h"
#include "net.h"
#include "ui_hmi.h"
#include "pantilt.h"
#include "shared/clock.h"
#include "sim_inputs.h"
#include "cfg.h"
#include "mic.h"
#include "voice_net.h"
#include "ui_settings.h"

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
        // SAY WHEN NOTHING WILL BE HEARD. The cue is still recorded and still queued -- audio can
        // be switched on mid-run and the next one sounds -- but a bare "OK" here was actively
        // misleading: cuePlay()'s first statement is `if (!settingsAudioEnabled()) return`, audio
        // defaults OFF by design (the Mode tone was rejected as too loud, so it is opt-in), and a
        // sender saw a confident OK while the robot stood silent.
        //
        // That cost the choreography session an investigation on 2026-10-08: earcons sent from the
        // Thor were acked and nothing was heard, and the suspicion fell on the shipping image
        // rather than on a setting. One word in the reply would have answered it, so here it is.
        if (!settingsAudioEnabled())
            return snprintf(tx, cap, "OK cue %s (AUDIO OFF -- enable it in SETTINGS)", rx + 4);
        return snprintf(tx, cap, "OK cue %s", rx + 4);
    }

    // CFG is specified but BLOCKED: it needs the theme file format (choreography 3.4a) and the
    // storage decision (3.4b). It answers honestly rather than accepting a URL it cannot honour --
    // a config command that silently does nothing is indistinguishable from a broken one.
    // CFG is no longer blocked: the format was already specified in face-config-data-model.md,
    // and this pass installs the part of it that needs no asset cost bounds -- themes as palettes.
    if (strncmp(rx, "CFG ", 4) == 0) {
        char verdict[256];
        cfgFetch(rx + 4, verdict, sizeof(verdict));
        return snprintf(tx, cap, "%s", verdict);
    }

    // The twin's stand-ins. On the head this REFUSES rather than no-ops: a head that quietly
    // accepted a simulated state would be lying about where its state came from.
    if (strncmp(rx, "SIM ", 4) == 0) return simCommand(rx + 4, tx, cap);

    // VOICE -- push-to-talk over the wire, and the noise-floor readout.
    //
    // A UDP verb rather than a button on the glass, for now, and deliberately: the workplan needs
    // the noise floor measured WITH THE DRIVE MOTORS RUNNING, which means the measurement has to be
    // triggerable from wherever the person running the motors is standing -- not from the one place
    // where they would have to be leaning on the robot's face. It also lands on the twin for free,
    // because this parser is shared, so `VOICE PRESS` exercises the whole state machine with no
    // hardware at all.
    //
    // A touch control is still wanted and is the honest long-term trigger; it is UI work in files
    // another session is editing, so it is not bundled in here.
    if (strncmp(rx, "VOICE", 5) == 0) {
        const char* a = rx + 5;
        while (*a == ' ') ++a;
        if (strcmp(a, "PRESS") == 0)   { micPress();   return snprintf(tx, cap, "OK press | %s", micStatus()); }
        if (strcmp(a, "RELEASE") == 0) { micRelease(); return snprintf(tx, cap, "OK release | %s", micStatus()); }
        // Where the Thor is. Runtime, not compiled in: the address is per-deployment and
        // per-robot values do not belong in this repo. Not persisted -- set it after boot.
        if (strncmp(a, "THOR ", 5) == 0) {
            char host[64] = "";
            unsigned port = 0;
            if (sscanf(a + 5, "%63s %u", host, &port) != 2 || port == 0 || port > 65535)
                return snprintf(tx, cap, "ERR VOICE THOR <host> <port>");
            voiceNetSetThor(host, (uint16_t)port);
            return snprintf(tx, cap, "OK thor | %s", voiceNetStatus());
        }
        if (*a == '\0' || strcmp(a, "STATUS") == 0)
            return snprintf(tx, cap, "%s | %s", micStatus(), voiceNetStatus());
        return snprintf(tx, cap, "ERR VOICE {PRESS|RELEASE|STATUS|THOR <host> <port>}");
    }

    // GAZE -- a show aims the head. notes/workplan-choreography.md 5.6b.
    //
    // In the SHARED parser, so the face twin takes the same line the robot does and a show can be
    // rehearsed without a robot. ABSOLUTE degrees, 90 centred, because that is the cue format's
    // convention and a show knows where it wants to look -- unlike the RC stick, which sends a rate
    // and lets the head own the position.
    //
    // NOT the 0x104 absolute CAN frame, deliberately: the dashboard's YOLO tracker and the ROS head
    // bridge both already send 0x104 and are ignored today, so reviving it would silently hand the
    // head to YOLO instead of to a show.
    if (strncmp(rx, "GAZE", 4) == 0 && (rx[4] == '\0' || rx[4] == ' ')) {
        const char* a = rx + 4;
        while (*a == ' ') ++a;

        if (strcmp(a, "OFF") == 0) {
            pantiltGazeOff();
            return snprintf(tx, cap, "OK gaze off");
        }

        float pan = 0.0f, tilt = 0.0f;
        unsigned lease = 0;
        const int got = sscanf(a, "%f %f %u", &pan, &tilt, &lease);
        if (got < 2) return snprintf(tx, cap, "ERR gaze <pan_deg> <tilt_deg> [lease_ms] | OFF");

        float panOut = pan, tiltOut = tilt;
        if (!pantiltGaze(pan, tilt, lease, &panOut, &tiltOut))
            return snprintf(tx, cap, "ERR gaze no pan/tilt fitted");
        // THE CLAMPED ANGLES, not the requested ones -- a sender has no other way to see that a
        // limit bit. See pantiltGaze().
        return snprintf(tx, cap, "OK gaze %.0f %.0f", (double)panOut, (double)tiltOut);
    }

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
