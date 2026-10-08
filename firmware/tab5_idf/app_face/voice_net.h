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
// THE REPLY'S AUDIO IS NOT IMPLEMENTED, and that is a stated gap rather than an oversight. The
// plan wants TTS "streamed, not file-at-a-time", and this request/response carrier would deliver
// it file-at-a-time -- the robot would stand silent through a whole sentence of synthesis and then
// speak. `sayBytes` is parsed and skipped, and audio-down wants chunked streaming plus a way to
// STOP the speaker, which hal.h does not have (see mic.cpp). Both are Phase B's remaining half.
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

const char* voiceNetStatus();
