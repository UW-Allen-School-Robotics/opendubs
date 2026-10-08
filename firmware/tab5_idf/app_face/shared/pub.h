// A SEQLOCK FOR ONE WRITER AND MANY READERS, so published state never needs a mutex.
//
// notes/design/tab5-face-architecture.md §2.4: every cross-task "state" is a POD struct with a
// single writer, published through a seqlock. No reader ever blocks a writer; no writer ever
// blocks the UI. That property is the point: the one thing the face must never do is wait on
// best-effort perception work, and a mutex held across a 35 ms inference would do exactly that.
//
// The protocol: the writer bumps the counter to an ODD value, writes the payload, bumps it to EVEN.
// A reader samples the counter, copies, and re-samples; if the counter was odd or changed, the copy
// may be torn and it retries. Bounded retries, because an unbounded retry loop in the UI task is a
// hang (AUDIT.md invariant 4) -- a reader that loses eight races in a row keeps the previous value,
// which for a gaze target is both correct and invisible.
#pragma once
#include <atomic>
#include <type_traits>

template <typename T>
class Published {
    static_assert(std::is_trivially_copyable<T>::value,
                  "Published<T> copies T byte-wise under a seqlock: it must be a POD. A type that "
                  "allocates or owns a pointer would be shared, not published.");

public:
    void publish(const T& next)
    {
        _seq.fetch_add(1, std::memory_order_relaxed);
        std::atomic_thread_fence(std::memory_order_release);
        _v = next;
        std::atomic_thread_fence(std::memory_order_release);
        _seq.fetch_add(1, std::memory_order_relaxed);
    }

    // Returns false only if the value has never been published, or if eight consecutive reads were
    // torn. Callers treat false as "no new information", never as "no target".
    bool read(T* out) const
    {
        for (int attempt = 0; attempt < 8; ++attempt) {
            const uint32_t before = _seq.load(std::memory_order_relaxed);
            if (before == 0) return false;      // never published
            if (before & 1u) continue;          // write in progress
            std::atomic_thread_fence(std::memory_order_acquire);
            T copy = _v;
            std::atomic_thread_fence(std::memory_order_acquire);
            if (_seq.load(std::memory_order_relaxed) == before) {
                *out = copy;
                return true;
            }
        }
        return false;
    }

private:
    mutable std::atomic<uint32_t> _seq{0};
    T _v{};
};
