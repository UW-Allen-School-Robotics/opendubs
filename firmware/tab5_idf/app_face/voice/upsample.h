// 16 kHz MONO -> 48 kHz STEREO, which is what the speaker actually wants. Pure compute, no HAL.
//
// THE MIRROR OF resample.h, and it exists because the output sink is not negotiable: every path in
// the vendor HAL's audioPlay() calls
//
//     codec_handle->i2s_reconfig_clk_fn(48000, 16, I2S_SLOT_MODE_STEREO)
//
// before writing. The speaker is 48 kHz stereo, full stop. Handing it 16 kHz mono PCM does not
// fail -- it PLAYS, three times too slowly and an octave and a half too low, which is a bug that
// announces itself as "the TTS voice sounds wrong" rather than as an error.
//
// INTERPOLATION NEEDS A FILTER FOR THE SAME REASON DECIMATION DOES, in mirror image. Zero-stuffing
// two samples between each input sample creates spectral IMAGES at 16 kHz +/- the signal; left
// unfiltered they come out as a metallic shimmer on top of the speech. Repeating each sample three
// times (zero-order hold) is the other tempting shortcut and is a sinc-shaped low-pass that leaves
// most of the image energy in place.
//
// So the same 15-tap filter resample.cpp uses is correct here too -- for interpolation by 3 the
// anti-imaging filter is the anti-alias filter, cutoff at the LOWER rate's Nyquist. It runs as a
// POLYPHASE though: with two of every three input samples being zero, 10 of the 15 taps would be
// multiplying zeros, so the taps are split into 3 phases of 5 and each output costs 5 MACs rather
// than 15.
#pragma once
#include <stdint.h>

struct Upsampler {
    int16_t hist[5];     // one tap per polyphase phase; see kPoly in the .cpp
    bool    primed;
};

void upsampleReset(Upsampler* u);

// `in` is `n` samples of 16 kHz mono. Writes INTERLEAVED STEREO at 48 kHz -- the same sample to
// both ears, because a TTS voice is mono and pretending otherwise would be inventing a stage.
// Returns the number of int16s written (n * 3 * 2 when there is room).
int upsampleBlock(Upsampler* u, const int16_t* in, int n, int16_t* out, int outCap);

// int16s produced for `n` input samples: 3 for the rate, 2 for the channels.
constexpr int upsampleOutFor(int n) { return n * 3 * 2; }
