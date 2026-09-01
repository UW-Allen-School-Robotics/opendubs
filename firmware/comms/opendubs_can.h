#pragma once
// OpenDubs payload communication protocol — shared, framework-free core.
// Spec: docs/interfaces/payload-comms.md
//
// Pure encode/decode + ID/timing constants. No Arduino, no networking — compiles and
// tests on the host (test/test_opendubs_can.cpp) as well as on both the StampPLC
// (espressif32) and Tab5 (ESP32-P4) firmware targets.
#include <cstdint>
#include <cstring>

namespace opendubs_can {

// ---- CAN ID layout: [10:7] module ID, [6:0] message type (11-bit std ID) ----
constexpr uint16_t kModuleBroadcast = 0;
constexpr uint16_t kModuleLift      = 1;

constexpr uint16_t kTypeState = 0x00;
constexpr uint16_t kTypeCmd   = 0x01;
constexpr uint16_t kTypeFault = 0x02;

constexpr uint16_t makeId(uint16_t moduleId, uint16_t msgType) {
  return static_cast<uint16_t>((moduleId << 7) | (msgType & 0x7F));
}

constexpr uint16_t kIdAuthorize = makeId(kModuleBroadcast, 0x00);   // 0x000
constexpr uint16_t kIdLiftState = makeId(kModuleLift, kTypeState);  // 0x080
constexpr uint16_t kIdLiftCmd   = makeId(kModuleLift, kTypeCmd);    // 0x081
constexpr uint16_t kIdLiftFault = makeId(kModuleLift, kTypeFault);  // 0x082

// ---- Timing (docs/interfaces/payload-comms.md) ----
constexpr uint32_t kAuthorizePeriodMs  = 50;                        // 20 Hz
constexpr uint32_t kAuthorizeTimeoutMs = 250;                       // safety-derived, fixed
constexpr uint32_t kLiftStatePeriodMs  = 100;                       // 10 Hz
constexpr uint32_t kLiftStateTimeoutMs = kLiftStatePeriodMs * 3;    // 300ms, rate-derived

// ---- AUTHORIZE — 0x000, base -> broadcast, DLC 1 ----
struct Authorize {
  uint8_t counter;
  // Explicit constructor, not a default member initializer + aggregate-init: a default member
  // initializer disqualifies a struct from being an aggregate under C++11, which breaks
  // `Authorize a{authorize_counter++};`-style brace-init on the StampPLC's C++11 build even
  // though it compiles fine on the C++14+ native test / Tab5 targets. An explicit constructor
  // works identically at every C++ standard this header is built under.
  explicit Authorize(uint8_t counter_ = 0) : counter(counter_) {}
};
inline uint8_t encodeAuthorize(const Authorize& a, uint8_t out[8]) {
  out[0] = a.counter;
  return 1;
}
inline Authorize decodeAuthorize(const uint8_t* data, uint8_t dlc) {
  Authorize a{};
  if (dlc >= 1) a.counter = data[0];
  return a;
}

// ---- LIFT_STATE — 0x080, module -> base, DLC 4 ----
enum class LiftMotion : uint8_t { STOP = 0, UP = 1, DOWN = 2 };

struct LiftState {
  uint16_t   heightMm = 0;
  bool       inhibit = false;
  bool       atTop = false;
  bool       atBot = false;
  bool       tofOk = false;
  bool       relayUpPulse = false;
  bool       relayDnPulse = false;
  LiftMotion motion = LiftMotion::STOP;
  uint8_t    seq = 0;
};

inline uint8_t encodeLiftState(const LiftState& s, uint8_t out[8]) {
  out[0] = static_cast<uint8_t>(s.heightMm >> 8);
  out[1] = static_cast<uint8_t>(s.heightMm & 0xFF);
  uint8_t flags = 0;
  flags |= s.inhibit      ? 0x01 : 0;
  flags |= s.atTop        ? 0x02 : 0;
  flags |= s.atBot        ? 0x04 : 0;
  flags |= s.tofOk        ? 0x08 : 0;
  flags |= s.relayUpPulse ? 0x10 : 0;
  flags |= s.relayDnPulse ? 0x20 : 0;
  flags |= static_cast<uint8_t>((static_cast<uint8_t>(s.motion) & 0x03) << 6);
  out[2] = flags;
  out[3] = s.seq;
  return 4;
}

inline LiftState decodeLiftState(const uint8_t* data, uint8_t dlc) {
  LiftState s{};
  if (dlc < 4) return s;
  s.heightMm = static_cast<uint16_t>((data[0] << 8) | data[1]);
  const uint8_t flags = data[2];
  s.inhibit      = flags & 0x01;
  s.atTop        = flags & 0x02;
  s.atBot        = flags & 0x04;
  s.tofOk        = flags & 0x08;
  s.relayUpPulse = flags & 0x10;
  s.relayDnPulse = flags & 0x20;
  s.motion       = static_cast<LiftMotion>((flags >> 6) & 0x03);
  s.seq          = data[3];
  return s;
}

// ---- LIFT_CMD — 0x081, base -> module, DLC 2 (edge-triggered, not a target position) ----
enum class LiftCmd : uint8_t { STOP = 0, UP = 1, DOWN = 2 };

struct LiftCommand {
  LiftCmd cmd;
  uint8_t seq;
  // See Authorize's comment above — explicit constructor so aggregate-init status (and thus
  // this struct's C++11 compatibility) doesn't depend on default member initializers.
  explicit LiftCommand(LiftCmd cmd_ = LiftCmd::STOP, uint8_t seq_ = 0) : cmd(cmd_), seq(seq_) {}
};

inline uint8_t encodeLiftCmd(const LiftCommand& c, uint8_t out[8]) {
  out[0] = static_cast<uint8_t>(c.cmd);
  out[1] = c.seq;
  return 2;
}

inline LiftCommand decodeLiftCmd(const uint8_t* data, uint8_t dlc) {
  LiftCommand c{};
  if (dlc < 2) return c;
  c.cmd = static_cast<LiftCmd>(data[0]);
  c.seq = data[1];
  return c;
}

// ---- FAULT (common format, shared by every module type) — DLC 2 ----
enum class FaultCode : uint8_t {
  NONE = 0, TOF_FAULT = 1, STUCK_TIMEOUT = 2, LIMIT_CONFLICT = 3, RELAY_FAULT = 4,
};

struct Fault {
  FaultCode code;
  uint8_t   detail;
  // See Authorize's comment above. Callers must write `Fault{...}` (direct-list-init), not
  // `return {...}` (copy-init) — explicit constructors are excluded from copy-init. This is a
  // call-site requirement, not a C++-standard one: see firmware/tab5_face/src/main.cpp's
  // deriveFault(), which names the type explicitly for exactly this reason.
  explicit Fault(FaultCode code_ = FaultCode::NONE, uint8_t detail_ = 0)
      : code(code_), detail(detail_) {}
};

inline uint8_t encodeFault(const Fault& f, uint8_t out[8]) {
  out[0] = static_cast<uint8_t>(f.code);
  out[1] = f.detail;
  return 2;
}

inline Fault decodeFault(const uint8_t* data, uint8_t dlc) {
  Fault f{};
  if (dlc < 2) return f;
  f.code   = static_cast<FaultCode>(data[0]);
  f.detail = data[1];
  return f;
}

// ---- staleness helper (fail-safe-by-omission) ----
inline bool isStale(uint32_t nowMs, uint32_t lastSeenMs, uint32_t timeoutMs) {
  if (lastSeenMs == 0) return true;          // never seen == stale
  return (nowMs - lastSeenMs) > timeoutMs;   // unsigned subtraction wraps correctly across millis() rollover
}

}  // namespace opendubs_can
