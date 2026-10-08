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

// ---- THE CAPTURE BUFFER LAYOUT, which is NOT what the Arduino bench recorded ----------------
//
// This is the single most important difference between bench_mic and the IDF app, it is invisible
// if assumed, and getting it wrong yields plausible-looking garbage rather than an error. Read from
// the vendor's own source, not guessed:
//
//   * `hal.h` declares `audioRecord(std::vector<int16_t>&, uint16_t durationMs, float gain)` and
//     documents the buffer as `[MIC-L, AEC, MIC-R, MIC-HP]` -- FOUR interleaved channels.
//   * `hal_audio.cpp` sizes it `48000 * 4 * durationMs / 1000`, so the rate is 48 kHz and the
//     channel count is 4.
//   * the vendor's own dual-mic test confirms the offsets by using them:
//         out[i*2 + 0] = in[i*4 + 0];   // MIC-L
//         out[i*2 + 1] = in[i*4 + 2];   // MIC-R
//
// bench_mic/main.cpp, by contrast, called `M5.Mic.record(rec, n, 16000, true)` -- the Arduino
// M5Unified path, 16 kHz and two channels at stride 2. So the two microphones are at stride 4
// offsets 0 and 2 here, NOT at offsets 0 and 1, and a block is three times as many samples per
// millisecond as the bench's. The workplan called this a port rather than a wire-up; this is what
// it meant.
//
// The AEC slot at offset 1 is in the stream LAYOUT, which is not the same as it carrying a usable
// reference: `design/tab5-platform.md` records that the ES7210 has MIC34_PDN=0xFF, so the AEC
// reference inputs are powered down. Settled, and not re-derived here -- it is why the echo
// decision is half-duplex. The slot is named below only so nobody mistakes it for a microphone.
constexpr int kVoiceRate   = 48000;   // hal_audio.cpp, i2s_reconfig_clk_fn(48000, 16, ...)
constexpr int kVoiceChans  = 4;       // [MIC-L, AEC, MIC-R, MIC-HP]
constexpr int kVoiceMicL   = 0;
constexpr int kVoiceAecRef = 1;       // powered down on this board. Not a microphone
constexpr int kVoiceMicR   = 2;
constexpr int kVoiceMicHp  = 3;       // the 3.5 mm headset mic, not the on-board pair

// Level of the two ON-BOARD microphones for one block, as RMS on the int16 full scale
// (0 .. 32768). Their DIFFERENCE is the entire direction signal, so they are never mixed down
// before this point.
struct VoiceLevel {
    float l;
    float r;
    float mean() const { return (l + r) * 0.5f; }
};

// `frames` is sample FRAMES, not array elements: `samples` holds frames * kVoiceChans int16s, and
// this reads only offsets kVoiceMicL and kVoiceMicR out of each frame.
VoiceLevel voiceLevel(const int16_t* samples, int frames);

// Frames in a block of `ms` at the capture rate. 32 ms is 1536 frames -- 6144 int16s, 12 kB.
constexpr int voiceFramesForMs(int ms) { return kVoiceRate * ms / 1000; }

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
