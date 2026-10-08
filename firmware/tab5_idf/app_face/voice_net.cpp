#include "voice_net.h"
#include "percept/percept_config.h"

#if !defined(OPENDUBS_VOICE)

bool        voiceNetBegin()      { return false; }
void        voiceNetSetThor(const char*, uint16_t) {}
bool        voiceNetHaveThor()   { return false; }
bool        voiceNetSendClip(const ClipMeta&, const int16_t*, Reply* o)
{
    if (o) { *o = Reply{}; o->status = ReplyStatus::Malformed; }
    return false;
}
bool        voiceNetSendNoSpeech(uint32_t, uint32_t, uint32_t, float, Reply* o)
{
    if (o) { *o = Reply{}; o->status = ReplyStatus::Malformed; }
    return false;
}
void        voiceNetInterrupt()  {}
const char* voiceNetStatus()     { return "voice-net: compiled out"; }

#else

#include "cues.h"
#include "voice/stream.h"
#include "voice/resample.h"   // kClipRate: the contract rate the reply must be in
#include "shared/alloc.h"
#include <atomic>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <vector>

#ifdef ESP_PLATFORM
#include <hal/hal.h>
#endif

#ifdef ESP_PLATFORM
#include <lwip/netdb.h>
#include <lwip/sockets.h>
#include <mooncake_log.h>
#else
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace {

// Generous, because the Thor may be doing ASR before it answers, and short enough that a head
// whose Thor has gone away does not sit in the capture task for a minute. The capture task is idle
// between presses, so this delay costs nothing a person would notice.
constexpr int kTimeoutSec = 8;

char     g_host[64] = "";
uint16_t g_port     = 0;
uint32_t g_sent     = 0;
uint32_t g_failed   = 0;
char     g_last[80] = "never sent";
uint32_t g_spokenMs = 0;

// ---- the speaker side ------------------------------------------------------------------------
//
// THREE BUFFERS, ALLOCATED ONCE, and the sizes are the reason they are not std::vectors by
// default: 9600 int16s is 19.2 kB, which is over
// CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384 and therefore lands in PSRAM -- but only because it is
// over. Being explicit beats depending on a threshold nobody wrote down (shared/alloc.h).
//
// `g_play` IS the stream's output buffer, not a copy of it: audioPlay() takes a vector, so the
// stream writes straight into the vector's storage and the sink plays it in place.
std::vector<int16_t> g_play;        // 48 kHz stereo, handed to audioPlay
int16_t* g_stage = nullptr;         // 16 kHz mono staging
std::atomic<bool> g_interrupt{false};

void playSink(const int16_t* stereo48k, int count, void*)
{
#ifdef ESP_PLATFORM
    (void)stereo48k;   // it IS g_play.data(); see g_play above
    if (count <= 0 || g_play.empty()) return;
    // SHRINK FOR A PARTIAL BLOCK, then restore. audioPlay writes the whole vector, so the tail of
    // a reply would otherwise play kSpeakOutInt16s worth -- the last partial chunk followed by
    // whatever the previous chunk left in the buffer, i.e. a stutter on the final word.
    const size_t full = g_play.size();
    if ((size_t)count < full) g_play.resize((size_t)count);
    // THE SYNCHRONOUS PATH, deliberately: it is a bare blocking i2s_write, so it returns when the
    // driver has taken the audio and the next chunk can follow seamlessly. The async path copies
    // into a task and REFUSES while that task is busy, which cannot stream.
    GetHAL()->audioPlay(g_play, false);
    if (g_play.size() != full) g_play.resize(full);
#else
    (void)stereo48k;
    (void)count;
#endif
}

void note(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void note(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_last, sizeof(g_last), fmt, ap);
    va_end(ap);
}

int dial()
{
    if (!g_host[0] || !g_port) return -1;

    char portStr[8];
    snprintf(portStr, sizeof(portStr), "%u", (unsigned)g_port);
    addrinfo hints{};
    hints.ai_family   = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    if (getaddrinfo(g_host, portStr, &hints, &res) != 0 || !res) {
        note("resolve failed: %s", g_host);
        return -1;
    }
    const int fd = socket(res->ai_family, res->ai_socktype, 0);
    if (fd < 0) { freeaddrinfo(res); note("socket() failed"); return -1; }

    // BOTH TIMEOUTS, because the two failures are different and both happen: a Thor that is off
    // refuses or hangs on connect, and a Thor that is wedged mid-ASR accepts and never answers.
    timeval tv{kTimeoutSec, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    if (connect(fd, res->ai_addr, res->ai_addrlen) != 0) {
        freeaddrinfo(res);
        close(fd);
        note("connect failed: %s:%u", g_host, (unsigned)g_port);
        return -1;
    }
    freeaddrinfo(res);
    return fd;
}

bool sendAll(int fd, const void* p, size_t n)
{
    const char* c = (const char*)p;
    while (n) {
        const int w = (int)send(fd, c, n, 0);
        if (w <= 0) return false;
        c += w;
        n -= (size_t)w;
    }
    return true;
}

// Read the streamed body and speak it as it arrives. `pre`/`preLen` is the part of the body that
// already came in with the header.
//
// THE SPEAKER IS CLAIMED FOR THE WHOLE REPLY, because the synchronous audioPlay path has no mutex
// and an earcon on the async path would interleave into the same I2S device. See cuesSuspend().
void speakStream(int fd, const char* pre, int preLen)
{
    if (g_play.empty() || !g_stage) { note("no speak buffers"); return; }

    g_interrupt.store(false);
    cuesSuspend(true);

    Stream st;
    streamBegin(&st, playSink, nullptr, g_stage, g_play.data());

    bool framing = true;
    if (preLen > 0) framing = streamFeed(&st, (const uint8_t*)pre, preLen);

    char buf[1024];
    while (framing && !streamComplete(st)) {
        if (g_interrupt.load()) { streamStop(&st); break; }
        const int r = (int)recv(fd, buf, sizeof(buf), 0);
        if (r <= 0) break;
        framing = streamFeed(&st, (const uint8_t*)buf, r);
    }

    cuesSuspend(false);
    g_spokenMs = st.samples * 1000u / (uint32_t)kClipRate;

    if (!framing)                 note("streamed body framing error");
    else if (g_interrupt.load())  note("reply cut short at %lums", (unsigned long)g_spokenMs);
    else if (!streamComplete(st)) note("stream ended with no terminator at %lums",
                                       (unsigned long)g_spokenMs);
}

// One exchange. `hdr` is the framed header line; `pcm`/`bytes` the payload, which may be empty.
bool exchange(const char* hdr, int hlen, const void* pcm, size_t bytes, Reply* out)
{
    // FAIL CLOSED. A caller must never be able to read success out of a send that did not happen;
    // the loopback harness had exactly this bug and it is the reason this is the first statement.
    if (out) { *out = Reply{}; out->status = ReplyStatus::Malformed; }

    const int fd = dial();
    if (fd < 0) { ++g_failed; return false; }

    bool ok = sendAll(fd, hdr, (size_t)hlen);
    if (ok && bytes) ok = sendAll(fd, pcm, bytes);
    if (!ok) {
        close(fd);
        ++g_failed;
        note("send failed after %u bytes", (unsigned)bytes);
        return false;
    }
    // Half-close so the far end sees EOF and knows the payload is complete without having to
    // trust the byte count twice.
    shutdown(fd, SHUT_WR);

    // Read until the header's newline, keeping whatever body bytes came with it.
    char rx[1024];
    int  have = 0;
    int  nl   = -1;
    while (have < (int)sizeof(rx) - 1) {
        const int r = (int)recv(fd, rx + have, sizeof(rx) - 1 - (size_t)have, 0);
        if (r <= 0) break;
        have += r;
        for (int i = 0; i < have; ++i) if (rx[i] == '\n') { nl = i; break; }
        if (nl >= 0) break;
    }
    if (nl < 0) { close(fd); ++g_failed; note("no reply within %ds", kTimeoutSec); return false; }

    if (!out || !wireParseReply(rx, nl + 1, out)) {
        close(fd);
        ++g_failed;
        note("unparseable reply");
        return false;
    }

    if (out->stream) {
        if (!wireReplyFormatOk(*out)) {
            // REFUSED, NOT PLAYED. 16 kHz mono is the contract; anything else handed to the
            // 48 kHz stereo sink would play at the wrong speed and pitch, which sounds like a
            // broken voice rather than like a protocol error.
            close(fd);
            ++g_failed;
            note("reply format %luHz/%uch refused", (unsigned long)out->rate,
                 (unsigned)out->channels);
            out->status = ReplyStatus::Malformed;
            return false;
        }
        speakStream(fd, rx + nl + 1, have - (nl + 1));
    }
    close(fd);
    ++g_sent;
    note("seq=%lu %s%s", (unsigned long)out->seq, replyStatusName(out->status),
         out->stream ? " spoken" : "");
    return true;
}

} // namespace

bool voiceNetBegin()
{
    g_sent = g_failed = 0;
    snprintf(g_last, sizeof(g_last), "never sent");

    // Allocated once, never freed -- the same no-steady-state-allocation rule percept/ follows.
    g_play.assign(kSpeakOutInt16s, 0);
    g_stage = (int16_t*)bigAlloc((size_t)kSpeakStageSamples * sizeof(int16_t));
    if (!g_stage) {
        // Degrade: clips still go UP, the head simply cannot speak the answer. Better than
        // refusing to start, and micStatus()/voiceNetStatus() say so.
        note("speak staging buffer failed -- replies cannot be spoken");
        return false;
    }
    return true;
}

void voiceNetSetThor(const char* host, uint16_t port)
{
    if (!host) return;
    snprintf(g_host, sizeof(g_host), "%s", host);
    g_port = port;
    note("thor set to %s:%u", g_host, (unsigned)port);
}

bool voiceNetHaveThor()
{
    return g_host[0] != '\0' && g_port != 0;
}

bool voiceNetSendClip(const ClipMeta& m, const int16_t* pcm, Reply* out)
{
    char hdr[512];
    const int hl = wireClipHeader(hdr, sizeof(hdr), m);
    if (hl <= 0) {
        if (out) { *out = Reply{}; out->status = ReplyStatus::Malformed; }
        note("header did not fit");
        return false;
    }
    return exchange(hdr, hl, pcm, (size_t)m.samples * 2, out);
}

bool voiceNetSendNoSpeech(uint32_t seq, uint32_t beganMs, uint32_t endedMs, float floor,
                          Reply* out)
{
    char hdr[256];
    const int hl = wireNoSpeech(hdr, sizeof(hdr), seq, beganMs, endedMs, floor);
    if (hl <= 0) {
        if (out) { *out = Reply{}; out->status = ReplyStatus::Malformed; }
        return false;
    }
    return exchange(hdr, hl, nullptr, 0, out);
}

void voiceNetInterrupt()
{
    g_interrupt.store(true);
}

const char* voiceNetStatus()
{
    // 256, sized from the parts rather than guessed: g_host is 64, g_last is 80, and the fixed
    // text and counters are ~50 more. The device toolchain enables -Werror=format-truncation and
    // rejected 160 outright -- a warning the host build does not turn on, so this was caught only
    // by compiling for the P4.
    static char buf[256];
    snprintf(buf, sizeof(buf), "thor=%s:%u sent=%lu failed=%lu spoke=%lums%s last=\"%s\"",
             g_host[0] ? g_host : "(unset)", (unsigned)g_port,
             (unsigned long)g_sent, (unsigned long)g_failed, (unsigned long)g_spokenMs,
             g_stage ? "" : " NO-SPEAK-BUF", g_last);
    return buf;
}

#endif
