#pragma once
#include <stdint.h>
#include <stddef.h>
#include <cstdio>

struct GazeIntent;

/*
 * SIMULATED INPUTS, FOR THE TWIN ONLY.
 *
 * A twin has no robot: no StamPLC telling it a state, no camera finding a person, no ToF. Without
 * stand-ins the twin can only ever be shown doing nothing, which makes it useless for judging a
 * show. So each missing input gets one here -- settable from the keyboard for a person sitting in
 * front of it, and by command so the choreography bridge or a script can drive it.
 *
 * THE TWIN SAYS SO ON SCREEN, unmissably. Everything below exists to make the twin behave like the
 * head, which is exactly what makes mistaking one for the other easy -- and a simulated e-stop that
 * someone believes is a real one is the worst outcome this file could produce.
 *
 * NOTHING HERE REACHES THE DEVICE IMAGE. The implementation is guarded in sim_inputs.cpp, because
 * the vendor's CMake globs sources recursively and a file cannot be excluded by living in a
 * different directory. On the device these are inline no-ops, except simCommand(), which REFUSES --
 * a head that silently accepted a simulated input would be lying about where its state came from.
 */

#ifndef ESP_PLATFORM

void simBegin(void* screen);          // lv_obj_t*; void* keeps LVGL out of this header
void simTick(uint32_t nowMs);
bool simGaze(GazeIntent* out);        // the fake person, if one is switched on
bool simTofMm(float* mm);             // the simulated front distance, if one is set
bool simEstop();                      // drives netEstopPending() on the twin
int  simCommand(const char* arg, char* tx, size_t cap);   // a `SIM ...` line
// For PING. A keyboard press is otherwise unobservable from outside the process, and "the label
// looks right" is a report about a label rather than a check of the state behind it.
int  simReport(char* out, size_t cap);

#else   // the head

inline void simBegin(void*)            {}
inline void simTick(uint32_t)          {}
inline bool simGaze(GazeIntent*)       { return false; }
inline bool simTofMm(float*)           { return false; }
inline bool simEstop()                 { return false; }
inline int  simCommand(const char*, char* tx, size_t cap)
{
    return snprintf(tx, cap, "ERR not on the head -- SIM is twin-only");
}

#endif
