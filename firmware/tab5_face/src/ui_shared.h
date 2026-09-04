// OpenDubs Tab5 — shared UI state: the robot-state presentation map (used by both the
// full face and the collapsed controller chip), the UI mode, and the SAFE_IDLE puns.
#pragma once
#include <M5Unified.h>
#include <stddef.h>

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

// turn-signal (movement intent) pushed by the Thor: -1 left, +1 right, 0 straight → face chevrons
extern volatile int g_turnCmd;

// link/identity (set by commsTask once WiFi/CAN is up; stubbed OFFLINE for now)
extern volatile bool g_linkOnline;
extern char g_ipStr[24];
void setIpStr(const char* ip);
void clearIpStr();
void copyIpStr(char* out, size_t outSize);

// SAFE_IDLE pun ticker (humor 60%, discretion 100% — robot/sci-fi, clean)
extern const char* const PUNS[];
extern const int NPUN;

// convenience
static inline bool stateIs(int idx, const char* n) { return strcmp(STATES[idx].name, n) == 0; }
