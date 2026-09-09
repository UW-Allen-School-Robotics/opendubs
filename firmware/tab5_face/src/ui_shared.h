// OpenDubs Tab5 — shared UI state: the robot-state presentation map (used by both the
// full face and the collapsed controller chip), the UI mode, and the SAFE_IDLE puns.
#pragma once
#include <stddef.h>
#include <M5Unified.h>

// ---- robot-state presentation map ----
struct Expr { float h, w, tilt, bow, squint, lookY, brow; };
struct St   { const char* name; const char* label; uint8_t r, g, b; Expr e; };
extern St STATES[];
extern const int NST;

// index in STATES the FACE currently shows. Set by comms (state feed) once wired;
// demo-cycled locally until then. Plain int + volatile: single-writer, torn-read-safe enough.
extern volatile int g_robotState;

// which screen owns the panel
enum class UiMode { FACE, CONTROLLER };
extern volatile UiMode g_uiMode;

// pan/tilt control mode
enum class TrackingMode { MANUAL, AI };
extern volatile TrackingMode g_trackingMode;

// turn-signal (movement intent) pushed by the Thor: -1 left, +1 right, 0 straight → face chevrons
extern volatile int g_turnCmd;

// link/identity (set by commsTask once WiFi/CAN is up; stubbed OFFLINE for now)
extern volatile bool g_linkOnline;
// Written by commsTask (core 0), read by the UI (core 1) — always through these accessors,
// never directly. See ui_shared.cpp.
// CH7 payload mode, pushed by the Thor as "PMODE OFF|ZAXIS|PANTILT". It says what the
// operator's RIGHT STICK is currently doing, which is the thing they cannot tell by looking
// at the robot — and getting it wrong means aiming the head when you meant to strafe, or
// worse, moving the lift when you meant to drive.
enum PayloadMode : uint8_t { PMODE_OFF = 0, PMODE_ZAXIS = 1, PMODE_PANTILT = 2 };
extern volatile int g_payloadMode;

extern char g_ipStr[24];
void setIpStr(const char *ip);
void clearIpStr();
void copyIpStr(char *out, size_t outSize);

// SAFE_IDLE pun ticker (humor 60%, discretion 100% — robot/sci-fi, clean)
extern const char* const PUNS[];
extern const int NPUN;

// convenience
static inline bool stateIs(int idx, const char* n) { return strcmp(STATES[idx].name, n) == 0; }
