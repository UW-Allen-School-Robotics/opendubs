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
const char* voiceNetStatus()     { return "voice-net: compiled out"; }

#else

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

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

    char rx[512];
    const int n = (int)recv(fd, rx, sizeof(rx) - 1, 0);
    close(fd);
    if (n <= 0) { ++g_failed; note("no reply within %ds", kTimeoutSec); return false; }
    rx[n] = '\0';

    if (!out || !wireParseReply(rx, n, out)) {
        ++g_failed;
        note("unparseable reply");
        return false;
    }
    ++g_sent;
    note("seq=%lu %s", (unsigned long)out->seq, replyStatusName(out->status));
    return true;
}

} // namespace

bool voiceNetBegin()
{
    g_sent = g_failed = 0;
    snprintf(g_last, sizeof(g_last), "never sent");
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

const char* voiceNetStatus()
{
    // 256, sized from the parts rather than guessed: g_host is 64, g_last is 80, and the fixed
    // text and counters are ~50 more. The device toolchain enables -Werror=format-truncation and
    // rejected 160 outright -- a warning the host build does not turn on, so this was caught only
    // by compiling for the P4.
    static char buf[256];
    snprintf(buf, sizeof(buf), "thor=%s:%u sent=%lu failed=%lu last=\"%s\"",
             g_host[0] ? g_host : "(unset)", (unsigned)g_port,
             (unsigned long)g_sent, (unsigned long)g_failed, g_last);
    return buf;
}

#endif
