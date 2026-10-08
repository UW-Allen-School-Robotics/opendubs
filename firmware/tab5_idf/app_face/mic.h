// THE MICROPHONE SHIM: the one piece of the voice path that touches the HAL.
//
// Everything that DECIDES anything lives in voice/ and is HAL-free, LVGL-free and host-tested
// (`./build.sh voice`). This file is deliberately thin: it owns the capture task, calls
// audioRecord(), hands blocks to voice/listen.cpp, and turns the events that come back into the
// things the face already knows how to do. If you are looking for a threshold or a state machine,
// it is not here.
//
// BEHIND OPENDUBS_VOICE, DEFAULT OFF. Not because the code is doubtful but because this adds a
// FreeRTOS task, and on this image that is not free: a TCB must live in internal DRAM and one more
// has already asserted in xPortCheckValidTCBMem with esp-dl linked (percept/percept.h,
// idf_component.yml). Off by default means nobody else's build changes, and the flag is the one
// thing to flip when the robot is in front of you.
//
// PUSH-TO-TALK, so capture happens ONLY between micPress() and micRelease(). There is no idle
// capture at all: in a shared university building that is the privacy guarantee, and it is
// structural here rather than a policy -- the task does not call audioRecord() unless a press is
// held. See voice/listen.h for the decision and what it bought.
#pragma once
#include <stdint.h>

// Safe to call unconditionally. Returns false when voice is compiled out or the task could not be
// created, in which case everything here is a no-op and the face behaves exactly as before.
bool micBegin();
bool micPresent();

// Push-to-talk. A press means STOP TALKING AND LISTEN, so it also silences playback -- the two
// gates then never contend. Idempotent: a repeated press is not a new utterance.
void micPress();
void micRelease();

// For the panel, and for the `VOICE` reply over UDP. Stable buffer, same convention as
// pantiltStatus(). THE NUMBER THAT MATTERS HERE IS THE MEASURED NOISE FLOOR -- the workplan
// requires it be read with the drive motors running before anyone trusts a threshold, and this is
// how it is read.
const char* micStatus();
