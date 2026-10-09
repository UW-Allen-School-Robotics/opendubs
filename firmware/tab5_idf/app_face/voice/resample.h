// 48 kHz FOUR-CHANNEL CAPTURE -> 16 kHz MONO, which is what ASR wants. Pure compute, no HAL.
//
// Two jobs, and the second one is the one that is easy to get silently wrong.
//
//   1. MIX THE TWO MICROPHONES to mono. The bearing has already been extracted by level.cpp before
//      this runs, so the channel difference has served its purpose and the sum is just better SNR.
//      (Averaging two spaced mics does comb-filter, but at this spacing the first null sits far
//      above the speech band.) The AEC and headset slots are skipped -- see level.h on the layout.
//
//   2. DECIMATE 3:1, THROUGH A LOW-PASS FIRST. 48 kHz / 3 = 16 kHz exactly, so the decimation
//      itself is trivial -- and that is the trap. Dropping two of every three samples with no
//      anti-alias filter FOLDS everything above 8 kHz back into the band: a 12 kHz ESC whine lands
//      at 4 kHz, directly on top of the vowels, and sibilance smears into the formants. The result
//      is audio that sounds plausible and transcribes badly, which is the worst failure mode
//      available because nothing about it looks broken.
//
// So the FIR below is not decoration. Its stopband is where the alias energy would come from:
// -35.6 dB at 12 kHz and -58 dB at 16 kHz, which are the frequencies that fold to 4 kHz and 0 Hz.
//
// STREAMING, not block-at-a-time: the filter history and the decimation phase carry across calls,
// so a sentence split into 32 ms blocks produces the same samples as one long buffer would. Getting
// that wrong gives a click at every block boundary, 31 times a second.
#pragma once
#include <stdint.h>

constexpr int kClipRate = 16000;      // what Phase C hands the Thor
constexpr int kDecimate = 3;          // 48000 / 16000
constexpr int kFirTaps  = 15;

struct Resampler {
    // The tail of the previous block. kFirTaps-1 of history is exactly what the next block needs
    // to filter its first sample as though the stream had never been cut.
    int16_t hist[kFirTaps - 1];
    int     phase;                    // which of every 3 input samples is kept
    bool    primed;
};

void resampleReset(Resampler* r);

// Feed one block of 48 kHz 4-channel interleaved capture; writes 16 kHz mono samples to `out`.
// Returns how many were written. `frames` is input FRAMES; at most frames/3 + 1 are produced, so
// `outCap` must be at least that.
int resampleBlock(Resampler* r, const int16_t* in, int frames, int16_t* out, int outCap);

// How many output samples a block of `frames` can produce, for sizing a buffer.
constexpr int resampleMaxOut(int frames) { return frames / kDecimate + 1; }
