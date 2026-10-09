// THE STREAMED REPLY: TTS audio arriving in chunks and reaching the speaker as it arrives.
//
// Pure compute. It opens no socket, calls no HAL, and ALLOCATES NOTHING -- the caller hands it its
// buffers, exactly like clip.h -- so the whole framing-plus-conversion path is testable on a laptop
// against a fake Thor that really streams.
//
// ============================================================================================
// WHY STREAMING IS WHAT MAKES THE ROBOT INTERRUPTIBLE, which is not the reason the plan gives.
//
// The plan wants streaming so "the robot does not pause mid-sentence" -- a latency argument. The
// bigger payoff is different. hal.h has audioPlay() and NO counterpart: no stop, no abort, no
// is-it-playing. With one long playback call there is therefore no way to cut a reply short, and a
// robot that cannot be interrupted until it finishes a sentence is a much worse thing than one that
// cannot be interrupted mid-word.
//
// Chunked playback dissolves that. Each chunk is its own audioPlay call, so STOPPING IS SIMPLY NOT
// FEEDING THE NEXT ONE -- no HAL support required, and the interruption granularity becomes one
// chunk (~100 ms) instead of a whole utterance.
//
// Be precise about what that recovers, though. We gave up barge-in when we chose half-duplex, and
// this does not give it back in the form we gave it up: the head is still deaf while the speaker is
// active, so it cannot hear someone TALK over it. What it gives back is BUTTON barge-in -- a press
// cuts the reply off inside ~100 ms -- and with push-to-talk that is the natural gesture anyway.
// ============================================================================================
//
// THE FRAMING. A streamed body has no length up front, because a TTS engine emits before it has
// finished synthesising. So the body is a sequence of chunks:
//
//     <u32 little-endian length><length bytes of 16 kHz mono int16 PCM>
//     ...
//     <u32 little-endian 0>            <- the terminator
//
// A zero-length chunk is the end. A truncated connection is NOT an end and is reported as such:
// silently treating a dropped stream as a complete reply would have the robot stop mid-sentence and
// look like it had finished.
#pragma once
#include "upsample.h"
#include <stdint.h>

// Receives 48 kHz INTERLEAVED STEREO, ready to hand to the speaker untouched.
typedef void (*SpeakSink)(const int16_t* stereo48k, int count, void* user);

// 100 ms of 16 kHz mono staged before each conversion. That figure is doing two jobs: it is the
// playback granularity AND the interruption granularity, so it is the shortest a reply can be cut
// off. Shorter would interrupt faster and risk the speaker running dry between chunks.
constexpr int kSpeakStageSamples = 1600;
constexpr int kSpeakOutInt16s    = kSpeakStageSamples * 3 * 2;   // 9600: x3 rate, x2 channels

enum class StreamState : uint8_t {
    Length = 0,   // reading the 4-byte length prefix
    Payload,      // reading that many bytes
    Done,         // the terminator arrived: a COMPLETE reply
    Error,        // framing we could not follow
};

struct Stream {
    SpeakSink sink;
    void*     user;

    int16_t*  stage;      // kSpeakStageSamples of 16 kHz mono, caller-owned
    int16_t*  out;        // kSpeakOutInt16s of 48 kHz stereo, caller-owned
    int       staged;

    Upsampler up;
    StreamState st;

    uint8_t   lenBuf[4];
    int       lenGot;
    uint32_t  need;       // bytes still expected in the current chunk
    uint8_t   odd;        // a payload byte split across two feeds
    bool      haveOdd;

    bool      stopped;
    uint32_t  samples;    // 16 kHz samples actually spoken, for the status line
    uint32_t  chunks;
};

void streamBegin(Stream* s, SpeakSink sink, void* user, int16_t* stage, int16_t* out);

// Feed bytes as they arrive off the wire, in any sizes. Returns false on a framing error.
bool streamFeed(Stream* s, const uint8_t* data, int n);

// THE INTERRUPTION. After this, chunks are still parsed -- so the framing stays in step and the
// connection can be drained cleanly -- but nothing more reaches the speaker.
void streamStop(Stream* s);

// True only when the TERMINATOR arrived. A connection that just ended is not done; see the header
// comment on why that distinction matters.
bool streamComplete(const Stream& s);
