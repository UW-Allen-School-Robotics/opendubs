// PHASE B's CARRIER: the clip's trip to the Thor. The only file in the voice path that opens a
// socket.
//
// WHY A TCP CONNECTION PER UTTERANCE, which is the carrier decision the workplan left open.
//
// The plan ruled out overloading udp/4242 -- "the existing StamPLC protocol is line-based UDP and
// was never built to carry a payload" -- and it is right for a second reason: a clip is ~96 kB and
// UDP would mean fragmenting it, numbering the chunks, and inventing retransmission for the ones
// that drop. A dropped chunk does not degrade an utterance, it corrupts it, so that reliability is
// not optional and TCP already has it.
//
// And the shape fits exactly. We settled on BOUNDED CLIPS, so the exchange is request/response:
// one connection, header and payload up, one reply down, close. No session, no keepalive, no
// reconnect state machine. The head already does an HTTP pull for OTA, so a second port is
// precedented rather than novel.
//
// AUDIO DOWN IS STREAMED, chunk by chunk, straight to the speaker as it arrives -- which is what
// the plan asks for ("streamed, not file-at-a-time, or the robot pauses mid-sentence") and also
// what makes the reply INTERRUPTIBLE at all. hal.h has audioPlay() and no stop; with one long
// playback call a reply could not be cut short. Each chunk being its own call means stopping is
// simply not feeding the next one. See voice/stream.h.
//
// It uses the HAL's SYNCHRONOUS audioPlay path, which is the only one that can be fed chunk by
// chunk -- and which has no mutex, so cues are suspended for the duration. See cuesSuspend().
//
// IT BLOCKS, and is therefore called only from the capture task -- which is idle between presses
// and whose whole job is already to block on audioRecord(). Never from cmdTask, which is the 20 Hz
// lift publisher, and never from the UI task.
#pragma once
#include "voice/wire.h"
#include <stdint.h>

bool voiceNetBegin();

// Where the Thor is. Runtime rather than compiled in, because the address is per-deployment and
// per-robot values do not belong in this repo. Not persisted yet: set it with `VOICE THOR <host>
// <port>` after boot. Persisting it is a settings-panel change and is deliberately not bundled in.
void voiceNetSetThor(const char* host, uint16_t port);
bool voiceNetHaveThor();

// Send a clip and wait for the reply. `pcm` is `m.samples` of 16 kHz mono int16.
// Returns false when the Thor could not be reached or did not answer intelligibly; `out` then
// carries Malformed, never a silent Ok.
bool voiceNetSendClip(const ClipMeta& m, const int16_t* pcm, Reply* out);

// A press that heard nothing. Its own message, carrying no audio -- a failure to HEAR must not
// arrive looking like a failure to UNDERSTAND.
bool voiceNetSendNoSpeech(uint32_t seq, uint32_t beganMs, uint32_t endedMs, float floor,
                          Reply* out);

// CUT THE CURRENT REPLY SHORT. Called from another task (the `VOICE PRESS` verb arrives on
// cmdTask while the reply is being spoken on the capture task), so it is just a flag the playback
// loop checks between chunks -- interruption lands within one chunk, ~100 ms.
//
// This is the barge-in we gave up when we chose half-duplex, returned in a different form: the head
// still cannot HEAR someone talk over it, but a press cuts the answer off. With push-to-talk that
// is the natural gesture anyway.
void voiceNetInterrupt();

const char* voiceNetStatus();
