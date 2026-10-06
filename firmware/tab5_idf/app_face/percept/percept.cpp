#include "percept.h"
#include "percept_config.h"

#include "attention.h"
#include "facedet.h"
#include "frame.h"
#include "motion.h"
#include "selfmotion.h"
#include "selftest.h"
#include "../shared/alloc.h"
#include "../shared/clock.h"
#include "../shared/due.h"

#include <atomic>
#include <stdio.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#endif

namespace {

// ---- cadence -------------------------------------------------------------------------------
// 15 Hz for motion. The camera produces ~30 fps and the architecture note says 15 Hz is enough for
// saliency; the smoothing in motion.cpp reaches ~90% of a sustained level in about half a second at
// this rate, which is the timescale a person approaching happens on.
//
// NOTE WHAT THIS IS NOT: it is not synchronised to the camera. We poll a buffer somebody else
// overwrites, so we may difference two samples of the same capture (which reads as no motion:
// harmless) or skip a capture (also harmless). Frame-accurate pairing would need the camera task to
// publish a sequence number, which means owning the camera loop -- architecture §2.3's `cam` task,
// a later stage. Until then the cost of this shortcut is a little jitter in the smoothing, not a
// wrong answer.
constexpr uint32_t kPeriodMs = 66;

// Let the face app build its scene and the startup animation finish before we allocate several MB
// and start chewing CPU1. Inherited from the old probe task, where it was load-bearing.
constexpr uint32_t kStartDelayMs = 2500;

// FACE DETECTION IS GATED, because it costs 25-35 ms of CPU1 per run and is only useful inside
// ~1.1 m. 2 Hz when nothing is happening; ~8 Hz when motion says something is moving and it is not
// out at the edge of the frame, which is the only situation in which a face is plausibly about to
// be in range. The decision uses the PREVIOUS frame's motion, which costs one frame of latency at
// 15 Hz and keeps the frame sampling to a single pass.
constexpr uint32_t kFaceSlowMs = 500;
constexpr uint32_t kFaceFastMs = 125;
constexpr float kFaceHotStrength = 0.12f;
constexpr float kFaceHotCentre = 0.70f;   // |nx| below this counts as "toward the middle"

// ---- buffers, all allocated once, all in PSRAM, none freed ---------------------------------
uint8_t* g_luma = nullptr;      // 57,600 B  -- required
uint16_t* g_preview = nullptr;  // 115,200 B -- debug build only
uint8_t* g_rgb888 = nullptr;    // 172,800 B -- only if the detector is compiled in and loaded

const uint16_t* g_src = nullptr;
int g_srcW = 0, g_srcH = 0;

std::atomic<uint32_t> g_frameSeq{0};
std::atomic<bool> g_running{false};

// STATUS, DOUBLE-BUFFERED. A reader (the face app, via lv_label_set_text_static) holds the pointer
// we publish, so writing in place would let it render a half-rewritten line. Two buffers and an
// atomic pointer swap mean the buffer being written is never the one that was just published, and
// every buffer keeps a NUL in its last byte for good, so even a pathologically stale reader sees a
// terminated string rather than running off the end.
char g_statusBuf[2][176] = {"percept: off", "percept: off"};
std::atomic<int> g_statusIdx{0};

// NO CONSOLE WRITES FROM THIS TASK IN ANY BUILD, unless explicitly asked for. A write to
// USB-serial-JTAG blocks the calling task when no host is draining the port, which makes serial
// telemetry a plausible cause of the freeze it is supposed to be diagnosing -- this app has been
// there. The panel string above is the channel; OPENDUBS_DBG_PERCEPT_SERIAL exists for a bench
// session where someone IS draining the port and wants latency numbers.
#if defined(OPENDUBS_DBG_PERCEPT_SERIAL) && defined(ESP_PLATFORM)
#define PERCEPT_LOG(...) do { printf(__VA_ARGS__); fflush(stdout); } while (0)
#else
#define PERCEPT_LOG(...) do { } while (0)
#endif

// THE SELF-TEST VERDICT STICKS, and on a failure it takes the whole line. Perception whose own
// arithmetic failed its synthetic stimulus is not perception whose live numbers are worth reading,
// and burying that under a scrolling status would be the same mistake as reporting a probe only
// over a serial port nobody can read. Debug builds only; absent entirely otherwise.
char g_selfTest[96] = "";     // the full verdict, used when it FAILED and takes the whole line
char g_stToken[12] = "";      // the short form that rides along on a passing line
bool g_selfTestFailed = false;

void publishStatus(const MotionResult& m, const GazeIntent& cur, bool haveTarget, bool gated,
                   uint32_t nowMs)
{
    const int next = 1 - g_statusIdx.load(std::memory_order_relaxed);
    char* b = g_statusBuf[next];
    const FrameStats fs = frameLastStats();

    if (g_selfTestFailed) {
        strncpy(b, g_selfTest, sizeof(g_statusBuf[0]) - 1);
        b[sizeof(g_statusBuf[0]) - 1] = 0;
        g_statusIdx.store(next, std::memory_order_release);
        return;
    }

    const char* gate = gated ? (selfMotionHasImu() ? "self" : "glob")
                             : (selfMotionHasImu() ? "-" : "noimu");

    snprintf(b, sizeof(g_statusBuf[0]),
             "att %s %.2f @%+.2f%+.2f | mot %.2f c%d,%d chg%2.0f%% gate:%s %.0fdps | luma %u/%u/%u | %s%s",
             haveTarget ? attentionKindName(cur.kind) : "none",
             haveTarget ? cur.confidence : 0.0f,
             haveTarget ? cur.nx : 0.0f,
             haveTarget ? cur.ny : 0.0f,
             m.valid ? m.strength : 0.0f, m.cellX, m.cellY, m.changedFrac * 100.0f,
             gate, selfMotionRateDps(),
             (unsigned)fs.lo, (unsigned)fs.mean, (unsigned)fs.hi,
             g_stToken, facedetStatus());
    b[sizeof(g_statusBuf[0]) - 1] = 0;
    g_statusIdx.store(next, std::memory_order_release);
    (void)nowMs;
}

void setPlainStatus(const char* s)
{
    const int next = 1 - g_statusIdx.load(std::memory_order_relaxed);
    strncpy(g_statusBuf[next], s, sizeof(g_statusBuf[0]) - 1);
    g_statusBuf[next][sizeof(g_statusBuf[0]) - 1] = 0;
    g_statusIdx.store(next, std::memory_order_release);
}

#ifdef ESP_PLATFORM
void perceptTask(void*)
{
    vTaskDelay(pdMS_TO_TICKS(kStartDelayMs));

    selfMotionBegin();
    attentionReset();

#if defined(OPENDUBS_DBG_PERCEPT)
    // SELF-STIMULUS, SELF-VERDICT, NO HUMAN. The identical motion/arbiter code is driven with a
    // synthetic moving blob and asserted against; the verdict goes on the panel. This is the "S"
    // (self-verifying) half of stage 7 in notes/design/tab5-face-architecture.md, and it is the
    // same code `./build.sh percept` runs on a laptop -- so a difference between the two is a
    // platform difference, not a test difference.
    {
        char v[96];
        if (perceptSelfTest(v, sizeof(v))) {
            snprintf(g_stToken, sizeof(g_stToken), "st:PASS ");
        } else {
            snprintf(g_selfTest, sizeof(g_selfTest), "%s", v);
            g_selfTestFailed = true;
        }
        setPlainStatus(v);
    }
#endif

    // The detector loads several MB of weights, so do it here rather than in perceptStart(), which
    // runs on the UI task. A one-off few hundred milliseconds on the animation task would be a
    // visible stall; here it is invisible.
    if (facedetBegin()) {
        facedetSelfTest();
        g_rgb888 = (uint8_t*)bigAlloc((size_t)kPerceptW * kPerceptH * 3);
        if (!g_rgb888) setPlainStatus("percept: no PSRAM for detector input");
    }

    uint32_t nextFace = perceptNowMs();
    MotionResult last = {};
    last.cellX = -1;
    last.cellY = -1;

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(kPeriodMs));
        const uint32_t now = perceptNowMs();

        // SAMPLE THE GATE BEFORE THE FRAME, not after: the question is whether the frame we are
        // about to read was captured while the camera was moving.
        selfMotionSample(now);
        const bool suppress = selfMotionSuppressed(now);

        // Decide the detector BEFORE sampling, so rgb888 comes out of the same single pass.
        const bool hot = last.valid && last.strength > kFaceHotStrength &&
                         (last.nx > -kFaceHotCentre && last.nx < kFaceHotCentre);
        const bool runFace = g_rgb888 && facedetAvailable() && !suppress && due(now, nextFace);

        frameSample(g_src, g_srcW, g_srcH, g_luma, g_preview, runFace ? g_rgb888 : nullptr);

        const MotionResult m = motionUpdate(g_luma, suppress);
        if (m.valid) attentionNoteMotion(now, m.nx, m.ny, m.strength);
        if (!m.gated) last = m;

        if (runFace) {
            nextFace = now + (hot ? kFaceFastMs : kFaceSlowMs);
            float fnx = 0.0f, fny = 0.0f, score = 0.0f;
            if (facedetRun(g_rgb888, kPerceptW, kPerceptH, &fnx, &fny, &score)) {
                attentionNoteFace(now, fnx, fny, score);
                PERCEPT_LOG("[percept] face %.2f @%+.2f%+.2f\n", score, fnx, fny);
            }
        }

        attentionTick(now);

        GazeIntent cur = {};
        const bool haveTarget = attentionGet(&cur);
        g_frameSeq.fetch_add(1, std::memory_order_relaxed);
        publishStatus(m, cur, haveTarget, m.gated, now);
    }
}
#endif  // ESP_PLATFORM

}  // namespace

bool perceptStart(const uint16_t* src, int srcW, int srcH)
{
    if (g_running.load()) return true;
    if (!src || srcW <= 0 || srcH <= 0) {
        setPlainStatus("percept: no source buffer");
        return false;
    }
    g_src = src;
    g_srcW = srcW;
    g_srcH = srcH;

    g_luma = (uint8_t*)bigAlloc((size_t)kPerceptW * kPerceptH);
    if (!g_luma || !motionBegin()) {
        setPlainStatus("percept: no PSRAM for luma planes");
        return false;
    }

#if defined(OPENDUBS_DBG_PERCEPT)
    g_preview = (uint16_t*)bigAlloc((size_t)kPerceptW * kPerceptH * 2);
    if (!g_preview) setPlainStatus("percept: no PSRAM for preview (continuing)");
#endif

#ifdef ESP_PLATFORM
    // PRIORITY 2, PINNED TO CPU1, STACK IN PSRAM.
    //   prio 2  -- below cam(5), sense/net(3) and the LVGL port task(4). A 35 ms inference
    //              preempted by a servo tick is correct; the reverse is not.
    //   core 1  -- the structural half. The animation lives on CPU0; nothing we write that is not
    //              the UI runs there. A mistake in the priority ladder is then survivable.
    //   PSRAM stack -- xTaskCreatePinnedToCoreWithCaps puts the stack where we ask and the TCB in
    //              internal DRAM regardless (verified in IDF 5.5.5's
    //              freertos_tasks_c_additions.h), and CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY is
    //              already y. This task runs no ISR and does no flash I/O, which the architecture
    //              note names as the condition that makes an external stack safe. The result is
    //              ~5 KB LESS internal DRAM than the 6 KB internal stack it replaces -- and
    //              internal DRAM is the resource that has already refused to create a task here.
    if (xTaskCreatePinnedToCoreWithCaps(perceptTask, "percept", 8192, nullptr, 2, nullptr, 1,
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS) {
        setPlainStatus("percept: TASK CREATE FAILED");
        return false;
    }
#endif
    g_running.store(true);
    setPlainStatus("percept: starting");
    return true;
}

bool perceptRunning()
{
    return g_running.load();
}

bool perceptGaze(GazeIntent* out)
{
    return attentionGet(out);
}

void perceptNoteHeadCommand(uint32_t durMs)
{
    selfMotionNoteHeadCommand(perceptNowMs(), durMs);
}

const uint16_t* perceptPreview()
{
    return g_preview;
}

int perceptPreviewW()
{
    return kPerceptW;
}

int perceptPreviewH()
{
    return kPerceptH;
}

uint32_t perceptFrameSeq()
{
    return g_frameSeq.load(std::memory_order_relaxed);
}

const char* perceptStatus()
{
    return g_statusBuf[g_statusIdx.load(std::memory_order_acquire)];
}
