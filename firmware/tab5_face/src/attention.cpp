#include "attention.h"
#include "face.h"
#include <M5Unified.h>
#include <math.h>

namespace {

constexpr size_t kSamples = 256;        // 16 ms at 16 kHz -- short, so the loop stays responsive
int16_t rec[kSamples * 2];

// WHICH MICROPHONE IS ON WHICH SIDE is a physical fact about the panel that no datasheet here
// settles, and guessing it wrong makes the robot look AWAY from whoever is talking. If the first
// person to use this says it looks the wrong way, flip this one constant -- do not re-derive the
// gaze mirroring in face.cpp, which is already correct and separately tested.
constexpr bool kMicCh0IsRobotLeft = true;

// Levels are tiny (a quiet room reads ~15-20 RMS, speech peaks a few hundred) so the floor is
// tracked rather than assumed. A fixed threshold would either never fire or fire constantly,
// depending on the room -- and a quiet room reading low is NOT evidence of a broken microphone.
float floorL = 40, floorR = 40;
constexpr float kOnsetRatio = 2.2f;     // how far above the floor counts as "someone spoke"
constexpr float kFloorMin   = 25.0f;    // never trust a floor so low that noise trips it

uint32_t nextSample = 0;
uint32_t glanceUntil = 0;               // the current glance is committed until this moment
uint32_t quietUntil  = 0;               // no new cue before this -- one blip per utterance
uint32_t lastOnset   = 0;
int      heldDir     = 0;

// Audio cue on its OWN virtual channel (M5Unified gives 8), with stop_current_sound = false, so
// an acknowledgement can never cut off a BEEP or CHIME that means something operational.
constexpr int kCueChannel = 3;

void blip() { M5.Speaker.tone(2200, 70, kCueChannel, false); }

} // namespace

void attentionBegin() {
  M5.Mic.begin();          // coexists with the speaker on this board; both hold I2S together
  nextSample = 0;
}

void attentionTick(uint32_t now) {
  if (!M5.Mic.isEnabled()) return;
  if (now < nextSample) return;
  nextSample = now + 40;                // ~25 Hz is plenty to catch a voice onset

  if (!M5.Mic.record(rec, kSamples, 16000, true)) return;
  while (M5.Mic.isRecording()) { delay(1); }

  double a = 0, b = 0;
  for (size_t i = 0; i < kSamples; ++i) {
    a += double(rec[2 * i]) * rec[2 * i];
    b += double(rec[2 * i + 1]) * rec[2 * i + 1];
  }
  const float l = sqrtf(a / kSamples), r = sqrtf(b / kSamples);

  // Track the floor only while nothing is happening, and let it fall fast but rise slowly, so one
  // loud moment cannot desensitise the robot for the next minute.
  if (now > glanceUntil) {
    floorL += ((l < floorL) ? 0.08f : 0.01f) * (l - floorL);
    floorR += ((r < floorR) ? 0.08f : 0.01f) * (r - floorR);
    if (floorL < kFloorMin) floorL = kFloorMin;
    if (floorR < kFloorMin) floorR = kFloorMin;
  }

  const bool onset = (l > floorL * kOnsetRatio) || (r > floorR * kOnsetRatio);
  if (!onset) return;

  // Direction from the inter-channel level ratio. Three answers only -- left, right, centre --
  // which is what this sensing supports and what a glance needs.
  const float ratio = (l + 1.0f) / (r + 1.0f);
  int dir = 0;                                  // -1 robot's left, +1 robot's right
  if (ratio > 1.18f)      dir = kMicCh0IsRobotLeft ? -1 : +1;
  else if (ratio < 0.85f) dir = kMicCh0IsRobotLeft ? +1 : -1;

  // A CONTINUING voice gets REPEATED SHORT GLANCES, not one locked stare: re-aim only once the
  // previous glance has expired, and only cue again after a real pause. Without this the robot
  // either freezes mid-glance or chirps at every syllable, both of which read as malfunctioning.
  if (now < glanceUntil && dir == heldDir) return;

  heldDir = dir;
  faceLookAt(dir == 0 ? 0.0f : dir * 0.85f, dir == 0 ? 0.25f : 0.0f, 900);
  glanceUntil = now + 900;

  if (now > quietUntil) {                       // acknowledge the utterance, not each syllable
    blip();
    quietUntil = now + 1500;
  }
  lastOnset = now;
}
