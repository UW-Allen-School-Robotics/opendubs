// THE UTTERANCE BUFFER: 16 kHz mono PCM accumulated between the onset of speech and the end of it.
//
// IT NEVER ALLOCATES. The caller hands it storage, which is what keeps this unit pure enough to
// test on a laptop while the device puts the real buffer in PSRAM via shared/alloc.h's bigAlloc().
// On this chip that distinction is not pedantic: 10 s of 16 kHz mono int16 is 320 kB against
// ~250-310 kB of internal DRAM in total, so a clip buffer that landed internal would not fit at
// all, and a std::vector would have put it there (level.h, cues.cpp).
#pragma once
#include <stdint.h>

struct Clip {
    int16_t* buf;
    uint32_t cap;         // in SAMPLES, not bytes
    uint32_t len;
    uint32_t beganMs;
    uint32_t endedMs;
    bool     truncated;   // hit the cap. Reported on the wire rather than hidden
    bool     open;
    // Peak sample magnitude of what is actually HELD, which is what Phase C hands the Thor as the
    // clip's half of "the head's own confidence". It must be measured here and not borrowed from
    // listen.cpp: that peak is of the 48 kHz capture before the anti-alias filter, so it can be
    // higher than anything present in the audio the Thor receives.
    int32_t  peak;
};

// `buf`/`cap` may be null/0 -- the clip then counts what it would have held and reports truncated,
// which is what a board that could not allocate should do rather than crash or pretend.
void clipBegin(Clip* c, int16_t* buf, uint32_t cap, uint32_t nowMs);

// Append resampled samples. Silently stops at the cap and latches `truncated`.
void clipAppend(Clip* c, const int16_t* samples, uint32_t n);

void clipEnd(Clip* c, uint32_t nowMs);

// Milliseconds of audio actually held.
uint32_t clipDurationMs(const Clip& c);
