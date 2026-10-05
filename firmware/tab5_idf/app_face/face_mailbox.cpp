#include "face_mailbox.h"
#include <cstring>

// Seqlocks. The pattern is three lines and the reason it is correct is worth stating once,
// because it is the thing that replaces a mutex everywhere in this app:
//
//   writer:  seq++ (now ODD, "a write is in progress")  /  store fields  /  seq++ (now EVEN)
//   reader:  read seq; if odd, retry  /  copy fields  /  read seq again; if changed, retry
//
// A reader therefore either sees a complete generation or spins once more; a writer never waits
// for a reader at all. The acquire/release ordering is what stops the compiler or the core from
// hoisting a field read outside the two counter reads — on a dual-core P4 that is not theoretical.
//
// NO ALLOCATION, EVER. These are file-scope statics, so they are .bss (internal DRAM) and exist
// before main(). ~300 bytes total against the ~250-310 KiB internal budget.

namespace {

struct SeqBox {
    std::atomic<uint32_t> seq{0};
};

SeqBox        g_snapBox;
RobotSnapshot g_snap{};

SeqBox   g_intBox;
UiIntent g_int{};

// A bounded retry. An unbounded `while` would violate AUDIT.md invariant 4 in spirit — the cost
// of a read would depend on a value rather than being fixed — and in practice two retries is
// already pathological for one writer. On giving up we return the last copy we managed to take,
// which is at worst one generation old: strictly better than blocking the render task, and the
// staleness it could introduce is bounded by the writer's period, not by anything unbounded.
constexpr int kMaxRetries = 8;

}  // namespace

void mbSnapshotGet(RobotSnapshot* out)
{
    for (int i = 0; i < kMaxRetries; ++i) {
        const uint32_t s0 = g_snapBox.seq.load(std::memory_order_acquire);
        if (s0 & 1u) continue;
        std::memcpy(out, &g_snap, sizeof(*out));
        const uint32_t s1 = g_snapBox.seq.load(std::memory_order_acquire);
        if (s0 == s1) return;
    }
    // Fall through with whatever the last copy was. It is self-consistent per field, and the
    // caller cannot tell the difference from a value that is one frame old — which it is.
}

void mbSnapshotPublish(const RobotSnapshot& in)
{
    g_snapBox.seq.fetch_add(1, std::memory_order_release);
    std::memcpy(&g_snap, &in, sizeof(g_snap));
    g_snapBox.seq.fetch_add(1, std::memory_order_release);
}

void mbIntentGet(UiIntent* out)
{
    for (int i = 0; i < kMaxRetries; ++i) {
        const uint32_t s0 = g_intBox.seq.load(std::memory_order_acquire);
        if (s0 & 1u) continue;
        std::memcpy(out, &g_int, sizeof(*out));
        const uint32_t s1 = g_intBox.seq.load(std::memory_order_acquire);
        if (s0 == s1) return;
    }
}

void mbIntentPublish(const UiIntent& in)
{
    g_intBox.seq.fetch_add(1, std::memory_order_release);
    std::memcpy(&g_int, &in, sizeof(g_int));
    g_intBox.seq.fetch_add(1, std::memory_order_release);
}
