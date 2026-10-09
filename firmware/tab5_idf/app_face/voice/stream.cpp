#include "stream.h"
#include <string.h>

namespace {

// A sane ceiling on one chunk, so a corrupt length cannot make the head try to read 4 GB. 1 MB is
// ~32 seconds of 16 kHz mono: far more than any single chunk should be, and still obviously a
// framing error rather than a plausible value.
constexpr uint32_t kMaxChunkBytes = 1024u * 1024u;

// Convert and emit whatever is staged. Called when the stage fills and once at the end, so the
// tail of a reply is not dropped for being shorter than a full block.
void flush(Stream* s)
{
    if (!s || s->staged <= 0) return;
    if (!s->stopped && s->sink) {
        const int w = upsampleBlock(&s->up, s->stage, s->staged, s->out, kSpeakOutInt16s);
        if (w > 0) s->sink(s->out, w, s->user);
        s->samples += (uint32_t)s->staged;
    }
    s->staged = 0;
}

void pushSample(Stream* s, int16_t v)
{
    s->stage[s->staged++] = v;
    if (s->staged >= kSpeakStageSamples) flush(s);
}

} // namespace

void streamBegin(Stream* s, SpeakSink sink, void* user, int16_t* stage, int16_t* out)
{
    if (!s) return;
    memset(s, 0, sizeof(*s));
    s->sink  = sink;
    s->user  = user;
    s->stage = stage;
    s->out   = out;
    s->st    = StreamState::Length;
    upsampleReset(&s->up);
    // No buffers is a framing error up front rather than a crash on the first chunk.
    if (!stage || !out) s->st = StreamState::Error;
}

bool streamFeed(Stream* s, const uint8_t* data, int n)
{
    if (!s || s->st == StreamState::Error) return false;
    if (!data || n <= 0) return s->st != StreamState::Error;

    int i = 0;
    while (i < n) {
        if (s->st == StreamState::Done) return true;     // anything after the terminator is ignored

        if (s->st == StreamState::Length) {
            s->lenBuf[s->lenGot++] = data[i++];
            if (s->lenGot < 4) continue;
            s->lenGot = 0;
            const uint32_t len = (uint32_t)s->lenBuf[0]
                               | ((uint32_t)s->lenBuf[1] << 8)
                               | ((uint32_t)s->lenBuf[2] << 16)
                               | ((uint32_t)s->lenBuf[3] << 24);
            if (len == 0) {
                // THE TERMINATOR. Flush the tail before declaring it done, or the last partial
                // block of every reply is silently dropped -- which is the end of the last word.
                flush(s);
                s->st = StreamState::Done;
                return true;
            }
            if (len > kMaxChunkBytes || (len & 1u)) {
                // Odd is a framing error too: the payload is int16, so an odd byte count means we
                // are not where we think we are in the stream.
                s->st = StreamState::Error;
                return false;
            }
            s->need = len;
            s->st   = StreamState::Payload;
            ++s->chunks;
            continue;
        }

        // Payload. Assemble int16s little-endian, carrying a byte across feeds when a chunk is
        // split mid-sample -- which a socket will do, and which is the bug that would otherwise
        // appear as a burst of noise every few hundred milliseconds.
        while (i < n && s->need > 0) {
            if (!s->haveOdd) {
                s->odd     = data[i++];
                s->haveOdd = true;
                --s->need;
            } else {
                const int16_t v = (int16_t)((uint16_t)s->odd | ((uint16_t)data[i++] << 8));
                s->haveOdd = false;
                --s->need;
                pushSample(s, v);
            }
        }
        if (s->need == 0) s->st = StreamState::Length;
    }
    return true;
}

void streamStop(Stream* s)
{
    if (!s) return;
    s->stopped = true;
    s->staged  = 0;      // drop what was staged: it has not been spoken and must not be later
}

bool streamComplete(const Stream& s)
{
    return s.st == StreamState::Done;
}
