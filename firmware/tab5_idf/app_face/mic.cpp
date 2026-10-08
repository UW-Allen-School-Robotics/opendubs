#include "mic.h"
#include "percept/percept_config.h"

#if !defined(OPENDUBS_VOICE)

// COMPILED OUT, not merely disabled -- the face_probe.cpp pattern this project uses everywhere for
// an instrument that is off. These stubs exist only so callers still link.
bool        micBegin()   { return false; }
bool        micPresent() { return false; }
void        micPress()   {}
void        micRelease() {}
const char* micStatus()  { return "voice: compiled out"; }

#else

#include "cues.h"
#include "ui_settings.h"
#include "voice/level.h"
#include "voice/listen.h"
#include <hal/hal.h>
#include <mooncake_log.h>
#include <atomic>
#include <vector>

#ifdef ESP_PLATFORM
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#endif

namespace {

// ---- THE BLOCK SIZE, and why it is 32 ms and not 48 -----------------------------------------
//
// 32 ms is what voice/selftest.cpp tests, and matching it exactly means the device runs the
// configuration that was verified rather than a cousin of it. That sounds obvious; it is load
// bearing because level.cpp's floor warm-up is counted in BLOCKS (24 of them), so changing the
// block duration silently changes how long a cold-boot press takes to calibrate.
//
// I considered 48 ms for a real reason and rejected it. audioRecord() takes a
// std::vector<int16_t> with the DEFAULT allocator, so we cannot route it through shared/alloc.h's
// bigAlloc(); and CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 means a buffer under 16 KB lands in
// INTERNAL DRAM, the binding resource on this chip. A 48 ms block is 18.4 kB and would therefore
// land in PSRAM for free, where a 32 ms block is 12.3 kB and lands internal.
//
// But 24 blocks of 48 ms is 1152 ms, which pushes the cold-boot calibration PAST the one second
// the `Heard` earcon promises -- and that promise is the entire point of Phase A step 3. So the
// 12.3 kB of internal DRAM is paid knowingly, and recorded here so it is a decision and not an
// accident. If internal DRAM ever becomes the thing that breaks, the fix is to make the warm-up
// time-based instead of block-based, not to quietly change the block size.
constexpr uint16_t kBlockMs = 32;

// ONE BUFFER, ALLOCATED ONCE. audioRecord() calls resize() on what it is given, so handing it the
// same correctly-sized vector every time makes that a no-op. A fresh vector per block would mean a
// 12 kB internal allocation and free at 31 Hz, which is the steady-state churn AUDIT.md invariant 6
// exists to forbid.
std::vector<int16_t> g_buf;

std::atomic<bool> g_present{false};
std::atomic<bool> g_pressed{false};

// Gain is the HAL's own 0..100-ish float, NOT an ES7210 register. An earlier session poked those
// registers before finding this parameter, and bench_mic records that writing 0x00 there -- on the
// belief it was the chip default -- silenced a working microphone. So: never touch the registers.
constexpr float kGain = 80.0f;

uint32_t nowMs()
{
    return GetHAL()->millis();
}

#ifdef ESP_PLATFORM
void handle(VoiceEvent e)
{
    switch (e) {
        case VoiceEvent::Heard:
            // PHASE A STEP 3, and the cheapest honest thing the robot can do: "I noticed you",
            // inside a second, whether or not anything can be done about it. The earcon has existed
            // in cues.cpp since it was written with nothing to trigger it. This is the trigger.
            cuePlay(Cue::Heard);
            mclog::tagInfo("mic", "heard");
            break;

        case VoiceEvent::UtteranceEnd:
            // Phase B hands this to the Thor. Until then it is logged, because a bounded clip with
            // a direction is exactly the thing whose timing and plausibility wants watching on a
            // real robot before any transport is built on top of it.
            mclog::tagInfo("mic", "utterance ended, from the {}", voiceDirName(listenDir()));
            break;

        case VoiceEvent::NoSpeech:
            // NOT an error, and it must stay distinguishable from a clip: a failure to HEAR is not
            // a failure to UNDERSTAND. No earcon -- the robot should not chirp at someone who held
            // the button and said nothing.
            mclog::tagInfo("mic", "press ended with no speech (floor {:.0f})",
                           (double)voiceFloorThreshold(listenFloor()));
            break;

        default:
            break;
    }
}

void step()
{
    const uint32_t t = nowMs();

    // THE HALF-DUPLEX GATE'S INPUT. Told to listen.cpp rather than acted on here, so the mute and
    // the floor suppression stay in one place and this file keeps no policy.
    listenSetPlaying(t, cuesSpeaking(t));

    if (!g_pressed.load()) {
        // NO PRESS, NO CAPTURE. audioRecord() is not called at all -- the privacy guarantee is that
        // this branch exists, not that a sample is discarded later.
        handle(listenTick(t));
        return;
    }

    GetHAL()->audioRecord(g_buf, kBlockMs, kGain);
    const int frames = (int)(g_buf.size() / kVoiceChans);
    if (frames <= 0) {
        handle(listenTick(t));
        return;
    }
    handle(listenBlock(nowMs(), g_buf.data(), frames));
}

void micTask(void*)
{
    for (;;) {
        step();
        // Only yield when idle. While a press is held, audioRecord() blocks for kBlockMs doing the
        // i2s_read, which IS the pacing -- an extra delay there would drop audio on the floor
        // between blocks and punch holes in the middle of words.
        if (!g_pressed.load()) vTaskDelay(pdMS_TO_TICKS(kBlockMs));
    }
}
#endif  // ESP_PLATFORM

} // namespace

bool micBegin()
{
    if (g_present.load()) return true;

#ifndef ESP_PLATFORM
    // THE TWIN CANNOT CAPTURE, AND MUST NOT CLAIM TO.
    //
    // The desktop HAL's audioRecord() is an empty virtual: it leaves the buffer exactly as handed
    // in. Reporting "ready" here would make the twin lie in the one way that matters -- and it
    // nearly did something worse, because a buffer that is never filled stays zeroed, and all-zero
    // samples used to collapse the threshold to zero and read as continuous speech. That bug is
    // fixed in level.cpp (kMinThreshold) and tested, but the twin still has nothing to listen to.
    //
    // The state machine's verification is `./build.sh voice`, which exercises it far harder than
    // clicking at a window could: thirteen cases with synthetic stimulus and their own verdict.
    mclog::tagInfo("mic", "no capture on the desktop twin -- verify with ./build.sh voice");
    return false;
#else
    // Size the buffer ONCE, to exactly what audioRecord() will ask for:
    // 48000 * 4ch * ms / 1000, read from hal_audio.cpp rather than assumed.
    g_buf.assign((size_t)kVoiceRate * kVoiceChans * kBlockMs / 1000, 0);
    listenBegin();

    // PSRAM STACK, pinned to CPU1, priority 2 -- percept/percept.cpp's pattern and its reasoning
    // applies unchanged: the animation owns CPU0 and nothing of ours runs there, and an external
    // stack is safe for a task that runs no ISR and does no flash I/O. The TCB still costs internal
    // DRAM, which is why this whole file is behind a flag that defaults off.
    if (xTaskCreatePinnedToCoreWithCaps(micTask, "mic", 6144, nullptr, 2, nullptr, 1,
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        // DEGRADE, DO NOT ABORT. A board that cannot spare a TCB must still show a face; the whole
        // voice path going quiet is the correct failure and micPresent() reports it honestly.
        mclog::tagError("mic", "task create FAILED -- voice disabled, face unaffected");
        g_buf.clear();
        g_buf.shrink_to_fit();
        return false;
    }
#endif
    g_present.store(true);
    mclog::tagInfo("mic", "ready: {} ms blocks, {} frames, {} kB buffer",
                   kBlockMs, (int)(g_buf.size() / kVoiceChans),
                   (int)(g_buf.size() * sizeof(int16_t) / 1024));
    return true;
}

bool micPresent()
{
    return g_present.load();
}

void micPress()
{
    if (!g_present.load() || !settingsAudioEnabled()) return;
    // "A PRESS MEANS STOP TALKING AND LISTEN" -- EXCEPT THAT NOTHING CAN STOP THE SPEAKER.
    //
    // The vendor HAL has audioPlay() and no counterpart: there is no stop, no abort and no way to
    // ask whether it is still going (hal.h, checked rather than assumed). So a press cannot cut a
    // sound short. It waits the sound out: the half-duplex gate stays shut until the cue's own
    // deadline passes, and the head is deaf for the remainder.
    //
    // Tolerable TODAY because every sound this build plays is a cue, and the longest is the Fault
    // triple at about 500 ms. It is NOT tolerable for streamed TTS in Phase B -- a robot that
    // cannot be interrupted until it finishes a sentence is a different and much worse thing than
    // one that cannot be interrupted mid-word. Phase B therefore needs a real stop, and it will
    // have to come from the codec layer rather than from here.
    //
    // There is a smaller version of the same wrinkle that cannot be designed away: `Heard` is
    // itself a cue, so acknowledging someone deafens the robot for the ~90 ms the blip lasts. That
    // hole sits immediately after onset, in the gap between syllables, which is the least bad place
    // for it -- but it is real, and it is the price of answering with a sound while having no AEC.
    listenPress(nowMs());
    g_pressed.store(true);
}

void micRelease()
{
    if (!g_present.load()) return;
    g_pressed.store(false);
    listenRelease(nowMs());
}

const char* micStatus()
{
    static char buf[200];
    snprintf(buf, sizeof(buf), "voice %s %s | %s",
             g_present.load() ? "ready" : "ABSENT",
             g_pressed.load() ? "PRESSED" : "idle",
             listenStatus());
    return buf;
}

#endif
