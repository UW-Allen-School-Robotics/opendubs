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
// THE ROBOT MUST NOT HEAR ITSELF. The first run of this loop chirped, heard the chirp as a fresh
// onset at ~14000 RMS against a floor of 20, glanced at itself, chirped again -- and dragged the
// tracked floor up to 327, going deaf to actual people in the process. The speaker and microphone
// share this board, and the ES7210's echo-cancellation channels are disabled by M5Unified's Tab5
// init, so nothing upstream is going to solve this.
uint32_t deafUntil = 0;
constexpr uint32_t kSelfSoundTailMs = 220;   // ring-out after the tone stops
uint32_t glanceUntil = 0;               // the current glance is committed until this moment
uint32_t quietUntil  = 0;               // no new cue before this -- one blip per utterance
uint32_t lastOnset   = 0;
int      heldDir     = 0;

// Audio cue on its OWN virtual channel (M5Unified gives 8), with stop_current_sound = false, so
// an acknowledgement can never cut off a BEEP or CHIME that means something operational.
constexpr int kCueChannel = 3;

void blip() {
  M5.Speaker.tone(2200, 70, kCueChannel, false);
  deafUntil = millis() + 70 + kSelfSoundTailMs;
}

} // namespace

void attentionBegin() {
  const bool ok = M5.Mic.begin();   // coexists with the speaker here; both hold I2S together
  nextSample = 0;
  Serial.printf("[ATTN] mic %s -- social attention %s\n", ok ? "up" : "FAILED",
                ok ? "active" : "DISABLED");
}

void attentionTick(uint32_t now) {
  if (!M5.Mic.isEnabled()) return;
  if (now < nextSample) return;
  nextSample = now + 40;                // ~25 Hz is plenty to catch a voice onset

  // Ignore everything while we are making noise, and do not let our own sound pollute the floor.
  // Checking isPlaying() as well as the timer covers a BEEP or CHIME the robot was told to make
  // by the StamPLC, which this module never knows about in advance.
  if (now < deafUntil || M5.Speaker.isPlaying()) {
    if (M5.Speaker.isPlaying()) deafUntil = now + kSelfSoundTailMs;
    return;
  }

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

  // Heartbeat. Without it, a correctly-silent quiet room and a dead loop look identical from a
  // desk -- and "no output" has already been misread as broken hardware twice in this project.
  static uint32_t hb = 0;
  if (now - hb > 3000) {
    hb = now;
    Serial.printf("[ATTN] idle  L%4.0f R%4.0f  floor %3.0f/%3.0f  (fires above %3.0f)\n",
                  l, r, floorL, floorR, floorL * kOnsetRatio);
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

  const bool cued = now > quietUntil;
  if (cued) {                                   // acknowledge the utterance, not each syllable
    blip();
    quietUntil = now + 1500;
  }
  lastOnset = now;

  // One line per glance. Enough to confirm from a desk that the loop detects, decides a
  // direction and cues -- without anyone having to be in front of the panel to report it.
  Serial.printf("[ATTN] onset L%4.0f R%4.0f floor %3.0f/%3.0f -> look %-6s %s\n", l, r,
                floorL, floorR,
                dir < 0 ? "LEFT" : dir > 0 ? "RIGHT" : "CENTRE", cued ? "+chirp" : "");
}
