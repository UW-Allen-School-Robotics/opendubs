// PHASE B END TO END, ON THE HOST: the head's real framing code talking to the fake Thor over a
// real socket. `./build.sh loopback` builds this, starts tools/fake_thor.py, and runs it.
//
// Why this exists alongside the self-test. `./build.sh voice` proves the arithmetic against
// synthetic stimulus in-process; it cannot catch a framing mistake, because a bug and its mirror
// image in the same file agree with each other. Here the OTHER END IS A DIFFERENT PROGRAM, written
// to the contract in notes/workplan-tab5-audio.md rather than to this code, and it rejects
// anything that does not meet it -- a 48 kHz payload, a bytes/samples mismatch, a short read, a
// base64'd payload, a repeated seq.
//
// The socket code is HERE and not in the firmware: wire.cpp opens nothing, which is what lets the
// framing be tested on a laptop. The device's carrier is a separate shim.
#include "voice/clip.h"
#include "voice/level.h"
#include "voice/resample.h"
#include "voice/wire.h"

#include <arpa/inet.h>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <netinet/in.h>
#include <string>
#include <sys/socket.h>
#include <unistd.h>
#include <vector>

namespace {

int g_fails = 0;

void ok(const char* what)   { std::printf("  ok   %s\n", what); }
void bad(const char* w, const std::string& d)
{
    std::printf("  FAIL: %s -- %s\n", w, d.c_str());
    ++g_fails;
}

// ---- synthetic speech, in the device's real capture format ----------------------------------
// Amplitude-modulated so it is not a steady tone: a clip of constant amplitude would not exercise
// the peak tracking, and the fake Thor's "entirely silent" check would pass trivially either way.
constexpr int kFrames = 1536;              // 32 ms at 48 kHz, as the device captures

void speechBlock(int16_t* buf, int startFrame, float hz)
{
    for (int i = 0; i < kFrames; ++i) {
        const int   t  = startFrame + i;
        const float ph = 6.2831853f * hz * (float)t / (float)kVoiceRate;
        const float env = 0.45f + 0.55f * sinf(6.2831853f * 3.0f * (float)t / (float)kVoiceRate);
        const int16_t v = (int16_t)(sinf(ph) * 9000.0f * env);
        int16_t* f = &buf[kVoiceChans * i];
        f[kVoiceMicL]   = v;
        f[kVoiceMicR]   = (int16_t)(v / 3);          // leaning left, so dir should read L
        f[kVoiceAecRef] = 14000;                     // decoys: must never reach the payload
        f[kVoiceMicHp]  = -14000;
    }
}

// ---- a minimal client ------------------------------------------------------------------------
// RETRIES, rather than the driver probing the port first. The obvious way to wait for the server
// is to connect to it -- which CONSUMES the connection that a --once server is there to accept,
// so the probe itself made every case fail. Retrying here needs no probe and is what the head
// would do anyway.
int dial(int port)
{
    for (int attempt = 0; attempt < 100; ++attempt) {
        const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_port   = htons((uint16_t)port);
        a.sin_addr.s_addr = inet_addr("127.0.0.1");
        if (::connect(fd, (sockaddr*)&a, sizeof(a)) == 0) return fd;
        ::close(fd);
        usleep(50 * 1000);
    }
    return -1;
}

bool sendAll(int fd, const void* p, size_t n)
{
    const char* c = (const char*)p;
    while (n) {
        const ssize_t w = ::send(fd, c, n, 0);
        if (w <= 0) return false;
        c += w;
        n -= (size_t)w;
    }
    return true;
}

// Build a clip from `ms` of synthetic speech through the REAL resampler and clip buffer.
void buildClip(Clip* c, std::vector<int16_t>* store, int ms, uint32_t cap)
{
    store->assign(cap, 0);
    clipBegin(c, store->data(), cap, 1000);

    Resampler r;
    resampleReset(&r);
    std::vector<int16_t> in((size_t)kFrames * kVoiceChans);
    std::vector<int16_t> out(resampleMaxOut(kFrames));
    const int blocks = ms / 32;
    for (int b = 0; b < blocks; ++b) {
        speechBlock(in.data(), b * kFrames, 420.0f);
        const int got = resampleBlock(&r, in.data(), kFrames, out.data(), (int)out.size());
        clipAppend(c, out.data(), (uint32_t)got);
    }
    clipEnd(c, 1000 + (uint32_t)ms);
}

// One exchange: header + payload up, reply down and parsed by the head's own parser.
bool exchange(int port, const char* header, int hlen, const int16_t* pcm, uint32_t samples,
              Reply* reply, std::string* err)
{
    // FAIL CLOSED FROM THE FIRST LINE, mirroring wireParseReply. This was a real bug in this
    // harness: on a transport failure the function returned early and left the caller's Reply at
    // its default-constructed status, which is Ok -- so a connection that never happened read as
    // a successful exchange. Exactly the class of mistake this harness exists to catch, in the
    // harness itself.
    *reply = Reply{};
    reply->status = ReplyStatus::Malformed;

    const int fd = dial(port);
    if (fd < 0) { *err = "could not connect"; return false; }
    bool okSend = sendAll(fd, header, (size_t)hlen);
    if (okSend && samples) okSend = sendAll(fd, pcm, (size_t)samples * 2);
    if (!okSend) { ::close(fd); *err = "send failed"; return false; }
    ::shutdown(fd, SHUT_WR);

    char rx[512];
    const ssize_t n = ::recv(fd, rx, sizeof(rx) - 1, 0);
    ::close(fd);
    if (n <= 0) { *err = "no reply"; return false; }
    rx[n] = '\0';
    if (!wireParseReply(rx, (int)n, reply)) {
        *err = std::string("unparseable reply: ") + rx;
        return false;                           // reply->status is Malformed; see wire.h
    }
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    const int   port = (argc > 1) ? atoi(argv[1]) : 9400;
    const char* mode = (argc > 2) ? argv[2] : "clip";

    Clip c{};
    std::vector<int16_t> store;
    char hdr[512];
    Reply reply{};
    std::string err;

    if (strcmp(mode, "clip") == 0) {
        // 1.5 s of speech through the real pipeline, into a 10 s buffer so nothing truncates.
        buildClip(&c, &store, 1504, 10u * kClipRate);
        if (c.truncated)            { bad("clip", "truncated with a 10 s buffer"); return 1; }
        if (clipDurationMs(c) < 1400) {
            bad("clip", "only " + std::to_string(clipDurationMs(c)) + " ms accumulated");
            return 1;
        }
        ok(("clip: " + std::to_string(clipDurationMs(c)) + " ms of 16 kHz mono accumulated").c_str());

        const ClipMeta m{1, c.beganMs, c.endedMs, VoiceDir::Left, 120.0f, 6300.0f, c.len,
                         c.truncated};
        const int hl = wireClipHeader(hdr, sizeof(hdr), m);
        if (hl <= 0) { bad("header", "did not fit"); return 1; }

        if (!exchange(port, hdr, hl, c.buf, c.len, &reply, &err)) { bad("clip exchange", err); return 1; }
        if (reply.status != ReplyStatus::Ok)
            { bad("clip reply", std::string("status=") + replyStatusName(reply.status)); return 1; }
        if (reply.seq != 1) { bad("clip reply", "seq did not come back as 1"); return 1; }
        ok("the fake Thor accepted the clip and replied ok, seq matched");

    } else if (strcmp(mode, "nospeech") == 0) {
        const int hl = wireNoSpeech(hdr, sizeof(hdr), 1, 1000, 3000, 118.0f);
        if (hl <= 0) { bad("nospeech", "header did not fit"); return 1; }
        if (!exchange(port, hdr, hl, nullptr, 0, &reply, &err)) { bad("nospeech", err); return 1; }
        if (reply.status != ReplyStatus::Ok) { bad("nospeech", "not accepted"); return 1; }
        ok("NOSPEECH accepted as its own message, carrying no payload");

    } else if (strcmp(mode, "truncate") == 0) {
        // 4 s of speech into a 1 s buffer: the clip must SAY it is a fragment, and the fake Thor
        // must see truncated=1 rather than a confident short clip.
        buildClip(&c, &store, 4000, 1u * kClipRate);
        if (!c.truncated)  { bad("truncate", "a 1 s buffer swallowed 4 s without complaint"); return 1; }
        if (c.len != kClipRate) { bad("truncate", "length is not exactly the cap"); return 1; }
        const ClipMeta m{1, c.beganMs, c.endedMs, VoiceDir::Right, 120.0f, 6300.0f, c.len, true};
        const int hl = wireClipHeader(hdr, sizeof(hdr), m);
        if (!strstr(hdr, "truncated=1")) { bad("truncate", "the header hid the truncation"); return 1; }
        if (!exchange(port, hdr, hl, c.buf, c.len, &reply, &err)) { bad("truncate", err); return 1; }
        ok("a clip that hit the cap is sent as truncated=1, not as a confident fragment");

    } else if (strcmp(mode, "badreply") == 0) {
        // THE HALF THAT NORMALLY GOES UNTESTED. An unreadable reply must come back Malformed and
        // never as a silent success.
        buildClip(&c, &store, 320, 10u * kClipRate);
        const ClipMeta m{1, c.beganMs, c.endedMs, VoiceDir::Centre, 120.0f, 6300.0f, c.len, false};
        const int hl = wireClipHeader(hdr, sizeof(hdr), m);
        const bool parsed = exchange(port, hdr, hl, c.buf, c.len, &reply, &err);
        if (parsed) { bad("badreply", "garbage parsed as a valid reply"); return 1; }
        if (reply.status != ReplyStatus::Malformed)
            { bad("badreply", std::string("status=") + replyStatusName(reply.status)); return 1; }
        ok("an unparseable reply reads as MALFORMED, never as ok");

    } else {
        std::printf("usage: %s <port> {clip|nospeech|truncate|badreply}\n", argv[0]);
        return 2;
    }
    return g_fails ? 1 : 0;
}
