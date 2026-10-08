// OpenDubs Tab5 — social attention: notice a person, look at them, acknowledge them.
#pragma once
#include <cstdint>

// The smallest complete interaction the robot can have: something makes a sound, the eyes go to
// it, and a short cue says "heard you". Three HRI findings are built in rather than bolted on:
//
//  * GAZE IS THE PRIMARY SOCIAL SIGNAL, and turning toward a speaker is the convention humans
//    already use and therefore read correctly without being taught.
//  * MULTIPLE SHORT GLANCES BEAT ONE LONG STARE -- people read brief repeated fixations more
//    accurately than sustained gaze. That is also the only thing our sensing can honestly support:
//    the microphone resolves left/right/centre and a level ratio is noisy, so a glance that is
//    allowed to be approximate and then end hides jitter that continuous tracking would expose.
//    This is why the robot GLANCES toward a voice and does not track it.
//  * ACKNOWLEDGE INSIDE ABOUT A SECOND, whether or not anything can be done. Silence is read as
//    being ignored, or as broken.
//
// It deliberately does NOT imply understanding. The cue is one blip, never speech-shaped: the
// robot heard a sound from a direction, and that is all it should ever claim.
void attentionBegin();
void attentionTick(uint32_t now);
