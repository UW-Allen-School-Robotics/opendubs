#include "wire.h"
#include "resample.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

char dirCode(VoiceDir d)
{
    switch (d) {
        case VoiceDir::Left:   return 'L';
        case VoiceDir::Right:  return 'R';
        case VoiceDir::Centre: return 'C';
        default:               return 'N';
    }
}

// Find `key=` in a header line and return the value's start, or nullptr.
//
// MATCHES ON A KEY BOUNDARY, not a bare substring: a plain strstr for "seq=" would also match
// inside "lastseq=", and a parser that silently reads the wrong field is worse than one that
// fails. So the match must be at the start of the line or preceded by a space.
const char* value(const char* line, const char* key)
{
    const size_t kl = strlen(key);
    for (const char* p = line; (p = strstr(p, key)) != nullptr; p += kl) {
        if (p != line && p[-1] != ' ') continue;
        if (p[kl] != '=') continue;
        return p + kl + 1;
    }
    return nullptr;
}

} // namespace

int wireClipHeader(char* out, size_t cap, const ClipMeta& m)
{
    // %.0f on the levels: they are RMS on a 0..32768 scale and the fractional part is noise. One
    // less thing for a parser on the other side to disagree about.
    const int n = snprintf(out, cap,
        "%s CLIP seq=%lu began=%lu ended=%lu dir=%c floor=%.0f peak=%.0f "
        "rate=%d ch=1 bits=16 samples=%lu bytes=%lu%s\n",
        OPENDUBS_VOICE_WIRE,
        (unsigned long)m.seq, (unsigned long)m.beganMs, (unsigned long)m.endedMs,
        dirCode(m.dir), (double)m.floor, (double)m.peak,
        kClipRate, (unsigned long)m.samples, (unsigned long)m.samples * 2,
        m.truncated ? " truncated=1" : "");
    // snprintf returns what it WOULD have written, so a truncated header reports a length past the
    // buffer. Returning 0 rather than that is the difference between "it did not fit" and sending
    // a header chopped in half.
    if (n < 0 || (size_t)n >= cap) return 0;
    return n;
}

int wireNoSpeech(char* out, size_t cap, uint32_t seq, uint32_t beganMs, uint32_t endedMs,
                 float floor)
{
    const int n = snprintf(out, cap,
        "%s NOSPEECH seq=%lu began=%lu ended=%lu floor=%.0f\n",
        OPENDUBS_VOICE_WIRE, (unsigned long)seq, (unsigned long)beganMs,
        (unsigned long)endedMs, (double)floor);
    if (n < 0 || (size_t)n >= cap) return 0;
    return n;
}

const char* replyStatusName(ReplyStatus s)
{
    switch (s) {
        case ReplyStatus::Ok:             return "ok";
        case ReplyStatus::Unintelligible: return "unintelligible";
        case ReplyStatus::Declined:       return "declined";
        case ReplyStatus::Busy:           return "busy";
        default:                          return "malformed";
    }
}

bool wireParseReply(const char* in, int n, Reply* out)
{
    if (!out) return false;
    *out = Reply{};
    out->status = ReplyStatus::Malformed;      // FAIL CLOSED: see wire.h
    if (!in || n <= 0) return false;

    // Copy to a bounded, NUL-terminated scratch so the string functions below cannot run off the
    // end of a buffer that arrived from the network without a terminator.
    char line[512];
    int len = n;
    if (len > (int)sizeof(line) - 1) len = (int)sizeof(line) - 1;
    memcpy(line, in, (size_t)len);
    line[len] = '\0';
    if (char* nl = strchr(line, '\n')) *nl = '\0';

    if (strncmp(line, OPENDUBS_VOICE_WIRE " REPLY ", sizeof(OPENDUBS_VOICE_WIRE " REPLY ") - 1) != 0)
        return false;

    const char* v = value(line, "seq");
    if (!v) return false;
    out->seq = (uint32_t)strtoul(v, nullptr, 10);

    v = value(line, "status");
    if (!v) return false;
    if      (strncmp(v, "ok", 2) == 0 && (v[2] == ' ' || v[2] == '\0')) out->status = ReplyStatus::Ok;
    else if (strncmp(v, "unintelligible", 14) == 0) out->status = ReplyStatus::Unintelligible;
    else if (strncmp(v, "declined", 8) == 0)        out->status = ReplyStatus::Declined;
    else if (strncmp(v, "busy", 4) == 0)            out->status = ReplyStatus::Busy;
    else return false;                              // an unknown status is NOT an Ok

    // Optional. A missing `bytes` means no audio, which is a legitimate "answer without speaking".
    v = value(line, "bytes");
    out->sayBytes = v ? (uint32_t)strtoul(v, nullptr, 10) : 0;

    // Optional, and "-" means none -- the existing EMOTE/STATE vocabulary, not a new one.
    v = value(line, "face");
    if (v && *v != '-') {
        size_t i = 0;
        while (v[i] && v[i] != ' ' && i < sizeof(out->face) - 1) { out->face[i] = v[i]; ++i; }
        out->face[i] = '\0';
    }
    return true;
}
