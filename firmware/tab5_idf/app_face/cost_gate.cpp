#include "cost_gate.h"

#ifdef ESP_PLATFORM

// Nothing on the device. The measurement is only meaningful as a GATE, run before flashing, and
// the firmware should not carry an instrument it cannot act on.
void costGateBegin() {}
void costGateReport(bool) {}

#else

#include <lvgl.h>
#include <cstdio>
#include <cmath>

namespace {

// Measured on the device: a 290 k px sprite at 55.1 fps in the sprite bench. See
// notes/design/tab5-platform.md 6b.
constexpr double kDeviceBlitRate = 16.0e6;     // px/s
constexpr double kDeviceFps      = 30.0;       // what we want the device to hold
constexpr int    kScreenPx       = 1280 * 720;

// PER FRAME, NORMALISED BY ACTUAL REFRESH COUNT. Two earlier versions of this got the metric wrong
// in opposite directions, and the reason is worth keeping:
//
//   * Accumulating per LV_EVENT_REFR_READY alone reported peaks of twenty screens "per frame",
//     because LVGL only refreshes when there is work, so invalidations pile up across quiet
//     periods and land in whichever frame follows.
//   * Measuring pixels per SECOND imported the DESKTOP's frame rate, which is hundreds of fps
//     against the device's thirty -- and that destroys the only property that makes this gate
//     transferable at all.
//
// Dirty area PER FRAME is geometric: the same layout invalidates the same pixels on both machines.
// Pixels per second is not, because it depends on how fast the machine renders. So: sum the dirty
// area, divide by the number of frames actually rendered, and scale by the DEVICE's target rate.
uint64_t g_dirty  = 0;
uint64_t g_frames = 0;
uint64_t g_peakFrame = 0;
uint64_t g_sinceRefr = 0;

void onInvalidate(lv_event_t* e)
{
    // This OVERCOUNTS: areas are summed before LVGL merges overlapping ones, and this face is
    // deliberately layered -- pupil inside body, glint inside pupil, band over everything. So the
    // absolute is an UPPER BOUND, and the gate is most trustworthy COMPARATIVELY: did a change
    // make it better or worse. That is also the question that matters when shipping a config.
    lv_area_t* a = (lv_area_t*)lv_event_get_param(e);
    if (!a) return;
    const int64_t w = (int64_t)a->x2 - a->x1 + 1;
    const int64_t h = (int64_t)a->y2 - a->y1 + 1;
    if (w <= 0 || h <= 0) return;
    const uint64_t px = (uint64_t)(w * h);
    g_dirty += px;
    g_sinceRefr += px;
}

void onRefrReady(lv_event_t*)
{
    if (g_sinceRefr > g_peakFrame) g_peakFrame = g_sinceRefr;
    g_sinceRefr = 0;
    ++g_frames;
}

} // namespace

void costGateBegin()
{
    lv_display_t* d = lv_display_get_default();
    if (!d) { printf("[cost] no display; gate inactive\n"); return; }
    lv_display_add_event_cb(d, onInvalidate, LV_EVENT_INVALIDATE_AREA, nullptr);
    lv_display_add_event_cb(d, onRefrReady, LV_EVENT_REFR_READY, nullptr);
    printf("[cost] gate active. device: %.0f fps x budget = %.1f Mpx/s, so %.0f k px/frame.\n"
           "[cost] dirty area per frame is GEOMETRIC and transfers; pixels per second does not.\n"
           "[cost] numbers OVERCOUNT overlap, so use comparatively -- lower is better.\n",
           kDeviceFps, kDeviceBlitRate / 1e6, kDeviceBlitRate / kDeviceFps / 1000.0);
    fflush(stdout);
}

void costGateReport(bool final_)
{
    if (g_frames == 0) return;
    const double perFrame = (double)g_dirty / (double)g_frames;
    const double budget   = kDeviceBlitRate / kDeviceFps;          // px per frame on the device
    const double implied  = perFrame * kDeviceFps;                 // px/s the device would need

    printf("[cost] %s frames=%llu  mean %.0fk px/frame (%.0f%% of budget, %.2f screens)  "
           "implies %.1f Mpx/s on device  peak-frame %.0fk px\n",
           final_ ? "FINAL" : "     ", (unsigned long long)g_frames,
           perFrame / 1000.0, 100.0 * perFrame / budget, perFrame / kScreenPx,
           implied / 1e6, (double)g_peakFrame / 1000.0);

    if (final_) {
        const bool pass = perFrame <= budget;
        printf("[cost] VERDICT: %s  mean frame %s the device's %.0f k px budget\n",
               pass ? "PASS" : "FAIL", pass ? "fits" : "EXCEEDS", budget / 1000.0);
        printf("[cost]   remember the overcount: a FAIL within ~2-3x may still run, and a PASS has\n"
               "[cost]   real headroom. Reduce animated AREA first -- it dominates -- then quantise\n"
               "[cost]   sprung values, then cut object count. See tab5-platform.md 6b.\n");
    }
    fflush(stdout);
}

#endif
