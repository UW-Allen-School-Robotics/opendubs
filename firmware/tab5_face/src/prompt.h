// OpenDubs Tab5 — say out loud what the Z-axis pinch guard is waiting for.
//
// The guard stops a descent near the bottom and will not resume it until the operator RELEASES
// (stick centred, or button let go) for the dwell and then asks DOWN again. Every part of that
// was invisible: the PRISM turns red, which says "held" but not "here is how to proceed", and
// the 600 ms dwell had no feedback at all. Operators learned it as "a just-right flick of the
// stick", which is the sound of a rule nobody can see.
//
// This adds no authority and changes no safety logic. It reads the guard flag the head ALREADY
// receives (LIFT frame, bit 2 -> LiftController::Snapshot::guardHold) and turns it into a banner
// and a tone. If it is wrong, the lift still behaves exactly the same.
#pragma once
#include <stdint.h>

void promptTick(uint32_t nowMs);   // state, sounds, timers — call every loop
void promptRender(uint32_t nowMs); // draws over the current screen when active — call AFTER the
                                   // mode renderer (faceRender/hmiRender), same task
bool promptActive();               // is the banner currently owning the top of the screen
