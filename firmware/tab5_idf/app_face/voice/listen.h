// THE LISTENING STATE MACHINE: push-to-talk in, bounded clips and events out. Pure compute.
//
// Three decisions were settled by the maintainers on 2026-10-07 (notes/workplan-tab5-audio.md,
// decisions table) and this file is where all three live. They are written down here because each one is
// invisible in the code otherwise and each one would be re-litigated by the next reader:
//
//   1. PUSH-TO-TALK, not a wake word and not always-listening. Capture happens only while a press
//      is held. The privacy question in a shared university building is therefore answered by
//      construction rather than by policy -- nobody can be recorded without a visible, deliberate
//      act -- and there is no wake-word model to fit on the P4.
//
//      The consequence for this file is large and good: VAD is NOT the gate for whether to listen.
//      The button is. VAD answers exactly one question -- HAS THIS UTTERANCE ENDED -- so a false
//      positive is no longer "the robot started listening to the room", it is "the clip ended
//      early". Much smaller claim, much smaller threshold problem.
//
//   2. HALF-DUPLEX. The ES7210's AEC reference channels (3/4) are disabled in this board's
//      configuration, so there is no hardware cancellation and the robot cannot hear past itself.
//      Rather than write codec registers or run a canceller on the P4, capture is simply muted
//      while the speaker is active. The price is BARGE-IN -- you cannot interrupt the robot
//      mid-sentence -- and it is paid knowingly.
//
//      That gate is ONE function, listenSetPlaying(), and one branch in listenBlock(). It is this
//      concentrated on purpose: when software AEC arrives, it replaces that gate and touches
//      nothing else.
//
//   3. THE TAB5 DECIDES WHERE AN UTTERANCE ENDS, on VAD, and ships a BOUNDED CLIP. The Thor does
//      not get a continuous stream and does not get to revise the boundary with semantics. So a
//      thoughtful mid-sentence pause longer than the hangover will truncate -- tunable below, not
//      fixable here -- and in exchange the link carrying video is not also carrying a permanent
//      audio stream, and the head stays useful with the Thor down.
//
// NO HAL, NO LVGL. The caller feeds it blocks and a clock. On the device that caller is the shim
// that owns audioRecord(); on the host it is the self-test. Neither is visible from here.
#pragma once
#include "level.h"
#include <stdint.h>

enum class VoiceEvent : uint8_t {
    None = 0,

    // Speech was detected inside a held press. THE CHEAPEST HONEST THING THE ROBOT CAN DO: it
    // drives the `Heard` earcon, which has existed unwired in cues.cpp since it was written.
    // "I noticed you, inside a second, whether or not I can act."
    Heard,

    // A complete utterance. `dir` is where it came from -- feed it to the head as a time-boxed
    // GLANCE, never as a tracking target. See listenDir().
    UtteranceEnd,

    // The press ended and nothing ever rose above the measured floor. An honest outcome and NOT an
    // error: it is what a press in a loud bay with no speaker produces, and it must be
    // distinguishable from a clip, or a failure to hear reads as a failure to understand.
    NoSpeech,
};

const char* voiceEventName(VoiceEvent e);

enum class ListenState : uint8_t {
    Idle = 0,      // no press. The only state in which the noise floor is learned
    Waiting,       // press held, nothing above the floor yet
    Speech,        // press held, speech in progress
    Trailing,      // press held, speech dropped below the floor -- inside the hangover
};

void listenBegin();

// Press and release of whatever the operator actually holds -- a button on the face, or the RC
// deadman. A press means STOP TALKING AND LISTEN, so it is the caller's job to cancel playback;
// the two gates then never contend.
void listenPress(uint32_t nowMs);
void listenRelease(uint32_t nowMs);

// THE HALF-DUPLEX GATE, and the only one. True from the moment the speaker starts until it stops.
// While true, blocks are discarded and the floor is not updated -- a floor that learned during
// playback would learn the robot's own voice as the room.
void listenSetPlaying(uint32_t nowMs, bool playing);

// Feed one block of interleaved stereo. Returns the event this block produced, or None.
// At most one event per block; the state machine never emits two.
VoiceEvent listenBlock(uint32_t nowMs, const int16_t* samples, int frames);

// Call every tick even with no block, so a release or a hangover expiry is noticed promptly rather
// than waiting on the next block to arrive.
VoiceEvent listenTick(uint32_t nowMs);

ListenState listenState();

// Where the utterance that just ended came from. Only meaningful on the block that returned
// UtteranceEnd; it is the direction of the LOUDEST block of that utterance, not of its last block,
// because the tail of a sentence is its quietest part and its ratio is the least trustworthy.
VoiceDir listenDir();

// Measured floor, for the status line and for the UDP reply. The workplan requires this be read
// with the motors RUNNING before anyone trusts a threshold, so it has to be reportable.
const VoiceFloor& listenFloor();

// Diagnostics for the panel and `PING`. Stable buffer, same convention as pantiltStatus().
const char* listenStatus();
