// The SBUS framer: finds 25-byte radio frames in a byte stream. No Arduino in this file, so the
// same code runs in the firmware and in tests/ on a laptop.
//
// WHY THIS EXISTS (issue 642). The framer this replaces waited for a 0x0F, took the next 24 bytes
// blind, and on a failed check threw all 25 away. A frame is 25 bytes, so the hunt resumed at the
// SAME offset in the next frame. On 2026-10-26 a radio or power blip cost one byte; CH7 sat at
// 1000 and CH8 at 1800, which makes byte 10 of every frame 0x0F; the framer locked onto that byte
// again every frame, for 70 minutes, until someone turned the speed knob. Moving the sticks did
// nothing: they are not in byte 10.
//
// It had a second fault from the same cause, and that one fails unsafe: a misaligned window that
// happened to end on 0x00, 0x04, 0x14 or 0x24 was ACCEPTED, with garbage for channels.
//
// WHAT IT DOES INSTEAD. SBUS has no checksum; the only structure is that a frame starts with 0x0F
// and ends with one of four footer bytes, 25 bytes apart, frame after frame. So:
//
//   LOCKED    The framer knows where frames end. It checks the header and footer of each one. A
//             frame that fails drops the lock.
//   HUNTING   Every byte is tried as a frame end ("a sliding window"). A frame is accepted, and the
//             framer locks, when the same alignment has held for THREE frames in a row and no
//             OTHER alignment also held in that time.
//
// The last condition matters because a still transmitter repeats the same 25 bytes. If those bytes
// contain a 0x0F with a footer value just before it, a wrong alignment passes every check a right
// one does, forever, and nothing in the bytes says which is real. The framer then accepts neither
// (the robot sees no radio link, which is its safe state) and counts it in `ambiguous`. Any change
// to a control in those bytes ends the tie. Telling the two apart for certain needs the idle gap
// between frames, which the firmware cannot see today: it drains the UART in blocks every 20 ms.
//
// WHAT IT DOES NOT DO. It does not loosen anything: every frame it accepts has the header and one
// of the four footers, as before. Three limits remain, all shared with the framer it replaces:
//   * A damaged data byte inside a well-formed frame is not seen. SBUS has nothing to see it with.
//   * A frame that starts in the right place but has a byte inserted, or is cut short, is misread
//     ONCE when its 25th byte happens to be a footer value (after an inserted byte that is the
//     flags byte, which is usually 0x00). The lock drops at the next frame. Closing this needs the
//     gap, or holding every frame until the next header arrives, which delays every frame by one.
//   * While hunting, if the real frames are damaged in their header or footer for two frames
//     running AND a wrong alignment is self-consistent in those same frames, it locks onto the
//     wrong one and stays there while the controls do not move. The old framer needed no damage
//     at all to do that.
// What it costs: after any damage, two good frames go by before the third is accepted (14 ms at
// 143 frames a second). The old framer took the next one.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace sbus {

static const size_t   kFrameLen    = 25;
static const uint8_t  kHeader      = 0x0F;
// Bytes arriving with no frame accepted: 40 frames' worth. Crossing it is the 10-26 signature.
static const uint32_t kStarveBytes = 1000;

// The footer variants seen across receivers. Unchanged from the framer this replaces.
inline bool footerOk(uint8_t f) { return f == 0x00 || f == 0x04 || f == 0x14 || f == 0x24; }

struct Frame {
    uint16_t ch[16];
    bool     failsafe;
    bool     frame_lost;
};

// 25 bytes -> channels and flags. False when the header or footer is wrong.
inline bool decode(const uint8_t* f, Frame* out)
{
    if (f[0] != kHeader || !footerOk(f[24])) return false;
    uint16_t* ch = out->ch;
    ch[0]  = (uint16_t)((f[1]        | f[2]  << 8)                 & 0x07FF);
    ch[1]  = (uint16_t)((f[2]  >> 3  | f[3]  << 5)                 & 0x07FF);
    ch[2]  = (uint16_t)((f[3]  >> 6  | f[4]  << 2  | f[5]  << 10)  & 0x07FF);
    ch[3]  = (uint16_t)((f[5]  >> 1  | f[6]  << 7)                 & 0x07FF);
    ch[4]  = (uint16_t)((f[6]  >> 4  | f[7]  << 4)                 & 0x07FF);
    ch[5]  = (uint16_t)((f[7]  >> 7  | f[8]  << 1  | f[9]  << 9)   & 0x07FF);
    ch[6]  = (uint16_t)((f[9]  >> 2  | f[10] << 6)                 & 0x07FF);
    ch[7]  = (uint16_t)((f[10] >> 5  | f[11] << 3)                 & 0x07FF);
    ch[8]  = (uint16_t)((f[12]       | f[13] << 8)                 & 0x07FF);
    ch[9]  = (uint16_t)((f[13] >> 3  | f[14] << 5)                 & 0x07FF);
    ch[10] = (uint16_t)((f[14] >> 6  | f[15] << 2  | f[16] << 10)  & 0x07FF);
    ch[11] = (uint16_t)((f[16] >> 1  | f[17] << 7)                 & 0x07FF);
    ch[12] = (uint16_t)((f[17] >> 4  | f[18] << 4)                 & 0x07FF);
    ch[13] = (uint16_t)((f[18] >> 7  | f[19] << 1  | f[20] << 9)   & 0x07FF);
    ch[14] = (uint16_t)((f[20] >> 2  | f[21] << 6)                 & 0x07FF);
    ch[15] = (uint16_t)((f[21] >> 5  | f[22] << 3)                 & 0x07FF);
    out->frame_lost = (f[23] & (1 << 2)) != 0;
    out->failsafe   = (f[23] & (1 << 3)) != 0;
    return true;
}

// Channels and flags -> 25 bytes. Used by the self-test and the host tests; the robot never sends.
inline void encode(const Frame& in, uint8_t* f, uint8_t footer = 0x00)
{
    memset(f, 0, kFrameLen);
    f[0] = kHeader;
    unsigned bit = 0;
    for (int c = 0; c < 16; c++)
        for (int b = 0; b < 11; b++, bit++)
            if ((in.ch[c] >> b) & 1) f[1 + (bit >> 3)] |= (uint8_t)(1u << (bit & 7));
    f[23] = (uint8_t)((in.frame_lost ? 1 << 2 : 0) | (in.failsafe ? 1 << 3 : 0));
    f[24] = footer;
}

class Framer {
public:
    Framer() { reset(); }

    void reset()
    {
        memset(ring_, 0, sizeof(ring_));
        memset(last_ok_, 0, sizeof(last_ok_));
        memset(&frame_, 0, sizeof(frame_));
        w_ = 0; ph_ = 0; have_ = 0; n_ = 0;
        locked_ = false; to_end_ = 0; hunt_ = 0; since_valid_ = 0;
        valid = rejected = resyncs = hunt_bytes = max_hunt = ambiguous = starved = 0;
    }

    // One byte from the receiver. True when it completes an accepted frame: read it with frame().
    bool feed(uint8_t b)
    {
        ring_[w_] = b;
        if (++w_ == kRing) w_ = 0;
        if (++ph_ == kFrameLen) ph_ = 0;
        if (have_ < kRing) have_++;
        n_++;
        if (since_valid_ < 0xFFFFFFFFu && ++since_valid_ == kStarveBytes) starved++;

        // Does a well-formed frame end on this byte?
        const bool one = have_ >= kFrameLen && footerOk(back(0)) && back(24) == kHeader;

        if (locked_) {
            if (--to_end_ != 0) return false;        // inside a frame
            if (one) { to_end_ = kFrameLen; accept(); return true; }
            locked_ = false;                          // the frame that should end here does not
            resyncs++; rejected++;
            hunt_ = 0;
            memset(last_ok_, 0, sizeof(last_ok_));
        }

        hunt_bytes++;
        if (++hunt_ > max_hunt) max_hunt = hunt_;
        if (!one) return false;
        // ... and did one end 25 bytes ago too?
        if (!(have_ >= 2 * kFrameLen && footerOk(back(25)) && back(49) == kHeader)) { rejected++; return false; }

        const uint64_t before = last_ok_[ph_];
        last_ok_[ph_] = n_;
        for (size_t p = 0; p < kFrameLen; p++)
            if (p != ph_ && last_ok_[p] != 0 && n_ - last_ok_[p] < kFrameLen) {
                ambiguous++; rejected++;              // another alignment holds as well: accept neither
                return false;
            }
        if (before == 0 || n_ - before != kFrameLen) { rejected++; return false; }   // not three in a row yet

        locked_ = true;
        to_end_ = kFrameLen;
        accept();
        return true;
    }

    const Frame& frame() const { return frame_; }
    bool locked() const { return locked_; }

    uint32_t valid;        // frames accepted
    uint32_t rejected;     // well-formed-looking frames NOT accepted, and locked frames that failed
    uint32_t resyncs;      // times the lock was lost
    uint32_t hunt_bytes;   // bytes seen while not locked
    uint32_t max_hunt;     // longest single hunt, in bytes
    uint32_t ambiguous;    // frames refused because two alignments both held
    uint32_t starved;      // times kStarveBytes arrived with no frame accepted

private:
    static const size_t kRing = 2 * kFrameLen;

    uint8_t back(size_t j) const { return ring_[(w_ + kRing - 1 - j) % kRing]; }

    void accept()
    {
        uint8_t lin[kFrameLen];
        for (size_t k = 0; k < kFrameLen; k++) lin[k] = back(kFrameLen - 1 - k);
        decode(lin, &frame_);                         // header and footer were checked above
        valid++;
        since_valid_ = 0;
        hunt_ = 0;
    }

    uint8_t  ring_[kRing];        // the last 50 bytes
    uint64_t last_ok_[kFrameLen]; // per alignment: the byte count when it last held for two frames
    Frame    frame_;
    size_t   w_, ph_, have_;
    uint64_t n_;                  // bytes since reset (64-bit: 32 would wrap in two weeks of radio)
    bool     locked_;
    size_t   to_end_;
    uint32_t hunt_, since_valid_;
};

// The 10-26 stream as a known-answer test, run once at boot: CH7 = 1000 and CH8 = 1800 put a 0x0F
// in byte 10, and the stream is entered AT that byte. Then one byte is dropped mid-stream. True
// when the framer accepts only the frames sent, starts within three whole frames, and relocks.
inline bool selfTest()
{
    Frame sent;
    memset(&sent, 0, sizeof(sent));
    for (int c = 0; c < 16; c++) sent.ch[c] = 992;
    sent.ch[6] = 1000;
    sent.ch[7] = 1800;
    uint8_t raw[kFrameLen];
    encode(sent, raw);
    if (raw[10] != kHeader) return false;             // the vector must contain the false header

    Framer fr;
    bool wrong = false;
    int first = -1, got = 0;
    for (size_t i = 10; i < kFrameLen; i++) wrong |= fr.feed(raw[i]);
    for (int n = 0; n < 6; n++)
        for (size_t i = 0; i < kFrameLen; i++)
            if (fr.feed(raw[i])) {
                if (first < 0) first = n;
                got++;
                wrong |= memcmp(fr.frame().ch, sent.ch, sizeof(sent.ch)) != 0 || i != kFrameLen - 1;
            }
    if (wrong || first != 2 || got != 4 || fr.starved != 0) return false;

    int again = 0;
    for (int n = 0; n < 6; n++)
        for (size_t i = 0; i < kFrameLen; i++) {
            if (n == 0 && i == 7) continue;           // one byte lost
            if (fr.feed(raw[i])) {
                again++;
                wrong |= memcmp(fr.frame().ch, sent.ch, sizeof(sent.ch)) != 0;
            }
        }
    return !wrong && again == 3 && fr.resyncs == 1;
}

}   // namespace sbus
