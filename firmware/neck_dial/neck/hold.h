// HOLD: THE NECK'S ONE DELIBERATE JOB FOR A PERSON (notes/design/head-ownership.md, the user's design
// of 2026-10-22). A held head stays where it is: tracking, shows and touch do not move it, and
// neither does the RC stick. It is a PAUSE, never a motion command, with one exception: software
// may hold at given angles (`HOLD <pan> <tilt>`, for a task that aims the head-top camera), and the
// head then slews there at its ordinary rate and stays.
//
//   * Set by the KNOB (turned one way, deliberately: several detents within about a second), or by
//     the network (`HOLD`, `HOLD HERE`, `HOLD <pan> <tilt>`).
//   * Released by ANYTHING: the knob turned the other way, the dashboard, software (`HOLD OFF`).
//   * RELEASE IS ANNOUNCED: the head stays held for kReleaseAnnounceMs after the release, while the
//     screen says "releasing hold" and the dashboard shows a notice, and only then may it move.
//   * NEVER KEPT: the hold lives in RAM, so a reboot or a power cycle always comes back released.
//   * NO TIMEOUT. A hold that expires by itself is the latch problem head-ownership.md §3 records.
//
// The knob is turned rather than pressed because the button is hard to press, above all while the
// neck moves, and because the press already has a job: confirming a tilt-limit change.
//
// PURE COMPUTE, like pantilt_core: no GPIO, no clock, no PCNT. The caller reads the encoder and
// passes the counts, so the gesture can be checked on a laptop (tools/pantilt_host_test.cpp).

#pragma once
#include <cstdint>

namespace neck {

// THE KNOB'S GESTURE. The PCNT counts every edge of both encoder phases, four counts to a detent.
// A turn of kKnobDetents detents in ONE direction within kKnobWindowMs is the gesture; slower, or
// fewer, is nothing, so a bump or a brush against the neck does not hold or release it. A turn back
// the other way starts the count again.
constexpr int      kKnobCountsPerDetent = 4;
constexpr int      kKnobDetents = 3;
constexpr uint32_t kKnobWindowMs = 1000;
// Which way holds. NOT YET CHECKED ON THE DIAL: the encoder's phase order (41/40) decides whether
// clockwise counts up. If the screen says RELEASE for the clockwise turn, flip this.
constexpr bool kKnobUpHolds = true;

enum class Knob : uint8_t { None, Hold, Release };

struct KnobGesture {
    int      acc = 0;          // counts in the current direction, signed
    uint32_t startMs = 0;      // when the current run of turning began
};

// Feed the counts read since the last call (any sign, any size, 0 is fine). Returns Hold or
// Release on the call that completes a gesture, None otherwise.
Knob knobFeed(KnobGesture& g, int counts, uint32_t nowMs);

// THE HOLD ITSELF.
constexpr uint32_t kReleaseAnnounceMs = 3000;

enum class HoldPhase : uint8_t { Off, On, Releasing };
enum class HoldBy : uint8_t { None, Knob, Remote };

struct Hold {
    HoldPhase phase = HoldPhase::Off;
    HoldBy    by = HoldBy::None;
    float     pan = 90, tilt = 90;     // where the head is held (commanded degrees)
    uint32_t  releaseAt = 0;           // while Releasing: when the head is free again
};

// Hold at these angles. Also cancels a release still being announced: holding again means held.
void holdAt(Hold& h, float pan, float tilt, HoldBy by);
// Start the release: the head stays held, announced, until nowMs + kReleaseAnnounceMs. Returns
// false when there was nothing to release (or a release is already under way).
bool holdRelease(Hold& h, uint32_t nowMs);
// Call every loop. Ends a release whose announcement is over; returns true on that call.
bool holdTick(Hold& h, uint32_t nowMs);
inline bool holding(const Hold& h) { return h.phase != HoldPhase::Off; }

const char* holdPhaseName(const Hold& h);   // "off" | "on" | "releasing"
const char* holdByName(const Hold& h);      // "knob" | "remote" | "-"

}  // namespace neck
