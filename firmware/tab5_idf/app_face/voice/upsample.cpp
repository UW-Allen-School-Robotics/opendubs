#include "upsample.h"
#include <string.h>

namespace {

// THE SAME 15 TAPS AS resample.cpp, split into 3 polyphase phases.
//
// kPoly[p][k] = h[3*k + p], so output phase p is sum_k kPoly[p][k] * x[n-k]. The identity that
// makes this correct: zero-stuffing by 3 and convolving with h is exactly this, with the zero
// multiplies removed rather than computed.
//
// GENERATED from resample.cpp's taps rather than hand-transcribed: the first attempt at
// writing these out by eye got three of the fifteen wrong, which would have been a quiet
// distortion rather than an error. Each phase sums to ~1/3 of the whole, which is why the
// divisor below is the full tap sum over kPhases.
constexpr int kPhases = 3;
constexpr int kTapsPerPhase = 5;
constexpr int16_t kPoly[kPhases][kTapsPerPhase] = {
    {     37,   -667,   8012,   4080,   -525 },   // h[0], h[3], h[6], h[9], h[12]
    {   -128,    687,   9779,    687,   -128 },   // h[1], h[4], h[7], h[10], h[13]
    {   -525,   4080,   8012,   -667,     37 },   // h[2], h[5], h[8], h[11], h[14]
};

// GAIN. Zero-stuffing throws away two thirds of the energy, so interpolation needs a factor of
// kPhases to come back out at the amplitude it went in at. Folding it into the divisor rather than
// scaling the taps keeps the taps identical to resample.cpp's, which is the point of reusing them.
//
// The divisor is the sum of ALL 15 taps -- the actual sum, 32771, not 32768, for the same reason
// resample.cpp divides by the truth: the Q15 rounding left a +0.0008 dB error that is free to
// avoid.
constexpr int32_t kAllTapSum = 32771;
constexpr int32_t kDiv = kAllTapSum / kPhases;

inline int16_t clamp16(int32_t v)
{
    if (v >  32767) return  32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

} // namespace

void upsampleReset(Upsampler* u)
{
    if (!u) return;
    memset(u->hist, 0, sizeof(u->hist));
    u->primed = false;
}

int upsampleBlock(Upsampler* u, const int16_t* in, int n, int16_t* out, int outCap)
{
    if (!u || !in || !out || n <= 0) return 0;

    int w = 0;
    for (int i = 0; i < n; ++i) {
        const int16_t s = in[i];

        // PRIME FROM THE FIRST SAMPLE, not from zeros -- the same reasoning as resample.cpp. A
        // reply's audio starts at the first syllable of the answer, so a filter ramping up from
        // silence puts a click exactly there.
        if (!u->primed) {
            for (int k = 0; k < kTapsPerPhase; ++k) u->hist[k] = s;
            u->primed = true;
        }

        // Newest sample first: hist[0] is x[n], hist[k] is x[n-k].
        memmove(u->hist + 1, u->hist, sizeof(int16_t) * (kTapsPerPhase - 1));
        u->hist[0] = s;

        for (int p = 0; p < kPhases; ++p) {
            if (w + 2 > outCap) return w;
            int32_t acc = 0;
            for (int k = 0; k < kTapsPerPhase; ++k) acc += (int32_t)kPoly[p][k] * u->hist[k];
            const int16_t v = clamp16(acc / kDiv);
            out[w++] = v;      // interleaved stereo: the same sample both ears
            out[w++] = v;
        }
    }
    return w;
}
