#include "clip.h"
#include "resample.h"
#include <string.h>

void clipBegin(Clip* c, int16_t* buf, uint32_t cap, uint32_t nowMs)
{
    if (!c) return;
    c->buf       = buf;
    c->cap       = buf ? cap : 0;
    c->len       = 0;
    c->beganMs   = nowMs;
    c->endedMs   = nowMs;
    // NO BUFFER IS ALREADY A TRUNCATION, and saying so up front means a board that failed to
    // allocate reports a fragment honestly instead of sending a confident zero-length clip.
    c->truncated = (c->cap == 0);
    c->open      = true;
    c->peak      = 0;
}

void clipAppend(Clip* c, const int16_t* samples, uint32_t n)
{
    if (!c || !c->open || !samples || n == 0) return;
    if (!c->buf) { c->truncated = true; return; }

    uint32_t room = (c->cap > c->len) ? (c->cap - c->len) : 0;
    if (n > room) {
        n = room;
        c->truncated = true;      // latched: the Thor is told it got a fragment
    }
    if (n == 0) return;
    memcpy(c->buf + c->len, samples, (size_t)n * sizeof(int16_t));
    for (uint32_t i = 0; i < n; ++i) {
        // -32768 negates to itself in int16, so widen before taking the magnitude.
        const int32_t a = samples[i] < 0 ? -(int32_t)samples[i] : (int32_t)samples[i];
        if (a > c->peak) c->peak = a;
    }
    c->len += n;
}

void clipEnd(Clip* c, uint32_t nowMs)
{
    if (!c || !c->open) return;
    c->open    = false;
    c->endedMs = nowMs;
}

uint32_t clipDurationMs(const Clip& c)
{
    return c.len * 1000u / (uint32_t)kClipRate;
}
