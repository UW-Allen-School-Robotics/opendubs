#include "frame.h"

namespace {

FrameStats g_stats = {0, 0, 0};

// BT.601 luma, computed from the 5/6/5 fields directly rather than from expanded 8-bit channels.
//
//   r5 spans 0..31, so its weight is 0.299 * 255 / 31 = 2.459  -> 629/256
//   g6 spans 0..63, so its weight is 0.587 * 255 / 63 = 2.376  -> 608/256
//   b5 spans 0..31, so its weight is 0.114 * 255 / 31 = 0.938  -> 240/256
//
// Full white is 31*629 + 63*608 + 31*240 = 65243, i.e. 254 after the shift -- one count short of
// 255 and uniformly so, which is irrelevant to a difference metric and saves the three shifts and
// three ORs that expanding to 8 bits per channel would cost per pixel.
inline uint8_t luma565(uint16_t p)
{
    const int r5 = (p >> 11) & 0x1f;
    const int g6 = (p >> 5) & 0x3f;
    const int b5 = p & 0x1f;
    return (uint8_t)((r5 * 629 + g6 * 608 + b5 * 240) >> 8);
}

}  // namespace

void frameSample(const uint16_t* src, int srcW, int srcH,
                 uint8_t* luma, uint16_t* preview, uint8_t* rgb888)
{
    if (!src || !luma || srcW <= 0 || srcH <= 0) return;

    unsigned sum = 0;
    unsigned lo = 255, hi = 0;

    for (int y = 0; y < kPerceptH; ++y) {
        // Integer row mapping, recomputed per row rather than accumulated, so a rounding error
        // cannot drift down the frame.
        const uint16_t* srow = src + (size_t)((y * srcH) / kPerceptH) * (size_t)srcW;
        uint8_t* lrow = luma + (size_t)y * kPerceptW;
        uint16_t* prow = preview ? preview + (size_t)y * kPerceptW : nullptr;
        uint8_t* drow = rgb888 ? rgb888 + (size_t)y * kPerceptW * 3 : nullptr;

        for (int x = 0; x < kPerceptW; ++x) {
            const uint16_t p = srow[(x * srcW) / kPerceptW];
            const uint8_t l = luma565(p);
            lrow[x] = l;
            sum += l;
            if (l < lo) lo = l;
            if (l > hi) hi = l;
            if (prow) prow[x] = p;
            if (drow) {
                drow[x * 3 + 0] = (uint8_t)(((p >> 11) & 0x1f) << 3);
                drow[x * 3 + 1] = (uint8_t)(((p >> 5) & 0x3f) << 2);
                drow[x * 3 + 2] = (uint8_t)((p & 0x1f) << 3);
            }
        }
    }

    g_stats.lo = (uint8_t)lo;
    g_stats.hi = (uint8_t)hi;
    g_stats.mean = (uint8_t)(sum / (unsigned)(kPerceptW * kPerceptH));
}

FrameStats frameLastStats()
{
    return g_stats;
}
