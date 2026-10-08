/*
 * Non-verbal audio cues — the five-cue vocabulary from notes/design/face-design.md.
 *
 * Rhythm-distinct on purpose. The sonification literature maps state onto pitch, rhythm, timbre,
 * loudness and repetition, but a cue separated only by PITCH is lost on a noisy room and on anyone
 * not listening for it, while rhythm survives both. So the five differ first in rhythm and only
 * incidentally in pitch.
 *
 * ENVELOPE, NOT WAVEFORM. A hard-gated tone clicks and reads as cheap; the same frequency with a
 * short attack and a longer decay reads as deliberate. That single detail is worth more than any
 * amount of frequency selection, so every cue is rendered into PCM with an envelope rather than
 * being switched on and off.
 *
 * ONE PLAYER, NOT EIGHT CHANNELS. The design assumed M5Unified's eight virtual channels; this
 * build uses the demo HAL, whose audioPlay() REFUSES while something is already playing -- it does
 * not queue and does not mix. So a cue cannot be layered, and an unguarded second cue is silently
 * DROPPED. Priority is therefore enforced here in software: see cuePlay().
 */
#pragma once
#include <cstdint>

enum class Cue : uint8_t {
    Heard = 0,   // one short blip -- "I noticed you", inside a second, whether or not I can act
    Attend,      // two quick rising blips -- "I am attending to you"
    Refuse,      // two slow falling blips -- "I cannot do that" (distinct from a fault)
    Fault,       // repeating triple -- "something is wrong", re-issued while the condition holds
    Mode,        // one long tone -- an authority or mode transition, which must be audible
    Boot,        // ours, replacing the vendor startup sound
    Count
};

void cuesBegin();                 // render the PCM once; allocates, so call at startup
void cuePlay(Cue c);              // priority-aware; a lower cue is suppressed, not queued
void cueTick(uint32_t nowMs);     // drives the Fault repeat
void cueFaultActive(bool on);     // latch the fault condition on or off

// IS THE SPEAKER ACTIVE RIGHT NOW. This is the half-duplex gate's input: with the ES7210's AEC
// reference channels powered down the robot cannot hear past itself, so capture is muted while
// anything is playing (voice/listen.h has the full reasoning).
//
// It reads the same deadline cuePlay() already maintains rather than asking the HAL, because the
// HAL has no "is it playing" accessor and because this deadline is what cuePlay itself trusts.
// Consequence worth knowing: it covers CUES, which is all this build plays. When streamed TTS
// arrives (Phase B) it must feed the same gate, or the robot will hear its own voice.
bool cuesSpeaking(uint32_t nowMs);
