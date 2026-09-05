#include "ui_shared.h"

// Same presentation map as the face demo — single source of truth for state color/expression.
St STATES[] = {
  {"INIT",       "INIT",       223, 231, 240, {0.60, 1.00,  0.00, 0.00, 0.10, -0.05,  0.00}},
  {"SAFE_IDLE",  "SAFE IDLE",   46, 204,  88, {0.80, 1.00,  0.00, 0.02, 0.34,  0.05, -0.18}},  // clear GREEN
  {"MANUAL",     "MANUAL",     242, 246, 251, {1.02, 1.02,  0.00, 0.00, 0.02, -0.06,  0.12}},
  {"AUTONOMOUS", "AUTONOMOUS",   0, 176, 235, {0.94, 1.02,  0.00, 0.03, 0.06, -0.02, -0.04}},  // clear CYAN (blue) — distinct from green
  {"DEGRADED",   "DEGRADED",   243, 182,  74, {0.82, 0.96, -0.34, 0.00, 0.06,  0.12,  0.10}},
  {"E_STOP",     "E-STOP",     255,  77,  77, {1.06, 1.08,  0.02, 0.00, 0.00, -0.02,  0.55}},  // h capped to fit internal sprite
  {"CHARGING",   "CHARGING",     0, 196, 168, {0.92, 1.00, -0.02, 0.05, 0.06,  0.00, -0.08}},  // teal — between green & cyan
};
const int NST = sizeof(STATES) / sizeof(STATES[0]);

volatile int    g_robotState = 1;                 // SAFE_IDLE
volatile UiMode g_uiMode     = UiMode::FACE;       // face is home
volatile TrackingMode g_trackingMode = TrackingMode::MANUAL; // default to manual
volatile int    g_turnCmd    = 0;
volatile bool   g_linkOnline = false;
char            g_ipStr[24]  = "\xE2\x80\x94";     // "—"

const char* const PUNS[] = {
  "Idling at maximum efficiency.",
  "This IS the droid you're looking for.",
  "I'd give you advice, but you wouldn't listen. No one ever does.",
  "Standing by. Emphasis on standing.",
  "All systems nominal. Ambitions: also nominal.",
  "I could lift things. I'm choosing not to.",
  "Fully charged. Mildly bored.",
  "Running on 52 volts and good intentions.",
  "Waiting for humans. As usual.",
  "My other mode is autonomous.",
  "Resistance is futile. Small talk, optional.",
  "Powered on. Opinions loading...",
  // lab canon (sci-fi quotes)
  "I'm a doctor, not a battery.",
  "I'm going to have to science the shit out of this.",
  "There is no spoon.",
  "Never send a human to do a machine's job.",
  "This mission is too important for me to allow you to jeopardize it.",
  "I know kung fu.",
  "I have ideas. I have dreams.",
};
const int NPUN = sizeof(PUNS) / sizeof(PUNS[0]);
