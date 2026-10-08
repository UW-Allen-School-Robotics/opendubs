#include "cues.h"
#include "ui_settings.h"
#include "shared/due.h"
#include <hal/hal.h>
#include <mooncake_log.h>
#include <vector>
#include <cmath>

namespace {

// ============================================================================================
// CUES ARE DISABLED. The team asked for beeps off until further notice: the Mode tone was still
// too loud AND TOO SHRILL for something that fires on every state change. Flip this one flag to
// bring them back.
//
// "Shrill" is a pitch complaint, not a volume one, so lowering the gain again would not have
// fixed it -- 2000 Hz is in the band the ear is most sensitive to and reads as piercing however
// quiet it is. The Mode cue below is retuned to 950 Hz at 0.22 gain so that re-enabling starts
// from a warmer place rather than from the thing that was rejected. Untested by ear, and marked
// as such: it is a better guess, not a verified fix.
// ============================================================================================
// RETIRED: this was a compile-time constant hard-disabling audio because the Mode tone was too
// loud. It was a compile-time lie about a runtime setting -- there is no way to change it at the
// robot, and a shipped image would carry "audio off" as an unchangeable property. Audio is now a
// setting on the settings panel, persisted in NVS, defaulting OFF so nobody re-makes the "too
// loud" call on an operator's behalf.
constexpr bool kCuesEnabled = true;

// What the HAL actually wants: 48 kHz, 16-bit, interleaved stereo. Read from
// hal_audio.cpp's i2s_reconfig_clk_fn(48000, 16, I2S_SLOT_MODE_STEREO) rather than assumed.
constexpr int kRate = 48000;

// Priority. Higher wins; equal does not interrupt. Fault and Mode outrank the social cues because
// a dropped "I heard you" is a missed pleasantry while a dropped fault is a missed fault.
constexpr uint8_t kPrio[(int)Cue::Count] = {
    1,  // Heard
    1,  // Attend
    2,  // Refuse
    4,  // Fault
    3,  // Mode
    2,  // Boot
};

struct Note { float hz; int ms; };

// LOUDNESS IS INVERSELY PROPORTIONAL TO HOW OFTEN A CUE FIRES. Mode sounds on every state change,
// so it is routine, and a routine cue at the volume of an alarm is the fastest way to make someone
// mute the robot entirely -- at which point the cue that actually mattered goes with it. Fault is
// rare and must not be missed, so it keeps the headroom.
//
// Per-cue gain, not one master level:
constexpr float kGain[(int)Cue::Count] = {
    0.40f,   // Heard  -- frequent, a background acknowledgement
    0.50f,   // Attend -- less frequent
    0.70f,   // Refuse -- infrequent and consequential
    1.00f,   // Fault  -- rare, urgent, keeps the headroom
    0.22f,   // Mode   -- ROUTINE, and retuned low. See kCuesEnabled above.
    0.65f,   // Boot   -- once per power cycle
};

std::vector<int16_t> g_pcm[(int)Cue::Count];
uint32_t g_busyUntil = 0;
uint8_t  g_busyPrio  = 0;

// due() is now shared/due.h -- the copy that used to live here said "if a third copy ever appears,
// promote it to a header", a third had appeared in face_probe.cpp, and a fourth was about to.
bool     g_faultOn   = false;
uint32_t g_nextFault = 0;

// Attack and decay in milliseconds. Short attack so the onset is crisp, longer decay so it does
// not click off -- the asymmetry is what makes it read as a sound rather than a switch.
void renderNote(std::vector<int16_t>& out, float hz, int ms, float gain)
{
    const int n = kRate * ms / 1000;
    const int atk = kRate * 8 / 1000;
    const int dec = kRate * 55 / 1000;
    for (int i = 0; i < n; ++i) {
        float env = 1.0f;
        if (i < atk)            env = (float)i / (float)atk;
        else if (n - i < dec)   env = (float)(n - i) / (float)dec;
        // A touch of second harmonic. A pure sine on a small speaker reads as thin; this is the
        // cheapest way to give it some body without straying toward anything speech-like.
        const float ph = 6.2831853f * hz * (float)i / (float)kRate;
        const float s  = sinf(ph) * 0.82f + sinf(ph * 2.0f) * 0.18f;
        const int16_t v = (int16_t)(s * env * gain * 20000.0f);
        out.push_back(v);   // interleaved stereo: same sample both ears
        out.push_back(v);
    }
}

void renderSilence(std::vector<int16_t>& out, int ms)
{
    const int n = kRate * ms / 1000;
    for (int i = 0; i < n; ++i) { out.push_back(0); out.push_back(0); }
}

void build(Cue c, const Note* notes, int count, const int* gaps)
{
    auto& v = g_pcm[(int)c];
    v.clear();
    const float gain = kGain[(int)c];
    for (int i = 0; i < count; ++i) {
        renderNote(v, notes[i].hz, notes[i].ms, gain);
        if (i + 1 < count) renderSilence(v, gaps[i]);
    }
}

uint32_t durationMs(Cue c)
{
    // Stereo, so two samples per frame.
    return (uint32_t)(g_pcm[(int)c].size() / 2 * 1000 / kRate);
}

} // namespace

void cuesBegin()
{
    // IDEMPOTENT, AND IT DOES NOT ALLOCATE UNLESS AUDIO IS ON.
    //
    // This rendered 306 kB of PCM at startup -- TWICE, because the startup animation and the face
    // app both call it -- and with CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 a std::vector of this
    // size lands in INTERNAL DRAM, of which the whole chip has 250-310 kB. The allocation failed,
    // the result was dereferenced, and the board panicked with a load access fault on boot.
    //
    // I knew this: an earlier commit message records that the earcon PCM lands internal because
    // the HAL's audioPlay() takes std::vector<int16_t>& with the default allocator, and says it
    // was deliberately not hacked around. Then kCuesEnabled was flipped to true and it allocated
    // anyway. The compile-time guard had been hiding a real memory problem, not just a loud beep.
    //
    // So: render on demand, once, and only when audio is actually enabled.
    static bool rendered = false;
    if (rendered) return;
    if (!settingsAudioEnabled()) {
        mclog::tagInfo("cues", "audio off -- PCM not rendered (saves ~306 kB internal)");
        return;
    }
    rendered = true;

    if (!kCuesEnabled) return;
    // The vocabulary. Five meanings, each identifiable by RHYTHM alone -- one, two-fast, two-slow,
    // three-repeating, one-long -- so none depends on hearing the pitch correctly.
    { const Note n[] = {{2200, 70}};                       build(Cue::Heard,  n, 1, nullptr); }
    { const Note n[] = {{1800, 55}, {2600, 55}};
      const int  g[] = {45};                               build(Cue::Attend, n, 2, g); }
    { const Note n[] = {{2400, 110}, {1600, 130}};
      const int  g[] = {110};                              build(Cue::Refuse, n, 2, g); }
    { const Note n[] = {{2800, 70}, {2800, 70}, {2800, 70}};
      const int  g[] = {55, 55};                           build(Cue::Fault,  n, 3, g); }
    // 950 Hz, not 2000. The ear is most sensitive around 2-4 kHz, so a routine cue placed there
    // reads as piercing no matter how far the gain comes down. Lower and a touch longer.
    { const Note n[] = {{950, 340}};                       build(Cue::Mode,   n, 1, nullptr); }
    // Ours, replacing the vendor startup sound -- a rising third, which is about as much identity
    // as a boot cue should claim.
    { const Note n[] = {{1600, 90}, {2000, 90}, {2500, 160}};
      const int  g[] = {30, 30};                           build(Cue::Boot,   n, 3, g); }

    size_t bytes = 0;
    for (auto& v : g_pcm) bytes += v.size() * sizeof(int16_t);
    mclog::tagInfo("cues", "rendered {} kB of PCM for {} cues", bytes / 1024, (int)Cue::Count);
}

void cuePlay(Cue c)
{
    if (!kCuesEnabled || !settingsAudioEnabled()) return;
    const uint32_t now = GetHAL()->millis();
    const uint8_t prio = kPrio[(int)c];

    // SUPPRESS, DO NOT QUEUE. The HAL refuses a second audioPlay while one is in flight and only
    // logs a warning, so without this a cue would be dropped silently and invisibly. Tracking the
    // in-flight cue's priority here turns that into a deliberate decision: a lower or equal cue is
    // skipped outright rather than attempted and lost.
    if (!due(now, g_busyUntil) && prio <= g_busyPrio) return;

    auto& v = g_pcm[(int)c];
    if (v.empty()) return;
    GetHAL()->audioPlay(v, true);
    g_busyUntil = now + durationMs(c) + 20;   // a little slack for the task to pick it up
    g_busyPrio  = prio;
}

void cueFaultActive(bool on)
{
    if (on == g_faultOn) return;
    g_faultOn = on;
    if (on) g_nextFault = 0;                  // sound immediately on entering the condition
}

void cueTick(uint32_t nowMs)
{
    // "Something is wrong" REPEATS and does not stop, because a fault that announced itself once
    // and went quiet is indistinguishable from a fault nobody noticed. Spaced so it is insistent
    // without masking speech or becoming fatiguing.
    if (!g_faultOn) return;
    // Wrap-safe, and this one matters more than the gate above: the early return happens
    // BEFORE g_nextFault is advanced, so with a raw `<` the 32-bit wrap at ~49 days of uptime
    // would silence the fault re-announcement for good rather than for one cycle.
    if (!due(nowMs, g_nextFault)) return;
    g_nextFault = nowMs + 3200;
    cuePlay(Cue::Fault);
}
