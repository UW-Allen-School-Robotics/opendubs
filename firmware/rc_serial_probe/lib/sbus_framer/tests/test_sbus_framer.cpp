// Host tests of the SBUS framer (../src/sbus_framer.h). No board, no radio: run test_sbus_framer.sh.
//
// The one rule every case checks: the framer never hands over a frame that was not on the wire.
// After that: it finds the frames, and after damage it finds them again within three frames.
#include <stdio.h>
#include <stdlib.h>

#include <deque>
#include <vector>

#include "sbus_framer.h"

using sbus::Frame;
using sbus::Framer;
using sbus::kFrameLen;

static int g_fail = 0;
#define CHECK(cond, ...)                                             \
    do {                                                             \
        if (!(cond)) {                                               \
            g_fail++;                                                \
            printf("  FAIL %s:%d  ", __FILE__, __LINE__);            \
            printf(__VA_ARGS__);                                     \
            printf("\n");                                            \
        }                                                            \
    } while (0)

// The framer this replaces, copied from main.cpp at 3cdf41fe, to show the fault and nothing else.
struct OldFramer {
    uint8_t  buf[25];
    size_t   pos;
    uint32_t ok, bad;
    Frame    last;
    OldFramer() : pos(0), ok(0), bad(0) {}
    bool feed(uint8_t b)
    {
        if (pos == 0) {
            if (b != 0x0F) return false;
            buf[pos++] = b;
            return false;
        }
        buf[pos++] = b;
        if (pos < sizeof(buf)) return false;
        pos = 0;
        if (sbus::decode(buf, &last)) { ok++; return true; }
        bad++;
        return false;
    }
};

struct Rng {   // xorshift: the same stream on every machine
    uint64_t s;
    explicit Rng(uint64_t seed) : s(seed) {}
    uint32_t next()
    {
        s ^= s << 13; s ^= s >> 7; s ^= s << 17;
        return (uint32_t)(s >> 16);
    }
    uint32_t below(uint32_t n) { return next() % n; }
};

static Frame frameOf(uint16_t all)
{
    Frame f;
    memset(&f, 0, sizeof(f));
    for (int c = 0; c < 16; c++) f.ch[c] = all;
    return f;
}

static bool same(const Frame& a, const Frame& b)
{
    return memcmp(a.ch, b.ch, sizeof(a.ch)) == 0 && a.failsafe == b.failsafe && a.frame_lost == b.frame_lost;
}

// A still transmitter has a second, wrong alignment that passes every check when some byte is 0x0F
// and the byte before it is a footer value (the wrong frame's footer is that byte, a frame later).
static bool hasRival(const uint8_t* raw)
{
    for (size_t k = 2; k < kFrameLen - 1; k++)
        if (raw[k] == sbus::kHeader && sbus::footerOk(raw[k - 1])) return true;
    return false;
}

// ---- (a) the 10-26 stream ---------------------------------------------------------------------
static void test_1026()
{
    printf("-- (a) the 2026-10-26 stream: CH7=1000, CH8=1800, entered at byte 10\n");
    Frame sent = frameOf(992);
    sent.ch[6] = 1000;
    sent.ch[7] = 1800;
    uint8_t raw[25];
    sbus::encode(sent, raw);
    CHECK(raw[10] == 0x0F, "byte 10 is 0x%02X, expected the false header 0x0F", raw[10]);

    OldFramer old;
    Framer    fr;
    int       first = -1;
    for (size_t i = 10; i < 25; i++) { old.feed(raw[i]); fr.feed(raw[i]); }
    const int frames = 10000;   // 70 s of radio at 143 frames a second
    for (int n = 0; n < frames; n++)
        for (size_t i = 0; i < 25; i++) {
            old.feed(raw[i]);
            if (fr.feed(raw[i])) {
                if (first < 0) first = n;
                CHECK(same(fr.frame(), sent), "frame %d decoded wrongly", n);
            }
        }
    printf("   old framer: ok=%u bad=%u    new framer: ok=%u first=%d resync=%u starved=%u\n",
           old.ok, old.bad, fr.valid, first, fr.resyncs, fr.starved);
    CHECK(old.ok == 0 && old.bad >= (uint32_t)frames - 1, "the old framer no longer shows the fault: fix the copy");
    CHECK(first == 2, "first frame accepted was %d, expected the third whole frame (2)", first);
    CHECK(fr.valid == (uint32_t)frames - 2, "accepted %u of %d", fr.valid, frames);
    CHECK(fr.starved == 0 && fr.resyncs == 0 && fr.ambiguous == 0, "counters moved on a clean stream");
    CHECK(sbus::selfTest(), "the boot self-test fails");
}

// ---- (b) every entry point, a 0x0F in every byte ----------------------------------------------
static void test_every_offset()
{
    printf("-- (b) entered at every offset, with 0x0F forced into every payload byte\n");
    int cases = 0, rivals = 0;
    for (size_t at = 1; at <= 23; at++)          // which byte is forced to 0x0F (23 is the flags byte)
        for (int prev = 0; prev < 3; prev++)     // the byte before it: as it falls, 0x04 (a footer value), 0xA5
            for (size_t enter = 0; enter < 25; enter++) {
                uint8_t raw[25];
                sbus::encode(frameOf(992), raw);
                raw[at] = 0x0F;
                if (at >= 2 && prev == 1) raw[at - 1] = 0x04;
                if (at >= 2 && prev == 2) raw[at - 1] = 0xA5;
                Frame want;
                CHECK(sbus::decode(raw, &want), "the test frame itself is malformed");
                const bool rival = hasRival(raw);

                Framer fr;
                int    first = -1, whole = 0;
                bool   wrong = false;
                for (size_t i = enter; i < 25; i++) wrong |= fr.feed(raw[i]);
                if (enter == 0) whole = 1;   // entering at byte 0 makes that a whole frame
                for (int n = whole; n < 8; n++)
                    for (size_t i = 0; i < 25; i++)
                        if (fr.feed(raw[i])) {
                            if (first < 0) first = n;
                            wrong |= !same(fr.frame(), want) || i != 24;
                        }
                cases++;
                CHECK(!wrong, "at=%zu prev=%d enter=%zu: a frame that was not sent", at, prev, enter);
                if (rival) {
                    rivals++;
                    CHECK(fr.valid == 0 && fr.ambiguous > 0,
                          "at=%zu prev=%d enter=%zu: two alignments hold, and one was accepted (ok=%u)", at, prev, enter, fr.valid);
                } else {
                    CHECK(first == 2, "at=%zu prev=%d enter=%zu: first accepted frame %d, expected 2", at, prev, enter, first);
                }
            }
    printf("   %d streams, %d of them with two alignments that both hold (accepted: none)\n", cases, rivals);
}

// ---- two alignments: refused while hunting, kept while locked, resolved by a control moving ----
static void test_rival()
{
    printf("-- two alignments that both hold\n");
    uint8_t tie[25], clean[25];
    sbus::encode(frameOf(992), clean);
    memcpy(tie, clean, 25);
    tie[10] = 0x04;
    tie[11] = 0x0F;
    Frame want_tie, want_clean;
    sbus::decode(tie, &want_tie);
    sbus::decode(clean, &want_clean);
    CHECK(hasRival(tie) && !hasRival(clean), "the test frames are not what they claim");

    // hunting into the tie: nothing, however long; then a control moves and the third frame is taken
    Framer fr;
    for (int n = 0; n < 500; n++)
        for (size_t i = 0; i < 25; i++) CHECK(!fr.feed(tie[i]), "accepted a frame while two alignments held");
    CHECK(fr.ambiguous > 0 && fr.starved == 1, "ambiguous=%u starved=%u", fr.ambiguous, fr.starved);
    int first = -1;
    for (int n = 0; n < 6; n++)
        for (size_t i = 0; i < 25; i++)
            if (fr.feed(clean[i])) {
                if (first < 0) first = n;
                CHECK(same(fr.frame(), want_clean) && i == 24, "wrong frame after the tie ended");
            }
    CHECK(first >= 0 && first <= 3, "after the tie ended the first frame accepted was %d", first);

    // already locked when the tie appears: the lock is kept and the frames are the real ones
    Framer lk;
    for (int n = 0; n < 5; n++)
        for (size_t i = 0; i < 25; i++) lk.feed(clean[i]);
    CHECK(lk.locked(), "not locked on a clean stream");
    int ok = 0;
    for (int n = 0; n < 500; n++)
        for (size_t i = 0; i < 25; i++)
            if (lk.feed(tie[i])) {
                ok++;
                CHECK(same(lk.frame(), want_tie) && i == 24, "locked framer handed over a wrong frame");
            }
    CHECK(ok == 500 && lk.resyncs == 0, "locked framer accepted %d of 500 through a tie (resync=%u)", ok, lk.resyncs);
}

// ---- a stream with damage, checked against what was really on the wire -------------------------
//
// What can be promised, and what cannot. SBUS has no checksum: a frame is taken on its header and
// footer alone, as before. So a frame that starts in the right place but has a byte inserted, or
// is cut short, is still MISREAD once when its 25th byte happens to be a footer value (after an
// inserted byte that is the flags byte, which is usually 0x00). Those are counted and compared
// with the old framer on the same bytes. What must never happen is a MISALIGNED frame: 25 bytes
// that do not begin where a frame begins. That is the unsafe twin of the 10-26 fault.
struct Wire {
    std::vector<uint8_t> bytes;
    std::vector<int>     ends;      // per byte: index of the whole frame that ends here, or -1
    std::vector<bool>    starts;    // per byte: a frame (whole or damaged) begins here
    std::vector<Frame>   frames;    // what each whole, well-formed frame decodes to
    void put(const uint8_t* raw, size_t n, bool whole, bool is_frame = true)
    {
        for (size_t i = 0; i < n; i++) {
            bytes.push_back(raw[i]);
            ends.push_back(-1);
            starts.push_back(i == 0 && is_frame);
        }
        Frame f;
        if (whole && n == 25 && sbus::decode(raw, &f)) {
            frames.push_back(f);
            ends.back() = (int)frames.size() - 1;
        }
    }
    bool real(size_t i, const Frame& f) const { return ends[i] >= 0 && same(f, frames[(size_t)ends[i]]); }
    bool aligned(size_t i) const { return i >= 24 && starts[i - 24]; }
};

struct Tally {
    uint32_t real;             // accepted, and it was the whole frame ending on that byte
    uint32_t misread;          // accepted a damaged frame that began in the right place (the old framer does too)
    uint32_t misaligned;       // accepted 25 bytes that do not begin where a frame begins: must be 0
    uint32_t dropped_clean;    // a good frame not accepted with no damage before it: must be 0
    int      worst_missed;     // most whole frames gone by between damage and the next real frame
    int      worst_run;        // most misreads in a row
};

static Tally run(const Wire& w, Framer& fr, const std::vector<size_t>& damage_at)
{
    Tally t;
    memset(&t, 0, sizeof(t));
    size_t next = 0;
    int    missed = 0, in_a_row = 0;
    bool   hurt = true;    // the start of the stream counts as damage
    for (size_t i = 0; i < w.bytes.size(); i++) {
        while (next < damage_at.size() && damage_at[next] <= i) { hurt = true; missed = 0; next++; }
        if (fr.feed(w.bytes[i])) {
            if (w.real(i, fr.frame())) { t.real++; hurt = false; in_a_row = 0; }
            else if (w.aligned(i))     { t.misread++; if (++in_a_row > t.worst_run) t.worst_run = in_a_row; }
            else {
                t.misaligned++;
                if (t.misaligned <= 5) {
                    printf("   MISALIGNED frame accepted at byte %zu, %zu bytes after the last damage. Bytes:", i,
                           next ? i - damage_at[next - 1] : i);
                    for (size_t k = i >= 79 ? i - 79 : 0; k <= i; k++) printf("%s%02X", (i - k) % 25 == 24 ? " | " : " ", w.bytes[k]);
                    printf("\n");
                }
            }
        } else if (w.ends[i] >= 0) {
            if (hurt) { if (++missed > t.worst_missed) t.worst_missed = missed; }
            else      { t.dropped_clean++; }
        }
    }
    return t;
}

// The old framer on the same bytes.
static Tally runOld(const Wire& w)
{
    Tally     t;
    OldFramer old;
    memset(&t, 0, sizeof(t));
    for (size_t i = 0; i < w.bytes.size(); i++)
        if (old.feed(w.bytes[i])) {
            if (w.real(i, old.last))  t.real++;
            else if (w.aligned(i))    t.misread++;
            else                      t.misaligned++;
        }
    return t;
}

static void randomFrame(Rng& r, uint8_t* raw, bool still, Frame* keep)
{
    if (!still || keep->ch[0] == 0) {
        for (int c = 0; c < 16; c++) keep->ch[c] = (uint16_t)(172 + r.below(1640));
    } else if (r.below(20) == 0) {
        keep->ch[r.below(16)] = (uint16_t)(172 + r.below(1640));   // one control moves now and then
    }
    keep->failsafe   = r.below(50) == 0;
    keep->frame_lost = r.below(50) == 0;
    static const uint8_t footers[4] = {0x00, 0x04, 0x14, 0x24};
    sbus::encode(*keep, raw, still ? 0x00 : footers[r.below(4)]);
}

// One damaged frame onto the wire. kind: 0 byte dropped, 1 byte inserted, 2 header bit flipped,
// 3 footer bit flipped, 4 data bit flipped (still well formed), 5 cut short, 6 a burst of noise.
static void damaged(Rng& r, Wire& w, uint8_t* raw, int kind)
{
    const size_t at = 1 + r.below(23);
    if (kind == 0) { uint8_t cut[24]; memcpy(cut, raw, at); memcpy(cut + at, raw + at + 1, 24 - at); w.put(cut, 24, false); }
    if (kind == 1) { uint8_t add[26]; memcpy(add, raw, at); add[at] = (uint8_t)r.next(); memcpy(add + at + 1, raw + at, 25 - at); w.put(add, 26, false); }
    if (kind == 2) { raw[0] ^= (uint8_t)(1u << r.below(8)); w.put(raw, 25, true); }
    if (kind == 3) { raw[24] ^= 0x80; w.put(raw, 25, true); }
    if (kind == 4) { raw[at] ^= (uint8_t)(1u << r.below(8)); w.put(raw, 25, true); }
    if (kind == 5) { w.put(raw, 1 + r.below(24), false); }
    if (kind == 6) { uint8_t junk[60]; const size_t len = 1 + r.below(60); for (size_t i = 0; i < len; i++) junk[i] = (uint8_t)r.next(); w.put(junk, len, false, false); }
}

// ---- (c) one piece of damage at a time --------------------------------------------------------
static void test_damage()
{
    printf("-- (c) one piece of damage in a moving stream, 400 streams each\n");
    printf("   %-20s %8s %9s %12s   %12s %15s\n", "", "missed", "misread", "misaligned", "old: misread", "old: misaligned");
    const char* kinds[] = {"byte dropped", "byte inserted", "header bit flipped", "footer bit flipped",
                           "data bit flipped", "frame cut short", "burst of noise"};
    for (int kind = 0; kind < 7; kind++) {
        Tally sum, old;
        memset(&sum, 0, sizeof(sum));
        memset(&old, 0, sizeof(old));
        for (uint32_t seed = 1; seed <= 400; seed++) {
            Rng   r(seed * 7919u + (uint32_t)kind);
            Wire  w;
            Frame keep = frameOf(0);
            std::vector<size_t> damage;
            const int hit = 10 + (int)r.below(10);
            for (int n = 0; n < 40; n++) {
                uint8_t raw[25];
                randomFrame(r, raw, false, &keep);
                if (n != hit) { w.put(raw, 25, true); continue; }
                damage.push_back(w.bytes.size());
                damaged(r, w, raw, kind);
            }
            Framer      fr;
            const Tally t = run(w, fr, damage);
            const Tally o = runOld(w);
            old.misread += o.misread;
            old.misaligned += o.misaligned;
            sum.misread += t.misread;
            sum.misaligned += t.misaligned;
            sum.dropped_clean += t.dropped_clean;
            if (t.worst_missed > sum.worst_missed) sum.worst_missed = t.worst_missed;
            if (t.worst_run > sum.worst_run) sum.worst_run = t.worst_run;
        }
        printf("   %-20s %8d %9u %12u   %12u %15u\n", kinds[kind], sum.worst_missed, sum.misread, sum.misaligned,
               old.misread, old.misaligned);
        CHECK(sum.misaligned == 0, "%s: %u misaligned frames accepted", kinds[kind], sum.misaligned);
        CHECK(sum.dropped_clean == 0, "%s: %u good frames dropped with no damage before them", kinds[kind], sum.dropped_clean);
        // Two whole frames go by while the alignment is seen to hold three times; never more.
        CHECK(sum.worst_missed <= 2, "%s: %d whole frames missed", kinds[kind], sum.worst_missed);
        CHECK(sum.worst_run <= 1, "%s: %d misreads in a row", kinds[kind], sum.worst_run);
        CHECK(sum.misread <= old.misread, "%s: more misreads (%u) than the old framer (%u)", kinds[kind], sum.misread, old.misread);
    }
}

// ---- (d) long runs with random damage ---------------------------------------------------------
static void test_fuzz(bool still, uint32_t frames)
{
    printf("-- (d) %u frames, %s, damage about every 150 frames\n", frames,
           still ? "a transmitter that mostly sits still" : "every channel random every frame");
    Rng   r(still ? 0xC0FFEEu : 0xBADF00Du);
    Wire  w;
    Frame keep = frameOf(0);
    std::vector<size_t> damage;
    for (uint32_t n = 0; n < frames; n++) {
        uint8_t raw[25];
        randomFrame(r, raw, still, &keep);
        if (r.below(150) != 0) { w.put(raw, 25, true); continue; }
        damage.push_back(w.bytes.size());
        damaged(r, w, raw, (int)r.below(7));
    }
    const uint32_t whole = (uint32_t)w.frames.size();
    Framer      fr;
    const Tally t = run(w, fr, damage);
    const Tally o = runOld(w);
    printf("   new: accepted %u of %u (%.2f%%), misread %u, misaligned %u\n",
           t.real, whole, 100.0 * t.real / whole, t.misread, t.misaligned);
    printf("   old: accepted %u of %u (%.2f%%), misread %u, misaligned %u\n",
           o.real, whole, 100.0 * o.real / whole, o.misread, o.misaligned);
    printf("   new: %zu damaged frames, resync=%u ambiguous=%u maxhunt=%u starved=%u, worst frames missed after damage %d\n",
           damage.size(), fr.resyncs, fr.ambiguous, fr.max_hunt, fr.starved, t.worst_missed);
    CHECK(t.misaligned == 0, "%u misaligned frames accepted", t.misaligned);
    CHECK(t.dropped_clean == 0, "%u good frames dropped with no damage before them", t.dropped_clean);
    // At most one misread per damaged frame. (Not "no more than the old framer": the old one is
    // sometimes out of step when a damaged frame goes by, and so misses the chance to misread it.)
    CHECK(t.misread <= damage.size(), "%u misreads from %zu damaged frames", t.misread, damage.size());
    CHECK(t.real > whole * 0.95, "only %u of %u accepted", t.real, whole);
}

int main()
{
    test_1026();
    test_every_offset();
    test_rival();
    test_damage();
    test_fuzz(false, 400000);
    test_fuzz(true, 400000);
    if (g_fail) { printf("FAILED: %d check(s)\n", g_fail); return 1; }
    printf("OK: no misaligned frame is accepted, and the frames are found again within three after damage\n");
    return 0;
}
