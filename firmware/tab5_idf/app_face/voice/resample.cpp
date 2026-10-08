#include "resample.h"
#include "level.h"
#include <string.h>

namespace {

// ANTI-ALIAS FIR, Q15, 15 taps, Hamming-windowed sinc, cutoff 7.2 kHz at 48 kHz.
//
// GENERATED, AND THE RECIPE IS HERE so the numbers are reproducible rather than magic:
//
//   RATE, DEC, TAPS, FC = 48000, 3, 15, 7200.0
//   for n in range(TAPS):
//       m = n - (TAPS-1)/2
//       x = 2*FC/RATE
//       s = x if m == 0 else math.sin(math.pi*x*m)/(math.pi*m)
//       h.append(s * (0.54 - 0.46*math.cos(2*math.pi*n/(TAPS-1))))
//   q = [round(c/sum(h)*32768) for c in h]
//
// 7.2 kHz rather than a flat 8 kHz: the transition band has to fit somewhere below the output's
// 8 kHz Nyquist, and speech gives up very little above 7 kHz. Measured response of these exact
// quantised taps:
//
//      100 Hz   -0.0 dB        8000 Hz   -8.5 dB
//     1000 Hz   -0.0 dB       10000 Hz  -18.0 dB
//     3000 Hz   -0.4 dB       12000 Hz  -35.6 dB   <- folds to 4 kHz, mid-vowel. The critical one.
//     6000 Hz   -3.4 dB       16000 Hz  -58.3 dB   <- folds to DC
constexpr int16_t kFir[kFirTaps] = {
    37, -128, -525, -667, 687, 4080, 8012, 9779, 8012, 4080, 687, -667, -525, -128, 37
};

// The ACTUAL sum, not 32768. Rounding to Q15 left the taps summing to 32771, so dividing by
// 32768 would add a +0.0008 dB DC gain error. Trivially small, and trivially avoidable by dividing
// by the truth instead of by the power of two, so there is no reason to carry it.
constexpr int32_t kFirSum = 37 - 128 - 525 - 667 + 687 + 4080 + 8012 + 9779
                          + 8012 + 4080 + 687 - 667 - 525 - 128 + 37;

// int32 ACCUMULATOR IS SAFE, and this is the proof rather than a hope -- if someone retunes the
// taps and breaks the headroom, this stops the build instead of producing quiet distortion on
// loud speech only.
constexpr int32_t kAbsSum = 37 + 128 + 525 + 667 + 687 + 4080 + 8012 + 9779
                          + 8012 + 4080 + 687 + 667 + 525 + 128 + 37;
static_assert((int64_t)kAbsSum * 32768 < 2147483647LL,
              "FIR gain exceeds int32: widen the accumulator or rescale the taps");

inline int16_t clamp16(int32_t v)
{
    if (v >  32767) return  32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

// Mono mix of one input frame: the two ON-BOARD microphones, skipping the AEC and headset slots.
inline int16_t mono(const int16_t* f)
{
    return (int16_t)(((int32_t)f[kVoiceMicL] + (int32_t)f[kVoiceMicR]) / 2);
}

} // namespace

void resampleReset(Resampler* r)
{
    if (!r) return;
    memset(r->hist, 0, sizeof(r->hist));
    r->phase  = 0;
    r->primed = false;
}

int resampleBlock(Resampler* r, const int16_t* in, int frames, int16_t* out, int outCap)
{
    if (!r || !in || !out || frames <= 0) return 0;

    int n = 0;
    for (int i = 0; i < frames; ++i) {
        const int16_t s = mono(&in[(size_t)kVoiceChans * i]);

        // PRIME THE HISTORY WITH THE FIRST SAMPLE, not with zeros, on the very first block only.
        // Starting from zeros makes the filter ramp up from silence over 14 samples, which is an
        // audible click at the start of every utterance -- and since a clip starts at the onset of
        // speech, that click lands exactly where the first phoneme is.
        if (!r->primed) {
            for (int k = 0; k < kFirTaps - 1; ++k) r->hist[k] = s;
            r->primed = true;
        }

        // Keep one in every kDecimate, and only compute the filter for the ones we keep: the
        // discarded two thirds would be thrown away anyway, so this is 3x less arithmetic for
        // identical output.
        const bool keep = (r->phase == 0);
        if (keep && n < outCap) {
            // The window is [history..., s]: hist[0] is the oldest.
            int32_t acc = 0;
            for (int k = 0; k < kFirTaps - 1; ++k) acc += (int32_t)kFir[k] * r->hist[k];
            acc += (int32_t)kFir[kFirTaps - 1] * s;
            out[n++] = clamp16(acc / kFirSum);
        }
        if (++r->phase >= kDecimate) r->phase = 0;

        // Slide the history. A 14-entry memmove per input sample is 48000 of them a second, which
        // on this chip is nothing and keeps the code obviously correct; a ring buffer would save
        // that and cost the next reader ten minutes.
        memmove(r->hist, r->hist + 1, sizeof(int16_t) * (kFirTaps - 2));
        r->hist[kFirTaps - 2] = s;
    }
    return n;
}
