#pragma once
#include <stddef.h>
#include <stdint.h>

/*
 * THE COMMAND PARSER, SHARED BY THE HEAD AND THE TWIN.
 *
 * The twin is the desktop SDL build of this same app, and its whole value is showing exactly what
 * the head shows. A second copy of the parser would diverge -- a verb added on one side, a reply
 * worded differently on the other -- and the twin would start lying in small ways, which is worse
 * than not having one. So the parser lives here, compiled for BOTH targets, and only the TRANSPORT
 * differs: lwIP on a Wi-Fi interface for the head, a loopback-only socket for the twin.
 *
 * It is a new file rather than a restructuring of net.cpp on purpose. net.cpp's command handling
 * sat wholly inside `#ifdef ESP_PLATFORM`, so sharing it meant moving large blocks across a
 * preprocessor boundary in a file that also carries Wi-Fi, OTA, mDNS, the StamPLC telemetry
 * listener and the lift/e-stop publisher -- and that another session is editing. Extracting into a
 * new file leaves net.cpp's existing guards alone.
 */

// Parse one line and produce the reply. Returns the reply length, or 0 for "say nothing" (the
// binary rate frame arrives 20x a second and must not be acked).
int netHandleLine(char* rx, int n, char* tx, size_t cap);

// ---- platform hooks: declared once, implemented once per target in net.cpp ----
// The platform-specific middle of the PING reply. Everything a twin cannot honestly answer --
// which slot is running, the image hash, the StamPLC address -- lives here.
int         netPlatPing(char* out, size_t cap);
bool        netPlatOta(const char* url);    // false -> "ERR not on desktop"; never a silent success
bool        netPlatReboot();                // likewise
const char* netPlatI2cScan();
const char* netPlatTof();
// The packed 0x107 pan/tilt rate frame. Returns true if this was that frame and it was consumed.
bool        netPlatRcFrame(const char* rx, int n);

// ---- robot state ----
// Written by `STATE <NAME>` on both targets, and by the StamPLC telemetry listener on the head.
// linkState() reads it, so the twin's face follows a STATE line exactly as the head's does -- which
// is what makes the twin worth having and is why this is shared rather than stubbed.
void netSetState(const char* name);
// Forget the robot's state, so linkState() reports no link again. The twin's `SIM LINK DOWN` uses
// it to get the real no-link face back without restarting.
void netClearState();

// ---- requests the parser RECORDS and the UI loop APPLIES ----
//
// The network task must never call LVGL (AUDIT invariant 1), and an emote or a theme is an LVGL
// write. So the parser only ever records an intent here and the render loop drains it under the
// lock. This is the same split `STATE` already used, generalised.
struct NetUiReq {
    int emote;   // index into kEmotes; -1 = AUTO (resume perception + cycling); -2 = no request
    int theme;   // theme::Id; -1 = no request
    int cue;     // Cue; -1 = no request
};
bool netTakeUiReq(NetUiReq* out);   // true if anything was pending; clears what it returns

// ---- name lookups, owned by the face because the tables are ----
// Implemented in app_face.cpp. The parser must not carry a second copy of these names.
int faceEmoteByName(const char* n);   // -1 unknown
int faceStateCount();
const char* faceStateName(int idx);   // "?" out of range
int faceThemeByName(const char* n);   // -1 unknown
int faceCueByName(const char* n);     // -1 unknown
