#include "selftest.h"
#include "percept_config.h"

#if defined(ESP_PLATFORM) && !defined(OPENDUBS_DBG_PERCEPT)

// RELEASE BUILD: the instrument is not merely disabled, it is absent. Same pattern as
// face_probe.cpp, which measured byte-identical with and without. The call site in percept.cpp is
// itself inside #if OPENDUBS_DBG_PERCEPT, so these exist only so that a caller added later still
// links.
bool perceptSelfTest(char*, int)
{
    return false;
}
bool perceptSelfTestVerbose(SelfTestSink)
{
    return false;
}

#else

#include "attention.h"
#include "frame.h"
#include "motion.h"
#include "../shared/alloc.h"
#include "../shared/clock.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

namespace {

SelfTestSink g_sink = nullptr;
char g_fail[96] = "";

void say(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void say(const char* fmt, ...)
{
    if (!g_sink) return;
    char line[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    g_sink(line);
}

bool fail(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
bool fail(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_fail, sizeof(g_fail), fmt, ap);
    va_end(ap);
    say("  FAIL: %s", g_fail);
    return false;
}

// ---- synthetic scene ------------------------------------------------------------------------
// A DETERMINISTIC background, not rand(): the same input every run means a failure is a regression
// rather than bad luck, which is the same reason the face detector's self-test uses a compiled-in
// reference image instead of the camera.
uint8_t* g_scene = nullptr;

bool sceneBegin()
{
    if (!g_scene) g_scene = (uint8_t*)bigAlloc((size_t)kPerceptW * kPerceptH);
    return g_scene != nullptr;
}

// Mid-contrast texture, 40..120, so a bright blob differs from it by far more than kPixelDelta
// everywhere and the test is not accidentally measuring the threshold.
void drawBackground(uint8_t* p, int bias)
{
    for (int y = 0; y < kPerceptH; ++y)
        for (int x = 0; x < kPerceptW; ++x)
            p[y * kPerceptW + x] = (uint8_t)(40 + bias + ((x * 7 + y * 13) & 0x3f) + ((x ^ y) & 0x0f));
}

constexpr int kBlob = 28;   // px, so it covers more than one 20x20 cell in each axis

void drawBlob(uint8_t* p, int cx, int cy)
{
    for (int y = cy - kBlob / 2; y < cy + kBlob / 2; ++y) {
        if (y < 0 || y >= kPerceptH) continue;
        for (int x = cx - kBlob / 2; x < cx + kBlob / 2; ++x) {
            if (x < 0 || x >= kPerceptW) continue;
            p[y * kPerceptW + x] = 245;
        }
    }
}

// ---- case 1: a static scene produces no target ----------------------------------------------
bool caseStatic()
{
    motionReset();
    drawBackground(g_scene, 0);
    for (int i = 0; i < 10; ++i) {
        const MotionResult m = motionUpdate(g_scene, false);
        // The first call primes the history and is gated by design; after that an unchanging scene
        // must produce nothing. A false positive here would mean the eyes track noise.
        if (i > 0 && m.valid)
            return fail("static scene produced a target (strength %.2f)", m.strength);
    }
    say("  ok   static scene: no target over 10 frames");
    return true;
}

// ---- case 2: the hottest cell follows a moving blob -----------------------------------------
bool caseTracksBlob()
{
    motionReset();
    drawBackground(g_scene, 0);
    motionUpdate(g_scene, false);   // prime

    const int cy = kPerceptH / 2;
    int checked = 0, hits = 0;
    float worstErr = 0.0f;

    // 6 px per step: less than one cell, so the leading and trailing edges of the change stay
    // inside the same neighbourhood the centroid averages over. A blob that jumped a whole cell per
    // frame would light up two places at once, which is a true description of the stimulus and a
    // useless test of tracking.
    for (int cx = 50; cx <= 270; cx += 6) {
        drawBackground(g_scene, 0);
        drawBlob(g_scene, cx, cy);
        const MotionResult m = motionUpdate(g_scene, false);

        // Warm-up: the smoothing needs a few frames to lift a cell over the floor. Checking before
        // that would be asserting against the filter's rise time, not against tracking.
        if (cx < 50 + 6 * 6) continue;
        checked++;
        if (!m.valid) continue;

        // Expected normalised position of the blob centre, in the same convention motion.cpp uses.
        const float wantNx = (float)cx / (float)kPerceptW * 2.0f - 1.0f;
        const float err = fabsf(m.nx - wantNx);
        if (err > worstErr) worstErr = err;
        // One cell is 2/16 = 0.125 of the normalised range. Allow 1.5 cells: the target trails the
        // blob slightly because the smoothing is causal, and that lag is real and wanted.
        if (err <= 0.1875f && m.cellY >= (kMotionCellsY / 2) - 1 && m.cellY <= (kMotionCellsY / 2) + 1)
            hits++;
    }

    if (checked == 0) return fail("blob sweep checked no frames");
    const int pct = hits * 100 / checked;
    say("  %s moving blob: %d/%d frames tracked (%d%%), worst |dnx| %.3f",
        pct >= 85 ? "ok  " : "FAIL", hits, checked, pct, worstErr);
    if (pct < 85) return fail("blob tracked in only %d%% of frames", pct);
    return true;
}

// ---- case 3: the sign convention is not inverted --------------------------------------------
// This cannot establish that the signs are right for the real lens -- only the reticle on the
// hardware can do that. What it CAN establish is that motion.cpp does not invert the convention it
// documents, which is the half of the risk that is checkable here.
bool caseSignConvention()
{
    motionReset();
    drawBackground(g_scene, 0);
    motionUpdate(g_scene, false);

    // Settle a blob in the top-left quadrant.
    for (int i = 0; i < 10; ++i) {
        drawBackground(g_scene, 0);
        drawBlob(g_scene, kPerceptW / 4 + (i & 1) * 4, kPerceptH / 4 + (i & 1) * 4);
        motionUpdate(g_scene, false);
    }
    MotionResult m = motionUpdate(g_scene, false);
    if (!m.valid) {
        drawBlob(g_scene, kPerceptW / 4 + 6, kPerceptH / 4 + 6);
        m = motionUpdate(g_scene, false);
    }
    if (!m.valid) return fail("no target for the top-left blob");
    if (m.nx >= 0.0f || m.ny >= 0.0f)
        return fail("top-left blob reported as %+.2f,%+.2f (want both negative)", m.nx, m.ny);
    say("  ok   sign convention: top-left blob -> %+.2f,%+.2f", m.nx, m.ny);
    return true;
}

// ---- case 4: the global-change gate discards a whole-frame change ---------------------------
bool caseGlobalGate()
{
    motionReset();
    drawBackground(g_scene, 0);
    motionUpdate(g_scene, false);
    drawBackground(g_scene, 0);
    motionUpdate(g_scene, false);

    // Every pixel brightens by 60: a light coming on, an auto-exposure step, or the robot driving.
    drawBackground(g_scene, 60);
    const MotionResult m = motionUpdate(g_scene, false);
    if (!m.gated) return fail("whole-frame change was not gated (chg %.2f)", m.changedFrac);
    if (m.valid) return fail("whole-frame change produced a target");
    say("  ok   global gate: %.0f%% of the frame changed -> discarded", m.changedFrac * 100.0f);

    // And the frame after the change must not itself become a false target, because the history was
    // dropped: differencing the new lighting against the old would otherwise light up everything.
    const MotionResult n = motionUpdate(g_scene, false);
    if (n.valid) return fail("the frame after a gated frame produced a target");
    say("  ok   global gate: the following frame is clean too");
    return true;
}

// ---- case 5: self-motion suppression --------------------------------------------------------
bool caseSuppression()
{
    motionReset();
    drawBackground(g_scene, 0);
    motionUpdate(g_scene, false);

    // A blob moves while the robot is "moving". Nothing may be published.
    for (int i = 0; i < 8; ++i) {
        drawBackground(g_scene, 0);
        drawBlob(g_scene, 80 + i * 12, 60);
        const MotionResult m = motionUpdate(g_scene, true);
        if (m.valid) return fail("suppressed frame produced a target");
        if (!m.gated) return fail("suppressed frame was not reported as gated");
    }
    say("  ok   suppression: 8 moving frames produced no target");

    // THE POINT OF THE TEST: the first frame after suppression lifts must not be a false target.
    // A suppressed frame is still adopted as history, so the difference is against a recent view --
    // if it were not, the moment the robot settled it would see the whole world as having moved.
    const MotionResult m = motionUpdate(g_scene, false);
    if (m.valid) return fail("the first frame after suppression produced a target");
    say("  ok   suppression: the frame after it lifts is clean");
    return true;
}

// ---- case 6: the arbiter's precedence and grading -------------------------------------------
bool caseArbiter()
{
    attentionReset();
    // Base the synthetic clock on the real one. attentionGet() deliberately performs its own
    // staleness test against percept's clock (shared/clock.h), so a test driving the arbiter with
    // times in the past would see every target expire instantly. Times at or after now behave.
    const uint32_t t0 = perceptNowMs();
    GazeIntent g = {};

    attentionTick(t0);
    if (attentionGet(&g)) return fail("arbiter reported a target before anything was noted");

    attentionNoteMotion(t0 + 10, 0.4f, 0.2f, 0.30f);
    attentionTick(t0 + 10);
    if (!attentionGet(&g)) return fail("motion alone produced no target");
    if (g.kind != GAZE_MOTION) return fail("motion alone arbitrated to %s", attentionKindName(g.kind));
    const float motionConf = g.confidence;
    const uint32_t motionSeq = g.seq;
    say("  ok   motion alone: kind=motion conf=%.2f", motionConf);

    // A FACE OUTRANKS MOTION even when the motion is strong and the face is weak, because the two
    // confidences measure different things. The face says a person is pointed at the robot.
    attentionNoteMotion(t0 + 20, 0.4f, 0.2f, 1.00f);
    attentionNoteFace(t0 + 20, -0.3f, -0.1f, 0.35f);
    attentionTick(t0 + 20);
    if (!attentionGet(&g)) return fail("face + motion produced no target");
    if (g.kind != GAZE_FACE) return fail("a weak face lost to strong motion");
    const float weakFaceConf = g.confidence;
    const uint32_t weakFaceGood = g.goodUntilMs;
    if (g.seq == motionSeq) return fail("switching cue did not produce a new seq");
    say("  ok   precedence: weak face (0.35) outranks saturated motion, conf=%.2f", weakFaceConf);

    // A STRONG, REPEATED FACE IS WORTH MORE than a weak isolated one -- more confidence and a
    // longer dwell. That is the whole of "a weak hit is a brief glance, a strong repeated hit earns
    // a sustained look", expressed as one graded number.
    attentionReset();
    uint32_t t = t0 + 100;
    for (int i = 0; i < 4; ++i) {
        attentionNoteFace(t, 0.10f, 0.05f, 0.95f);
        attentionTick(t);
        t += 120;
    }
    if (!attentionGet(&g)) return fail("repeated strong face produced no target");
    if (!(g.confidence > weakFaceConf))
        return fail("repeated strong face conf %.2f not above weak face %.2f", g.confidence,
                    weakFaceConf);
    const uint32_t strongDwell = g.goodUntilMs - (t - 120);
    const uint32_t weakDwell = weakFaceGood - (t0 + 20);
    if (!(strongDwell > weakDwell))
        return fail("strong dwell %ums not longer than weak dwell %ums", (unsigned)strongDwell,
                    (unsigned)weakDwell);
    say("  ok   grading: strong+repeated conf=%.2f dwell=%ums vs weak conf=%.2f dwell=%ums",
        g.confidence, (unsigned)strongDwell, weakFaceConf, (unsigned)weakDwell);

    // RETARGET HYSTERESIS. A centroid jittering by a pixel must not make the face saccade every
    // frame -- that reads as a malfunction, not as attention. A real move must.
    const uint32_t seqBefore = g.seq;
    attentionNoteFace(t, 0.12f, 0.06f, 0.95f);
    attentionTick(t);
    attentionGet(&g);
    if (g.seq != seqBefore) return fail("a 0.02 drift produced a retarget");
    attentionNoteFace(t + 60, 0.60f, 0.06f, 0.95f);
    attentionTick(t + 60);
    attentionGet(&g);
    if (g.seq == seqBefore) return fail("a 0.48 move did not produce a retarget");
    say("  ok   hysteresis: 0.02 drift holds seq, 0.48 move bumps it");

    // AGEING OUT degrades to nothing, which is the face's cue to resume its own saccades.
    attentionTick(t + 60 + 60000);
    if (attentionGet(&g)) return fail("a target 60 s old was still live");
    say("  ok   degradation: an old fix ages out to 'none'");
    return true;
}

}  // namespace

bool perceptSelfTestVerbose(SelfTestSink sink)
{
    g_sink = sink;
    g_fail[0] = 0;

    if (!sceneBegin() || !motionBegin()) {
        // INCONCLUSIVE, not FAIL: a test that could not allocate its own scene has established
        // nothing about the thing it was testing, and saying FAIL would be an accusation.
        snprintf(g_fail, sizeof(g_fail), "INCONCLUSIVE: no memory for the test scene");
        say("  %s", g_fail);
        g_sink = nullptr;
        return false;
    }

    bool ok = true;
    ok = caseStatic() && ok;
    ok = caseTracksBlob() && ok;
    ok = caseSignConvention() && ok;
    ok = caseGlobalGate() && ok;
    ok = caseSuppression() && ok;
    ok = caseArbiter() && ok;

    // Leave no residue: the next real frame must not be differenced against a synthetic one.
    motionReset();
    attentionReset();
    g_sink = nullptr;
    return ok;
}

bool perceptSelfTest(char* out, int outLen)
{
    const bool ok = perceptSelfTestVerbose(nullptr);
    if (out && outLen > 0) {
        if (ok)
            snprintf(out, (size_t)outLen, "percept selftest PASS");
        else
            snprintf(out, (size_t)outLen, "percept selftest %s",
                     g_fail[0] ? g_fail : "FAIL (no detail)");
    }
    return ok;
}

#endif  // ESP_PLATFORM && !OPENDUBS_DBG_PERCEPT
