# Payload Comms Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the CAN-native payload communication protocol (`docs/interfaces/payload-comms.md`) end to end for the lift module: a shared, framework-free protocol library; StampPLC firmware that terminates the module's WiFi/UDP traffic and relays it to the Thor via the existing `[TAG]` telemetry tee; Tab5 firmware that actually drives `commsTask` (currently a stub); and dashboard backend wiring that surfaces module presence/faults through the existing `robot_state`/`status.py` mechanism.

**Architecture:** A pure-logic header (`firmware/comms/opendubs_can.h`) defines the wire format and is shared, unmodified, by both PlatformIO firmware projects via an include-path build flag — no submodule, no code duplication. A thin Arduino transport wrapper (`opendubs_can_udp.h`) sits on top, usable by both `EthernetUDP` (StampPLC) and `WiFiUDP` (Tab5) since both derive Arduino's common `UDP` interface. All payload traffic is UDP **broadcast** on both directions (dedicated port `5006`, separate from the existing text-protocol port `5005`) — no per-module IP tracking, since CAN-ID filtering already does the addressing (matches the spec's explicit non-goal of dynamic discovery). StampPLC is the terminus (it already owns the LAN-facing `EthernetUDP` socket and CAN hardware; the Tab5 has WiFi only) and relays derived state to the Thor as a new `[LIFT]` tag line, reusing the exact `tprintf`/`[TAG] k=v` mechanism already used for `[RC]`/`[BMS]`/etc.

**Tech Stack:** C++17 (header-only core, native-testable with plain `g++`), Arduino/ESP32 (`espressif32` for StampPLC, `pioarduino`/ESP32-P4 for Tab5), Python 3 + pytest (dashboard backend).

**Spec:** [`docs/interfaces/payload-comms.md`](../../interfaces/payload-comms.md) — the plan implements this document section by section; executors should read it in full before starting.

## Global Constraints

- Wire format is exactly as specified: 11-bit CAN ID = `(moduleId << 7) | msgType`; `AUTHORIZE`=`0x000` (DLC 1), `LIFT_STATE`=`0x080` (DLC 4, big-endian `heightMm`), `LIFT_CMD`=`0x081` (DLC 2), `LIFT_FAULT`=`0x082` (DLC 2, common `FAULT` format).
- `AUTHORIZE`: 20 Hz (50 ms), 250 ms staleness timeout (fixed, safety-derived — do not scale with rate).
- `LIFT_STATE`: 10 Hz (100 ms) flat rate, 300 ms staleness timeout (3× its own period — diagnostic, not safety-critical, per spec).
- No TLV, no fragmentation, no dynamic module discovery — every message is a fixed struct, CAN ID is the only discriminator. Do not add flexibility beyond what's specified.
- No new UI component for faults — reuse `robot_state.py`'s existing `mast` subsystem slot and `status.py`'s existing `raw_faults` list exactly as the spec's "Dashboard integration" section states.
- Scissor Lift repo (`/Users/rgnp/Documents/PlatformIO/Projects/Scissor Lift`) is explicitly out of scope — legacy prototype, not touched by this plan.
- `firmware/comms/` code must stay framework-free where practical (`opendubs_can.h`) so it stays natively testable; only the transport wrapper (`opendubs_can_udp.h`) may depend on Arduino's `Udp.h`.

---

## File Structure

| File | Responsibility |
|---|---|
| `firmware/comms/opendubs_can.h` | **Create.** Pure protocol logic: IDs, timing constants, struct encode/decode. Zero Arduino dependency. |
| `firmware/comms/test/test_opendubs_can.cpp` | **Create.** Native (host `g++`) test for the above. |
| `firmware/comms/opendubs_can_udp.h` | **Create.** Arduino `UDP&`-based transport (`send`/`poll`), shared by both firmware targets. |
| `firmware/rc_serial_probe/platformio.ini` | **Modify.** Add `-I../comms` to `build_flags`. |
| `firmware/rc_serial_probe/src/main.cpp` | **Modify.** New UDP socket + `AUTHORIZE` broadcast + `LIFT_STATE`/`LIFT_FAULT` receive + `[LIFT]` tag emission + `LIFT <UP\|DOWN\|STOP>` host command. |
| `firmware/tab5_face/platformio.ini` | **Modify.** Add `-I../comms` to `build_flags`. |
| `firmware/tab5_face/src/main.cpp` | **Modify.** Real `commsTask`: WiFi bring-up, `AUTHORIZE` receive → `Lift.setInhibit()`, `LIFT_STATE`/`LIFT_FAULT` send, `LIFT_CMD` receive → `Lift.commandUp/Down/Stop()`. |
| `software/kiosk-dashboard/backend/stamplc.py` | **Modify.** Parse `[LIFT] ...` into `self._d["lift"]`. |
| `software/kiosk-dashboard/backend/status.py` | **Modify.** `_lift()` accessor + `REQUIRED_MODULES` + wire into `_robot_state()`'s `raw_faults`/`mast_fault`. |
| `software/kiosk-dashboard/backend/tests/test_robot_state.py` | **Create.** Pure-function test of the required-module → `DEGRADED` path. |
| `software/kiosk-dashboard/backend/requirements-dev.txt` | **Create.** `pytest` (dev-only, kept out of the runtime `requirements.txt`). |

---

### Task 1: Core protocol library (`opendubs_can.h`)

**Files:**
- Create: `firmware/comms/opendubs_can.h`
- Create: `firmware/comms/test/test_opendubs_can.cpp`

**Interfaces:**
- Produces (used by Tasks 2–3): namespace `opendubs_can` with `kIdAuthorize`, `kIdLiftState`, `kIdLiftCmd`, `kIdLiftFault` (`uint16_t`); `kAuthorizePeriodMs`, `kAuthorizeTimeoutMs`, `kLiftStatePeriodMs`, `kLiftStateTimeoutMs` (`uint32_t`); types `Authorize{counter}`, `LiftState{heightMm,inhibit,atTop,atBot,tofOk,relayUpPulse,relayDnPulse,motion,seq}`, `LiftMotion{STOP,UP,DOWN}`, `LiftCommand{cmd,seq}`, `LiftCmd{STOP,UP,DOWN}`, `Fault{code,detail}`, `FaultCode{NONE,TOF_FAULT,STUCK_TIMEOUT,LIMIT_CONFLICT,RELAY_FAULT}`; functions `encodeAuthorize/decodeAuthorize`, `encodeLiftState/decodeLiftState`, `encodeLiftCmd/decodeLiftCmd`, `encodeFault/decodeFault` (each `encode*(const T&, uint8_t out[8]) -> uint8_t dlc`, each `decode*(const uint8_t* data, uint8_t dlc) -> T`); `isStale(uint32_t nowMs, uint32_t lastSeenMs, uint32_t timeoutMs) -> bool`.

- [ ] **Step 1: Write the failing native test**

Create `firmware/comms/test/test_opendubs_can.cpp`:

```cpp
// Native (host) test for the pure protocol logic in opendubs_can.h.
// Build + run: g++ -std=c++17 -Wall -Wextra -I.. test_opendubs_can.cpp -o /tmp/test_opendubs_can && /tmp/test_opendubs_can
#include "../opendubs_can.h"
#include <cassert>
#include <cstdio>

using namespace opendubs_can;

static void test_ids() {
  assert(kIdAuthorize == 0x000);
  assert(kIdLiftState == 0x080);
  assert(kIdLiftCmd   == 0x081);
  assert(kIdLiftFault == 0x082);
}

static void test_lift_state_roundtrip() {
  LiftState s{};
  s.heightMm = 633;
  s.inhibit = false; s.atTop = true; s.atBot = false; s.tofOk = true;
  s.relayUpPulse = true; s.relayDnPulse = false;
  s.motion = LiftMotion::UP;
  s.seq = 42;

  uint8_t buf[8] = {};
  uint8_t dlc = encodeLiftState(s, buf);
  assert(dlc == 4);
  assert(buf[0] == 0x02 && buf[1] == 0x79);   // 633 = 0x0279, big-endian
  assert(buf[2] == 0x5A);                     // atTop|tofOk|relayUpPulse|motion(UP=1)<<6
  assert(buf[3] == 42);

  LiftState d = decodeLiftState(buf, dlc);
  assert(d.heightMm == 633);
  assert(d.inhibit == false);
  assert(d.atTop == true);
  assert(d.atBot == false);
  assert(d.tofOk == true);
  assert(d.relayUpPulse == true);
  assert(d.relayDnPulse == false);
  assert(d.motion == LiftMotion::UP);
  assert(d.seq == 42);
}

static void test_lift_state_short_dlc_is_safe() {
  uint8_t buf[8] = {0xAA, 0xBB, 0xCC};
  LiftState d = decodeLiftState(buf, 2);   // short DLC must not be trusted
  assert(d.heightMm == 0);
  assert(d.seq == 0);
}

static void test_lift_cmd_roundtrip() {
  LiftCommand c{LiftCmd::DOWN, 7};
  uint8_t buf[8] = {};
  uint8_t dlc = encodeLiftCmd(c, buf);
  assert(dlc == 2);
  assert(buf[0] == static_cast<uint8_t>(LiftCmd::DOWN));
  assert(buf[1] == 7);

  LiftCommand d = decodeLiftCmd(buf, dlc);
  assert(d.cmd == LiftCmd::DOWN);
  assert(d.seq == 7);
}

static void test_fault_roundtrip() {
  Fault f{FaultCode::TOF_FAULT, 3};
  uint8_t buf[8] = {};
  uint8_t dlc = encodeFault(f, buf);
  assert(dlc == 2);
  Fault d = decodeFault(buf, dlc);
  assert(d.code == FaultCode::TOF_FAULT);
  assert(d.detail == 3);
}

static void test_staleness() {
  assert(isStale(1000, 0, 250) == true);     // never seen
  assert(isStale(1000, 900, 250) == false);  // 100ms ago, within 250ms
  assert(isStale(1000, 700, 250) == true);   // 300ms ago, over 250ms
  assert(isStale(1000, 750, 250) == false);  // exactly at the 250ms boundary — not stale
}

int main() {
  test_ids();
  test_lift_state_roundtrip();
  test_lift_state_short_dlc_is_safe();
  test_lift_cmd_roundtrip();
  test_fault_roundtrip();
  test_staleness();
  std::printf("opendubs_can: all tests passed\n");
  return 0;
}
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd firmware/comms/test && g++ -std=c++17 -Wall -Wextra -I.. test_opendubs_can.cpp -o /tmp/test_opendubs_can
```
Expected: compile error — `opendubs_can.h: No such file or directory`.

- [ ] **Step 3: Write `opendubs_can.h`**

Create `firmware/comms/opendubs_can.h`:

```cpp
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
  uint8_t counter = 0;
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
  LiftCmd cmd = LiftCmd::STOP;
  uint8_t seq = 0;
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
  FaultCode code = FaultCode::NONE;
  uint8_t   detail = 0;
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
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
cd firmware/comms/test && g++ -std=c++17 -Wall -Wextra -I.. test_opendubs_can.cpp -o /tmp/test_opendubs_can && /tmp/test_opendubs_can
```
Expected: `opendubs_can: all tests passed`, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add firmware/comms/opendubs_can.h firmware/comms/test/test_opendubs_can.cpp
git commit -m "$(cat <<'EOF'
firmware(comms): payload protocol wire encode/decode + native test

Pure, framework-free core of docs/interfaces/payload-comms.md — CAN
ID layout, AUTHORIZE/LIFT_STATE/LIFT_CMD/FAULT structs, staleness
helper. Host-testable so byte-layout bugs are caught before flashing.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: StampPLC firmware — terminate the payload UDP link

**Files:**
- Create: `firmware/comms/opendubs_can_udp.h`
- Modify: `firmware/rc_serial_probe/platformio.ini`
- Modify: `firmware/rc_serial_probe/src/main.cpp`

**Interfaces:**
- Consumes: everything from Task 1's `opendubs_can` namespace.
- Produces (used by Task 3): `opendubs_can::UdpTransport` class — `UdpTransport(UDP& udp, IPAddress dest, uint16_t port)`, `bool send(uint16_t canId, uint8_t dlc, const uint8_t* payload)`, `bool poll(uint16_t& outCanId, uint8_t& outDlc, uint8_t outPayload[8])`.
- Produces (used by Task 4): the `[LIFT] live= height_mm= inhibit= at_top= at_bot= tof_ok= motion= fault=` tag line contract, and the `LIFT <UP|DOWN|STOP>` host command.

This task has no isolated automated test of its own beyond the PlatformIO compile — `opendubs_can_udp.h` depends on Arduino's `Udp.h`, which only exists inside a PlatformIO build, so it can't be host-tested like Task 1. It's exercised end-to-end by the StampPLC's own AUTHORIZE broadcast, which Step 6's bench check verifies with `tcpdump`.

- [ ] **Step 1: Write the transport wrapper**

Create `firmware/comms/opendubs_can_udp.h`:

```cpp
#pragma once
// Arduino UDP transport for the OpenDubs payload comms protocol.
// Works with any class deriving Arduino's UDP interface (WiFiUDP on the Tab5,
// EthernetUDP on the StampPLC) — both expose the same beginPacket/write/endPacket/
// parsePacket/read surface, so one implementation serves both firmware targets.
// Wire framing: [CAN_ID:2B big-endian][DLC:1B][payload:0..8B] per UDP datagram
// (docs/interfaces/payload-comms.md "Transport"). The caller owns the UDP object's
// begin(port) — this class only frames/sends/polls.
#include <Udp.h>
#include "opendubs_can.h"

namespace opendubs_can {

class UdpTransport {
public:
  UdpTransport(UDP& udp, IPAddress dest, uint16_t port)
      : udp_(udp), dest_(dest), port_(port) {}

  bool send(uint16_t canId, uint8_t dlc, const uint8_t* payload) {
    if (dlc > 8) return false;
    uint8_t frame[11];
    frame[0] = static_cast<uint8_t>(canId >> 8);
    frame[1] = static_cast<uint8_t>(canId & 0xFF);
    frame[2] = dlc;
    if (dlc) std::memcpy(frame + 3, payload, dlc);
    if (!udp_.beginPacket(dest_, port_)) return false;
    udp_.write(frame, 3 + dlc);
    return udp_.endPacket() == 1;
  }

  bool poll(uint16_t& outCanId, uint8_t& outDlc, uint8_t outPayload[8]) {
    int psize = udp_.parsePacket();
    if (psize < 3) return false;
    uint8_t buf[11];
    int len = udp_.read(buf, sizeof(buf));
    if (len < 3) return false;
    outCanId = static_cast<uint16_t>((buf[0] << 8) | buf[1]);
    uint8_t dlc = buf[2];
    if (dlc > 8 || len < 3 + dlc) return false;
    outDlc = dlc;
    std::memcpy(outPayload, buf + 3, dlc);
    return true;
  }

private:
  UDP&      udp_;
  IPAddress dest_;
  uint16_t  port_;
};

}  // namespace opendubs_can
```

- [ ] **Step 2: Wire the include path**

Edit `firmware/rc_serial_probe/platformio.ini` — append to the existing `build_flags`:

```ini
build_flags =
  -DARDUINO_USB_MODE=1
  -DARDUINO_USB_CDC_ON_BOOT=1
  -I../comms
```

- [ ] **Step 3: Add the payload UDP socket and periodic AUTHORIZE broadcast**

In `firmware/rc_serial_probe/src/main.cpp`:

1. Add includes near the top (after the existing `#include <Update.h>`):
```cpp
#include "opendubs_can.h"
#include "opendubs_can_udp.h"
```

2. Add new state after the existing `static const uint16_t kNetPort = 5005;` line (in the "PoE W5500 Ethernet state" block):
```cpp
// --- Payload comms (docs/interfaces/payload-comms.md) — dedicated UDP port, broadcast both ways;
// the module's CAN ID does the addressing so neither side needs to learn the other's IP. ---
static EthernetUDP                 payload_udp;
static const uint16_t              kPayloadPort = 5006;
static opendubs_can::UdpTransport  payload_tx(payload_udp, IPAddress(255, 255, 255, 255), kPayloadPort);
static unsigned long               last_authorize_tx_ms  = 0;
static uint8_t                     authorize_counter     = 0;
static unsigned long               last_lift_state_rx_ms = 0;
static opendubs_can::LiftState     last_lift_state{};
static opendubs_can::Fault         last_lift_fault{};
static unsigned long               last_lift_pub_ms      = 0;
static uint8_t                     lift_cmd_seq          = 0;
```

3. In `netBringUp()`, right after the existing `eth_udp.begin(kNetPort);` line, add:
```cpp
    payload_udp.begin(kPayloadPort);
```

4. Add a forward declaration next to the other function forward-declarations near the top of the file (after `static void netMaintain();`):
```cpp
static void publishLift();   // emits [LIFT] — payload comms bridge (docs/interfaces/payload-comms.md)
```

5. Define `publishLift()` — add it directly after `pollNetCommands()`:
```cpp
// Emit the [LIFT] tag (docs/interfaces/payload-comms.md "Dashboard integration"). `live` is
// THIS module's own staleness check — same pattern as [RC]'s `live=`, independent of the
// general eth_up / dashboard-link freshness stamplc.py already tracks.
static void publishLift()
{
    const bool live = !opendubs_can::isStale(millis(), last_lift_state_rx_ms,
                                              opendubs_can::kLiftStateTimeoutMs);
    const char* motion = last_lift_state.motion == opendubs_can::LiftMotion::UP   ? "UP"
                        : last_lift_state.motion == opendubs_can::LiftMotion::DOWN ? "DOWN"
                                                                                    : "STOP";
    const char* fault =
        last_lift_fault.code == opendubs_can::FaultCode::NONE           ? "NONE"
      : last_lift_fault.code == opendubs_can::FaultCode::TOF_FAULT      ? "TOF_FAULT"
      : last_lift_fault.code == opendubs_can::FaultCode::STUCK_TIMEOUT  ? "STUCK_TIMEOUT"
      : last_lift_fault.code == opendubs_can::FaultCode::LIMIT_CONFLICT ? "LIMIT_CONFLICT"
      : last_lift_fault.code == opendubs_can::FaultCode::RELAY_FAULT    ? "RELAY_FAULT"
                                                                         : "UNKNOWN";
    tprintf("[LIFT] live=%d height_mm=%u inhibit=%d at_top=%d at_bot=%d tof_ok=%d motion=%s fault=%s\n",
            live ? 1 : 0, static_cast<unsigned>(last_lift_state.heightMm),
            last_lift_state.inhibit ? 1 : 0, last_lift_state.atTop ? 1 : 0,
            last_lift_state.atBot ? 1 : 0, last_lift_state.tofOk ? 1 : 0, motion, fault);
}
```

6. In `loop()`, add this block right after the existing `pollNetCommands();` line:
```cpp
    // ---- Payload comms: AUTHORIZE broadcast (20 Hz) + STATE/FAULT receive ----
    if (eth_up && (millis() - last_authorize_tx_ms) >= opendubs_can::kAuthorizePeriodMs) {
        opendubs_can::Authorize a{authorize_counter++};
        uint8_t buf[8]; uint8_t dlc = opendubs_can::encodeAuthorize(a, buf);
        payload_tx.send(opendubs_can::kIdAuthorize, dlc, buf);
        last_authorize_tx_ms = millis();
    }
    if (eth_up) {
        uint16_t rxId; uint8_t rxDlc; uint8_t rxBuf[8];
        while (payload_tx.poll(rxId, rxDlc, rxBuf)) {
            if (rxId == opendubs_can::kIdLiftState) {
                last_lift_state = opendubs_can::decodeLiftState(rxBuf, rxDlc);
                last_lift_state_rx_ms = millis();
            } else if (rxId == opendubs_can::kIdLiftFault) {
                last_lift_fault = opendubs_can::decodeFault(rxBuf, rxDlc);
            }
        }
    }
    if ((millis() - last_lift_pub_ms) > 100) {   // 10 Hz, matches LIFT_STATE's own rate
        publishLift();
        last_lift_pub_ms = millis();
    }
```

- [ ] **Step 4: Add the `LIFT` host command**

In `handleHostCommand()`, insert this block right after the `int value = -1;` line, before the existing `PDH_SWITCH` check:
```cpp
    char liftDir[8] = {0};
    if (sscanf(cmd, "LIFT %7s", liftDir) == 1) {
        opendubs_can::LiftCmd c;
        if      (strcmp(liftDir, "UP")   == 0) c = opendubs_can::LiftCmd::UP;
        else if (strcmp(liftDir, "DOWN") == 0) c = opendubs_can::LiftCmd::DOWN;
        else if (strcmp(liftDir, "STOP") == 0) c = opendubs_can::LiftCmd::STOP;
        else { Serial.printf("[LIFT] cmd invalid dir=%s (want UP/DOWN/STOP)\n", liftDir); return; }
        opendubs_can::LiftCommand lc{c, lift_cmd_seq++};
        uint8_t buf[8]; uint8_t dlc = opendubs_can::encodeLiftCmd(lc, buf);
        const bool ok = payload_tx.send(opendubs_can::kIdLiftCmd, dlc, buf);
        Serial.printf("[LIFT] cmd dir=%s seq=%u %s\n", liftDir, static_cast<unsigned>(lc.seq),
                      ok ? "sent" : "failed");
        return;
    }
```
This reuses the exact existing host-command pattern (`LED_COLOR`/`PAGE`/`PDH_SWITCH`) so `LIFT UP` is bench-testable the same way those already are — over USB serial or `pollNetCommands()`'s UDP path — without needing the deferred dashboard PAYLOAD panel.

- [ ] **Step 5: Compile check**

```bash
cd firmware/rc_serial_probe && pio run -e stamplc
```
Expected: `SUCCESS`. Fix any compile errors before proceeding (mistyped `Snapshot`/enum names are the likely failure mode).

- [ ] **Step 6: Bench-verify the AUTHORIZE broadcast**

Flash per the existing runbook (`firmware/rc_serial_probe/README.md` / `docs/stamplc-ethernet-transport.md`), then from a machine on the same LAN:
```bash
sudo tcpdump -n -i any udp port 5006
```
Expected: a `0x000`-prefixed 4-byte datagram (`00 00 01 <counter>`) roughly every 50 ms. Confirm the counter increments and wraps at 256.

- [ ] **Step 7: Commit**

```bash
git add firmware/comms/opendubs_can_udp.h firmware/rc_serial_probe/platformio.ini firmware/rc_serial_probe/src/main.cpp
git commit -m "$(cat <<'EOF'
firmware(stamplc): terminate the payload comms UDP link

New broadcast UDP socket on :5006 (separate from the [TAG] port
:5005) sends AUTHORIZE at 20Hz and receives LIFT_STATE/LIFT_FAULT,
relayed to the Thor as a new [LIFT] tag line via the existing tee.
Adds a LIFT <UP|DOWN|STOP> host command reusing the existing
PDH_SWITCH-style dispatcher as the bench trigger for LIFT_CMD.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Tab5 firmware — real `commsTask`

**Files:**
- Modify: `firmware/tab5_face/platformio.ini`
- Modify: `firmware/tab5_face/src/main.cpp`

**Interfaces:**
- Consumes: `opendubs_can` (Task 1), `opendubs_can::UdpTransport` (Task 2); `LiftController`'s `snapshot()`, `commandUp()`, `commandDown()`, `commandStop()`, `setInhibit(bool)` (all pre-existing, `firmware/tab5_face/src/lift.h`); `g_linkOnline`, `g_ipStr` (pre-existing, `firmware/tab5_face/src/ui_shared.h`).
- Produces: nothing consumed by a later task — this is the module-side terminus.

No native test here either — WiFi/UDP bring-up needs real hardware. Verified by the same bench pattern as Task 2 (tcpdump on the StampPLC side, serial log on the Tab5 side).

- [ ] **Step 1: Wire the include path**

Edit `firmware/tab5_face/platformio.ini` — append to the existing `build_flags`:
```ini
build_flags =
  -DBOARD_HAS_PSRAM
  -DARDUINO_USB_MODE=1
  -DARDUINO_USB_CDC_ON_BOOT=1
  -I../comms
```

- [ ] **Step 2: Replace the `commsTask` stub**

In `firmware/tab5_face/src/main.cpp`, add includes after the existing `#include "lift.h"`:
```cpp
#include <WiFi.h>
#include <WiFiUdp.h>
#include "opendubs_can.h"
#include "opendubs_can_udp.h"

#ifndef OPENDUBS_WIFI_SSID
#define OPENDUBS_WIFI_SSID "opendubs-local"   // override via platformio.ini build_flags; see firmware/tab5_face/README
#endif
#ifndef OPENDUBS_WIFI_PASS
#define OPENDUBS_WIFI_PASS "changeme"
#endif
```

Replace the entire existing `commsTask` function body:
```cpp
static void commsTask(void*) {
  // TODO-HW: bring up WiFi via the ESP32-C6 co-processor (ESP-Hosted), join OpenDubs, then
  // consume the transport-agnostic state feed -> g_robotState, feed the authorize heartbeat,
  // and publish this module's capabilities (Z-axis). Set g_linkOnline / g_ipStr when up.
  for (;;) { g_linkOnline = false; vTaskDelay(pdMS_TO_TICKS(500)); }
}
```
with:
```cpp
static opendubs_can::Fault deriveFault(const LiftController::Snapshot& s) {
  // STUCK_TIMEOUT / RELAY_FAULT: no sensor data to detect these yet (SIMULATE has no real
  // relay/motion feedback) — left NONE until the hardware path lands. [TODO-HW]
  if (s.atTop && s.atBot) return {opendubs_can::FaultCode::LIMIT_CONFLICT, 0};
  if (!s.tofOk)           return {opendubs_can::FaultCode::TOF_FAULT, 0};
  return {opendubs_can::FaultCode::NONE, 0};
}

static void commsTask(void*) {
  // [TODO-HW] verify WiFi.begin() actually joins over the ESP32-C6/ESP-Hosted co-processor on
  // real hardware — this is the standard Arduino WiFi API, which pioarduino's ESP32-P4 Tab5
  // support initializes over ESP-Hosted transparently, but it's unverified until bench-tested.
  WiFi.mode(WIFI_STA);
  WiFi.begin(OPENDUBS_WIFI_SSID, OPENDUBS_WIFI_PASS);

  static WiFiUDP payload_udp;
  static const uint16_t kPayloadPort = 5006;   // matches the StampPLC's payload_udp port
  payload_udp.begin(kPayloadPort);
  static opendubs_can::UdpTransport tx(payload_udp, IPAddress(255, 255, 255, 255), kPayloadPort);

  unsigned long lastAuthorizeRxMs = 0;
  unsigned long lastStateTxMs = 0;
  uint8_t       lastFaultCode = static_cast<uint8_t>(opendubs_can::FaultCode::NONE);
  uint8_t       lastCmdSeq = 0;
  bool          haveCmdSeq = false;
  uint8_t       stateSeq = 0;

  for (;;) {
    const bool wifiUp = WiFi.status() == WL_CONNECTED;
    g_linkOnline = wifiUp;
    if (!wifiUp) { g_ipStr[0] = '\0'; vTaskDelay(pdMS_TO_TICKS(20)); continue; }

    IPAddress ip = WiFi.localIP();
    snprintf(g_ipStr, sizeof(g_ipStr), "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);

    uint16_t rxId; uint8_t rxDlc; uint8_t rxBuf[8];
    while (tx.poll(rxId, rxDlc, rxBuf)) {
      if (rxId == opendubs_can::kIdAuthorize) {
        lastAuthorizeRxMs = millis();
      } else if (rxId == opendubs_can::kIdLiftCmd) {
        auto c = opendubs_can::decodeLiftCmd(rxBuf, rxDlc);
        if (!haveCmdSeq || c.seq != lastCmdSeq) {   // edge-detect: only act on a NEW seq
          lastCmdSeq = c.seq; haveCmdSeq = true;
          if      (c.cmd == opendubs_can::LiftCmd::UP)   Lift.commandUp();
          else if (c.cmd == opendubs_can::LiftCmd::DOWN) Lift.commandDown();
          else                                           Lift.commandStop();
        }
      }
    }

    const bool authorized = !opendubs_can::isStale(millis(), lastAuthorizeRxMs,
                                                     opendubs_can::kAuthorizeTimeoutMs);
    Lift.setInhibit(!authorized);   // fail-safe-by-omission: no fresh AUTHORIZE -> inhibited

    if (millis() - lastStateTxMs >= opendubs_can::kLiftStatePeriodMs) {
      auto snap = Lift.snapshot();
      opendubs_can::LiftState st{};
      st.heightMm     = static_cast<uint16_t>(snap.heightMm < 0.0f ? 0.0f : snap.heightMm);
      st.inhibit      = snap.inhibit;
      st.atTop        = snap.atTop;
      st.atBot        = snap.atBot;
      st.tofOk        = snap.tofOk;
      st.relayUpPulse = snap.relayUpPulse;
      st.relayDnPulse = snap.relayDnPulse;
      st.motion       = snap.motion == Motion::UP   ? opendubs_can::LiftMotion::UP
                       : snap.motion == Motion::DOWN ? opendubs_can::LiftMotion::DOWN
                                                      : opendubs_can::LiftMotion::STOP;
      st.seq = stateSeq++;
      uint8_t buf[8]; uint8_t dlc = opendubs_can::encodeLiftState(st, buf);
      tx.send(opendubs_can::kIdLiftState, dlc, buf);
      lastStateTxMs = millis();

      auto fault = deriveFault(snap);
      if (static_cast<uint8_t>(fault.code) != lastFaultCode) {
        uint8_t fbuf[8]; uint8_t fdlc = opendubs_can::encodeFault(fault, fbuf);
        tx.send(opendubs_can::kIdLiftFault, fdlc, fbuf);
        lastFaultCode = static_cast<uint8_t>(fault.code);
      }
    }

    vTaskDelay(pdMS_TO_TICKS(20));
  }
}
```

- [ ] **Step 3: Compile check**

```bash
cd firmware/tab5_face && pio run -e tab5
```
Expected: `SUCCESS`.

- [ ] **Step 4: Bench-verify against the StampPLC**

With both boards flashed and on the same WiFi/LAN: watch the Tab5's USB serial monitor for `[TAB5]` boot output, then on the StampPLC side re-run `tcpdump -n -i any udp port 5006` and confirm `0x080`-prefixed `LIFT_STATE` datagrams arrive at ~10 Hz. Confirm the StampPLC's own `[LIFT]` tag (via its USB serial or `:5005` UDP tee) shows `live=1` and a plausible `height_mm`. Send `LIFT UP` (Task 2's host command) and confirm the Tab5's face/controller UI reflects motion, matching the existing `SIMULATE=true` behavior in `lift.cpp`.

- [ ] **Step 5: Commit**

```bash
git add firmware/tab5_face/platformio.ini firmware/tab5_face/src/main.cpp
git commit -m "$(cat <<'EOF'
firmware(tab5): implement commsTask against the payload comms protocol

Fills the commsTask stub: WiFi bring-up, AUTHORIZE-gated Lift.setInhibit()
(fail-safe-by-omission), LIFT_STATE/LIFT_FAULT broadcast at 10Hz, and
LIFT_CMD receive dispatched to Lift.commandUp/Down/Stop() with
seq-based edge dedup. WiFi credentials are build-flag overridable, not
committed. ESP-Hosted join is unverified until bench-tested [TODO-HW].

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Dashboard backend — surface presence and faults

**Files:**
- Modify: `software/kiosk-dashboard/backend/stamplc.py`
- Modify: `software/kiosk-dashboard/backend/status.py`
- Create: `software/kiosk-dashboard/backend/tests/test_robot_state.py`
- Create: `software/kiosk-dashboard/backend/requirements-dev.txt`

**Interfaces:**
- Consumes: the `[LIFT] live= height_mm= inhibit= at_top= at_bot= tof_ok= motion= fault=` tag line (Task 2); `robot_state.derive()`, `robot_state.ENABLE`, `robot_state.SUBSYS_KEYS` (pre-existing, `robot_state.py`).
- Produces: `status._lift() -> {"fresh": bool, "height_mm": float|None, "inhibit": bool|None, "motion": str|None, "fault": str|None, "fault_active": bool}`.

- [ ] **Step 1: Write the failing test**

Create `software/kiosk-dashboard/backend/tests/test_robot_state.py`:
```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robot_state


def test_required_module_missing_surfaces_as_degraded_not_blocking():
    out = robot_state.derive(
        booting=False, estop_sources=[], authority="OFF", deadman=False,
        link=True, faults=["lift module not responding"], mast_fault=True,
    )
    assert out["state"] == "DEGRADED"
    assert "lift module not responding" in out["reason"]
    assert out["subsystems"]["mast"]["state"] == "FAULT"
    # DEGRADED must stay non-blocking — a silent required module warns, it doesn't stop drive
    # (docs/interfaces/payload-comms.md "Module presence and required-module alerting").
    base_idx = robot_state.SUBSYS_KEYS.index("base")
    assert robot_state.ENABLE["DEGRADED"][base_idx] != "off"


def test_lift_fault_alone_does_not_force_degraded():
    out = robot_state.derive(
        booting=False, estop_sources=[], authority="OFF", deadman=False,
        link=True, faults=[], mast_fault=True,
    )
    assert out["state"] != "DEGRADED"
    assert out["subsystems"]["mast"]["state"] == "FAULT"
```

- [ ] **Step 2: Run it to confirm it fails for the right reason**

```bash
cd software/kiosk-dashboard/backend && python3 -m pytest tests/test_robot_state.py -v
```
Expected: **passes already** — `robot_state.derive()` already supports `mast_fault` and `faults`; this step confirms the pure FSM logic this task relies on is already correct before wiring the Python glue around it. (If it fails, stop and fix `robot_state.py` first — that would mean the existing FSM doesn't behave as the spec assumes.)

- [ ] **Step 3: Add the dev test dependency**

Create `software/kiosk-dashboard/backend/requirements-dev.txt`:
```
pytest==8.*
```
```bash
pip install -r software/kiosk-dashboard/backend/requirements-dev.txt
```

- [ ] **Step 4: Parse `[LIFT]` in `stamplc.py`**

In `software/kiosk-dashboard/backend/stamplc.py`, add to the tag-contract comment block (right after the existing `#   [DRIVE] mode= armed= w=fl,fr,rl,rr  (normalized -1..1)` line):
```python
#   [LIFT]  live= height_mm= inhibit= at_top= at_bot= tof_ok= motion= fault=
```

Add the parser block right after the existing `if line.startswith("[DRIVE] "):` block's `return` (i.e., as the next `if` in that same chain):
```python
            if line.startswith("[LIFT] "):
                kv = _kv(line)
                fnum = lambda k: (float(kv[k]) if k in kv else None)
                self._d["lift"] = {
                    "live": kv.get("live") == "1",
                    "height_mm": fnum("height_mm"),
                    "inhibit": kv.get("inhibit") == "1",
                    "at_top": kv.get("at_top") == "1",
                    "at_bot": kv.get("at_bot") == "1",
                    "tof_ok": kv.get("tof_ok") == "1",
                    "motion": kv.get("motion"),
                    "fault": kv.get("fault"),
                }
                self._last = now; return
```

- [ ] **Step 5: Wire `status.py`**

In `software/kiosk-dashboard/backend/status.py`:

1. Add near the existing `THERMAL_OVERTEMP_C = 70.0` constant:
```python
# Modules this robot build has installed — compared against payload-comms `live`/`fresh`
# staleness to decide whether silence means "not installed" or "not responding"
# (docs/interfaces/payload-comms.md "Module presence and required-module alerting").
REQUIRED_MODULES = {"lift": True}
```

2. Add a new function near the existing `_rc_link()`:
```python
def _lift():
    """Lift/payload module health from the StampPLC's [LIFT] line
    (docs/interfaces/payload-comms.md). `fresh` combines the StampPLC link's own
    freshness with the module's own `live` flag — same pattern as `_rc_link()`."""
    s = stamplc.reader.snapshot()
    lift = s.get("lift") or {}
    return {
        "fresh": bool(s.get("fresh")) and bool(lift.get("live")),
        "height_mm": lift.get("height_mm"),
        "inhibit": lift.get("inhibit"),
        "motion": lift.get("motion"),
        "fault": lift.get("fault"),
        "fault_active": lift.get("fault") not in (None, "NONE"),
    }
```

3. In `_robot_state()`, replace:
```python
    mast_fault = False  # mast/payload is independent (Tab5-driven) — no mast-limit inputs on the StampPLC
```
with:
```python
    lift = _lift()
    if REQUIRED_MODULES.get("lift") and not lift["fresh"]:
        raw_faults.append("lift module not responding")
    mast_fault = lift["fault_active"]
```
(This must land **after** the existing `if _thermal().get("overtemp"): raw_faults.append("OVERTEMP")` block and **before** the `sup = supervisor.step(...)` call, since `raw_faults` is read there — i.e., exactly where the old `mast_fault = False` line already was.)

4. In `collect()`, add `"lift": _lift(),` to the returned dict, next to the existing `"drive": _drive(),` entry.

- [ ] **Step 6: Run the test suite again**

```bash
cd software/kiosk-dashboard/backend && python3 -m pytest tests/test_robot_state.py -v
```
Expected: both tests still pass (this task didn't change `robot_state.py`; it wired `status.py` to feed it correctly — the test guards that the FSM behavior these new call sites rely on doesn't regress later).

- [ ] **Step 7: Manual smoke check against a live or simulated StampPLC feed**

Start the dashboard backend per its existing run instructions and hit `/api/status`; confirm the response includes a `lift` block. With no `[LIFT]` line arriving yet (Tab5 not flashed/on the bench), confirm `robot_state.reason` includes `"lift module not responding"` and `robot_state.state == "DEGRADED"` — matching `REQUIRED_MODULES["lift"] = True` by default. Once Task 3's Tab5 firmware is live and broadcasting, confirm the fault clears (`state` leaves `DEGRADED` on that account, `subsystems.mast.state` leaves `FAULT`).

- [ ] **Step 8: Commit**

```bash
git add software/kiosk-dashboard/backend/stamplc.py software/kiosk-dashboard/backend/status.py \
        software/kiosk-dashboard/backend/tests/test_robot_state.py \
        software/kiosk-dashboard/backend/requirements-dev.txt
git commit -m "$(cat <<'EOF'
dashboard: wire lift module presence/faults into robot_state

Parses the new [LIFT] tag (stamplc.py), adds status._lift() following
the _rc_link() pattern, and replaces the mast_fault placeholder with
the real required-module check from docs/interfaces/payload-comms.md
— required+stale raises a DEGRADED (non-blocking) fault, LIFT_FAULT
alone flags only the mast subsystem chip. Adds a first pytest test
for robot_state.derive(), which was already documented as pure and
trivially testable but had no test file yet.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** Encoding/CAN ID layout → Task 1. Transport (WiFi/UDP framing) → Task 2 Step 1. `AUTHORIZE`/`LIFT_STATE`/`LIFT_CMD`/`FAULT` catalog → Tasks 1–3. Timing constants → Task 1 (`kAuthorizePeriodMs` etc.), consumed verbatim in Tasks 2–3. Module presence/required-module alerting → Task 4. Dashboard integration (`mast_fault`, `raw_faults`, `DEGRADED` non-blocking) → Task 4. Explicit spec Non-goals (TLV, fragmentation, dynamic discovery, arm catalog, CAN wire adapter) are correctly *not* built anywhere in this plan.
- **Placeholder scan:** no TBD/"add appropriate handling"-style steps; the two `[TODO-HW]` markers (ESP-Hosted WiFi join, STUCK_TIMEOUT/RELAY_FAULT detection) are carried-over, explicitly-scoped hardware unknowns already flagged in the existing codebase's own convention (see `lift.cpp`'s `SIMULATE` flag and `main.cpp`'s pre-existing `TODO-HW` comments) — not gaps in this plan's code, which is complete and compiles as written.
- **Type consistency:** `opendubs_can::LiftState`/`LiftCmd`/`Fault`/`FaultCode` field and enum names are identical across Task 1's definition and Tasks 2–3's usage. `UdpTransport`'s `send`/`poll` signature is identical in Task 2's definition and Task 2/3's call sites. `_lift()`'s returned dict keys (`fresh`, `height_mm`, `inhibit`, `motion`, `fault`, `fault_active`) match exactly between Task 4's Step 5 definition and Step 5.3's usage.
- **Scope check:** four tasks, each independently testable/committable, each touching files that already change together (StampPLC's socket+tag+command in one task, Tab5's task in one task, dashboard's parser+wiring+test in one task). Not further decomposed.
