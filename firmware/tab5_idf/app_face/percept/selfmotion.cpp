#include "selfmotion.h"
#include "../shared/due.h"
#include <math.h>

#ifdef ESP_PLATFORM
// The vendor's own BMI270 driver component, reached directly rather than through
// GetHAL()->updateImuData(). hal.h includes <lvgl.h>, and including it here would put LVGL into the
// perception layer transitively -- the grep in AUDIT.md would still pass while the invariant it
// stands for had stopped being structural. This header pulls in driver/i2c_master.h and bmi270.h
// and nothing else.
#include "accel_gyro_bmi270.h"
#endif

namespace {

// ---- thresholds. kGyroDps is now MEASURED; the rest are still reasoned ----
//
// MEASURED ON DEVICE 2026-10-05, via the walk-in panel's IMU readout: the Tab5 sitting flat on a
// bench reads 0 dps, and picking it up and moving it reads 100+ dps. That is the first time this
// gate's input has been observed at all -- it had only ever been reasoned about, and the code's
// fallback (four consecutive zero reads disable the gate and the panel says "gate:noimu") existed
// precisely because nobody knew whether the read worked.
//
// So 15 dps is confirmed as a sensible floor rather than a guess: roughly 15x the observed resting
// noise and about 7x below a deliberate hand movement, with no overlap between the two. Left
// unchanged, which is the point -- the value was right, and now it is KNOWN to be right.
// ----------------------------------------------------
// The BMI270 is configured by the vendor at +-1000 dps with 1/32.768 LSB per dps and a 200 Hz ODR
// -- read from accel_gyro_bmi270_enable_sensor(), not assumed.
//
// 15 dps is a deliberate, gentle threshold: a hand nudging the bench panel is tens of dps, a head
// pan is tens to hundreds, and sitting still is well under 1 dps of noise. Erring low costs
// nothing but a few suppressed frames; erring high lets a pan publish a false target, which is the
// failure the gate exists to prevent.
constexpr float kGyroDps = 15.0f;

// How long after the rate falls below the threshold before frames are believed again. Motion blur
// and mechanical settling both outlast the rate itself, and the first frame after a move is the one
// most likely to look like a scene-wide change.
constexpr uint32_t kSettleMs = 300;

// How many consecutive all-zero reads mean "there is no IMU answering" rather than "the board is
// perfectly still". An exactly-zero gyro triple is possible for one sample and vanishingly unlikely
// four times running, so four is enough -- and it matters that the number is small, because
// accel_gyro_bmi270_get_data() logs an error to the console when the device handle is null, and a
// console write BLOCKS the calling task when no host is draining the port.
[[maybe_unused]] constexpr int kZeroReadsToGiveUp = 4;

bool g_hasImu = true;
int g_zeroReads = 0;
float g_rateDps = 0.0f;
uint32_t g_suppressUntil = 0;
bool g_everSuppressed = false;

}  // namespace

void selfMotionBegin()
{
    g_hasImu = true;
    g_zeroReads = 0;
    g_rateDps = 0.0f;
    g_suppressUntil = 0;
    g_everSuppressed = false;
}

void selfMotionSample(uint32_t nowMs)
{
    if (!g_hasImu) return;

#ifdef ESP_PLATFORM
    // Zero-initialised, so if the driver bails out without writing (null handle) we observe zeros
    // and count toward giving up rather than reading a stale stack frame.
    struct bmi2_sens_data d = {};
    accel_gyro_bmi270_get_data(&d);

    if (d.gyr.x == 0 && d.gyr.y == 0 && d.gyr.z == 0) {
        if (++g_zeroReads >= kZeroReadsToGiveUp) {
            g_hasImu = false;
            g_rateDps = 0.0f;
        }
        return;
    }
    g_zeroReads = 0;

    // MAGNITUDE, so no axis mapping is needed and no sign can be wrong. The vendor's own conversion
    // negates and swaps axes to match the panel's orientation; none of that matters to a rotation
    // rate magnitude, which is invariant under it. One fewer thing that can only be checked by
    // picking the board up.
    const float gx = (float)d.gyr.x;
    const float gy = (float)d.gyr.y;
    const float gz = (float)d.gyr.z;
    g_rateDps = sqrtf(gx * gx + gy * gy + gz * gz) / 32.768f;
#else
    // Desktop build: no IMU, and no gate. Frame differencing on the desktop is only ever run
    // against synthetic input by the self-test, which does not move a camera.
    g_hasImu = false;
    g_rateDps = 0.0f;
    (void)nowMs;
    return;
#endif

    if (g_rateDps > kGyroDps) {
        g_suppressUntil = nowMs + kSettleMs;
        g_everSuppressed = true;
    }
}

void selfMotionNoteHeadCommand(uint32_t nowMs, uint32_t durMs)
{
    const uint32_t until = nowMs + durMs + kSettleMs;
    // Extend, never shorten: two overlapping commands must not let the second one's shorter window
    // cancel the first one's.
    if (!g_suppressUntil || !due(until, g_suppressUntil)) g_suppressUntil = until;
    g_everSuppressed = true;
}

bool selfMotionSuppressed(uint32_t nowMs)
{
    if (!g_everSuppressed) return false;   // nothing has ever set a window; 0 is not "now"
    return !due(nowMs, g_suppressUntil);
}

float selfMotionRateDps()
{
    return g_rateDps;
}

bool selfMotionHasImu()
{
    return g_hasImu;
}
