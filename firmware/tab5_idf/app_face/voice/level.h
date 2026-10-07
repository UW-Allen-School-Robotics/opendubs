// WHAT THE MICROPHONES ARE HEARING, as pure arithmetic. No HAL, no LVGL, no FreeRTOS, no driver.
//
// Same construction as percept/ and for the same reason: the layer that decides whether someone is
// speaking is pure compute over int16 samples, so the identical translation unit that runs on the
// panel compiles and runs on a laptop. That is the only verification available to anyone without
// the board -- and at the time of writing the vendor demo tree is not on this machine at all, so it
// is the only verification available here, full stop.
//
// It replaces nothing on the device yet. The HAL-facing shim that calls audioRecord() and feeds
// this is a separate file, deliberately: everything interesting is here, where it can be tested.
//
// PORTED FROM firmware/tab5_face/bench_mic/main.cpp, which is in the ARDUINO tree and measured
// these numbers interactively on 2026-10-04. Two results came out of it and both are encoded here:
//
//   * direction of a voice resolves to LEFT / RIGHT / CENTRE -- three values, not an angle. The
//     ratio thresholds below (1.15 / 0.87) are that bench's, carried over rather than re-guessed.
//   * "no stimulus" must read as NO SIGNAL and never as a direction. The bench learned this the
//     hard way: a quiet room read as low correlated levels on both channels and was misdiagnosed
//     as a dead chip. Every verdict here is gated on the measured floor for that reason.
#pragma once
#include <stdint.h>

// Per-channel level of one block of INTERLEAVED STEREO samples, as RMS on the int16 full scale
// (0 .. 32768). The HAL records stereo; the two channels are the two microphones and their
// DIFFERENCE is the entire direction signal, so they are never mixed down before this point.
struct VoiceLevel {
    float l;
    float r;
    float mean() const { return (l + r) * 0.5f; }
};

// `frames` is sample PAIRS, not array elements: `samples` holds frames*2 int16s.
VoiceLevel voiceLevel(const int16_t* samples, int frames);

// ---- the noise floor -------------------------------------------------------------------------
//
// THE THRESHOLD IS MEASURED, NOT CHOSEN. The drive motors, the lift and the ESCs are about a metre
// from these microphones, so a constant lifted from a quiet bench is fiction on a moving robot --
// the workplan says so in capitals and it is right.
//
// An asymmetric tracker, not an average: it rises SLOWLY and falls FAST. The asymmetry is the whole
// design. Rising slowly means a sentence cannot drag the floor up behind it and mute the rest of
// itself; falling fast means that when the drive motors stop, the robot regains its sensitivity in
// about a second rather than sulking at the loud floor for a minute.
struct VoiceFloor {
    float l;
    float r;
    bool  valid;     // false until enough quiet blocks have been seen to mean anything
    int   observed;  // blocks folded in so far
};

void voiceFloorReset(VoiceFloor* f);

// Fold one block into the floor. ONLY CALL THIS WHEN NOBODY IS SPEAKING AND NOTHING IS PLAYING --
// a floor that updates during speech chases the speech, and a floor that updates during playback
// learns the robot's own voice as the room. The caller owns that gate; see listen.h, which owns it
// on the device.
void voiceFloorUpdate(VoiceFloor* f, const VoiceLevel& v);

// The level a block must exceed to count as signal. Returns a deliberately high number while the
// floor is still invalid, so an un-measured floor fails CLOSED -- silence, not a hair trigger.
float voiceFloorThreshold(const VoiceFloor& f);

// ---- direction -------------------------------------------------------------------------------
enum class VoiceDir : uint8_t {
    None = 0,   // below the floor: no stimulus reached the microphones. NOT a direction
    Left,
    Right,
    Centre,
};

const char* voiceDirName(VoiceDir d);

// Three values, because three is what the bench could actually resolve. Resist the urge to return
// the ratio as an angle: design/hri-interaction-design.md rules that the head GLANCES toward a
// voice rather than tracking it, and left/right/centre is exactly enough to aim a glance.
VoiceDir voiceDirection(const VoiceLevel& v, const VoiceFloor& f);
