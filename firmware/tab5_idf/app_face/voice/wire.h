// THE WIRE FORMAT between the head and the Thor. Phase B's framing, and nothing else.
//
// This file serialises and parses. It opens no socket, which is the point: the CARRIER is a device
// concern and this is testable on a laptop against a real fake Thor over loopback.
//
// ============================================================================================
// RAW PCM, NOT OPUS -- and the plan left this open, so here is the reasoning.
//
// The workplan framed it as "16 kHz mono PCM is 256 kbit/s raw; Opus at 24 kbit/s is ~10x better".
// That arithmetic is right and the conclusion does not follow, because it is a rate comparison and
// we do not have a stream. We settled on BOUNDED CLIPS: a 3-second utterance is ~96 kB, sent once
// when someone finishes speaking, not 256 kbit/s continuously. On the LAN this robot runs on that
// is nothing, and it is not competing with video moment-to-moment -- it is a short burst between
// silences.
//
// Against that, Opus costs a dependency on the P4, flash, encode latency inside the ~150 ms budget
// Phase A step 5 sets, and a second place for the audio to be wrong. So: raw, and revisit only if
// the measured clip transfer actually hurts.
//
// Where compression WOULD earn its place is audio DOWN -- a TTS reply can be far longer than an
// utterance -- and that is gated on the streaming question below, not on this.
// ============================================================================================
//
// TEXT HEADER, BINARY PAYLOAD. The header is one readable line because every other protocol in
// this firmware is line-based and debuggable with netcat, and because a binary header saves ~100
// bytes on a 96 kB transfer -- which is not a trade worth making. The payload is raw
// little-endian int16 and is NOT text, so nothing here ever base64s audio.
#pragma once
#include "level.h"
#include <stddef.h>
#include <stdint.h>

// Bumped only on an INCOMPATIBLE change. A new optional key is not incompatible: parsers here
// ignore keys they do not know, so the Thor and the head can be upgraded in either order.
#define OPENDUBS_VOICE_WIRE "OPENDUBS-VOICE/1"

// What Phase C says the head hands over. See notes/workplan-tab5-audio.md.
struct ClipMeta {
    uint32_t seq;        // monotonic per boot; makes a reply matchable and a duplicate droppable
    uint32_t beganMs;
    uint32_t endedMs;
    VoiceDir dir;
    float    floor;      // THE HEAD'S OWN CONFIDENCE, handed over rather than hidden: a clip that
    float    peak;       // barely cleared a loud floor is a different thing from a clear one, and
                         // the Thor should be allowed to decline it rather than invent words.
    uint32_t samples;    // 16 kHz mono int16; payload is samples*2 bytes
    bool     truncated;  // the clip hit the head's cap. Honest: the Thor is told it got a fragment
};

// The header line for a clip, newline-terminated. Returns the length written, or 0 if it would not
// fit. The caller sends this and then `samples*2` bytes of payload.
int wireClipHeader(char* out, size_t cap, const ClipMeta& m);

// A press that produced no speech. Its own message, because A FAILURE TO HEAR MUST NOT ARRIVE
// LOOKING LIKE A FAILURE TO UNDERSTAND -- that is the difference between "say that again" and
// "I don't know", and the face owes the person the right one. Carries the floor and no audio.
int wireNoSpeech(char* out, size_t cap, uint32_t seq, uint32_t beganMs, uint32_t endedMs,
                 float floor);

enum class ReplyStatus : uint8_t {
    Ok = 0,
    Unintelligible,   // heard, not understood -> "say that again"
    Declined,         // understood, refused   -> the Refuse earcon
    Busy,             // not now
    Malformed,        // we could not parse the reply at all. Never silently an Ok
};

const char* replyStatusName(ReplyStatus s);

struct Reply {
    uint32_t    seq;
    ReplyStatus status;
    char        face[24];   // an EMOTE/STATE name, or empty. The existing vocabulary, not a new one
    uint32_t    sayBytes;   // how much audio follows the header, 0 for "answer without speaking"
};

// Parse a reply header. `n` is the bytes available; returns false on anything it does not
// understand, with status Malformed -- a reply we cannot read must never be mistaken for success.
bool wireParseReply(const char* in, int n, Reply* out);
