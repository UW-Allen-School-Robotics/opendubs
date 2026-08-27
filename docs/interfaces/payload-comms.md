# Payload communication protocol

**Status: designed, not yet implemented.** The message protocol a payload module speaks to the
base — command/telemetry frame layout, transport, and presence/safety semantics. Once
implemented, `firmware/tab5_face`'s `commsTask` stub and the punch-list's
["CAN payload protocol spec"](../../notes/punch-list.md) item are this document.

This is the *protocol* only — how bytes are laid out and moved. It does not cover *sending and
receiving* it (the transport adapter's concrete implementation), which is separate follow-on work.

## Principles

- **Hardware-compatible by construction.** A payload module speaks CAN framing regardless of
  the physical wire — the same canonical bytes ride a real CAN bus, or get carried inside a UDP
  datagram over Ethernet/WiFi. Swapping the physical transport never touches the message layer.
- **CAN-native, not CAN-derived.** Real-time messages (heartbeat, state, command) are sized to
  fit one classic CAN frame (≤8 bytes) by construction — never fragmented, regardless of which
  transport they actually ride.
- **Fixed struct per message type, no TLV.** The CAN ID is the only type discriminator a
  receiver needs; payload bytes are a fixed, known-at-compile-time layout. No self-describing
  fields, no runtime schema flexibility — adding a new message shape means defining a new
  message ID, not extending an existing one. (See [Non-goals](#non-goals) for why.)
- **Safety is fail-safe-by-omission, not delivery-guaranteed.** Presence of a heartbeat *grants*
  something (motion permission, module-required-and-healthy); absence revokes it. Nothing here
  depends on a message being reliably delivered — only on staleness being detected.

## Encoding

One canonical binary encoding for every message. A message is fully identified by its **11-bit
CAN ID**:

```
bit  10  9  8  7 | 6  5  4  3  2  1  0
     [ module ID ] [   message type   ]
```

- **Module ID** (bits 10–7, 4 bits): which physical module this message is to/from. Statically
  assigned per robot build — a module's ID is a compile-time constant on both the base and the
  module firmware, not negotiated at runtime (see [Module presence](#module-presence-and-required-module-alerting)
  for why dynamic discovery isn't needed here).
- **Message type** (bits 6–0, 7 bits): which message, within that module's namespace.
- **Module ID 0 is reserved** for base-wide broadcast messages (currently just `AUTHORIZE`).

Lowest numeric CAN ID wins arbitration on a real CAN bus, so reserving module 0 / type 0 →
`0x000` for `AUTHORIZE` means the safety heartbeat always wins bus contention over any other
traffic.

### Common message slots

Every module type reuses the same message-type offset for the same *kind* of message, so
base-side code that only needs presence/fault handling doesn't need to understand every module
type's specific state/command formats:

| Type offset | Name | Direction | Format |
|---|---|---|---|
| `0x00` | `STATE` | module → base | module-specific |
| `0x01` | `CMD` | base → module | module-specific |
| `0x02` | `FAULT` | module → base | common (below) |
| `0x03` | `PRESENT` | module → base | common (below) |

**`FAULT`** (common, 2 bytes): byte 0 = fault code (u8 enum, module-type-specific code space —
e.g. `NONE/SENSOR_FAULT/STUCK_TIMEOUT/RELAY_FAULT`), byte 1 = detail (u8, free-form per code).
Sent on change, plus a slow resend (~1 Hz) while a fault is active so a late-joining base learns
current state.

**`PRESENT`** (common, 2 bytes) — the reverse heartbeat: byte 0 = flags (bit0 = ready-for-commands,
bits 1–7 reserved), byte 1 = counter (u8, wraps, wire-health diagnostic only). Same 20 Hz / 250 ms
staleness convention as `AUTHORIZE` (below) — one timeout constant for the whole protocol.

### `AUTHORIZE` — `0x000`, base → broadcast

The forward heartbeat. Presence = motion permitted; staleness = every module stops, independent
of any in-flight command. This is the deadman pattern already used for the StampPLC's
autonomous-drive heartbeat, generalized to every payload module.

| Byte | Field |
|---|---|
| 0 | counter (u8), wraps — wire-health diagnostic only, not required for the fail-safe logic |

DLC = 1. **20 Hz** (50 ms period), **250 ms staleness timeout** (5× margin).

## Transport

One interface every transport implements; message-catalog code only calls this, never touches a
socket or a CAN controller directly:

```
send(canId, dlc, payload[≤8])
poll() -> (canId, dlc, payload, rxTimeMs) | none
isUp() -> bool
```

- **CAN wire adapter** — not yet implemented; no module has a CAN transceiver wired today (the
  StampPLC's CAN port is used for the REV PDH). Maps directly to native TWAI frames once built.
- **WiFi/UDP adapter** — today's real transport for the lift module (Tab5's ESP32-C6 co-processor).
  Each UDP datagram is `[CAN_ID:2B][DLC:1B][payload:0–8B]` — the CAN ID and DLC that a real CAN
  frame carries out-of-band get a 3-byte prefix since UDP has no equivalent framing. Uses a
  **dedicated port**, separate from the existing StampPLC `[TAG]` text protocol on `:5005`
  ([`stamplc-ethernet-transport.md`](../stamplc-ethernet-transport.md)) — binary and text framing
  don't share a socket.
- A bridge between a CAN segment and a WiFi segment is a byte-copy of the payload plus
  add/strip the 3-byte UDP prefix — never a re-encode.

## Message catalog — module 1: lift (scissor lift)

The only module type this spec is validated against (see [Non-goals](#non-goals)). Fields taken
from the real controller model in
[`firmware/tab5_face/src/lift.h`](../../firmware/tab5_face/src/lift.h) — relay-pulse + ToF
height sensing, not a PWM/position-servo lift.

### `LIFT_STATE` — `0x080`, module → base

| Byte | Field | Notes |
|---|---|---|
| 0–1 | `heightMm` (u16, big-endian) | mm; matches `Snapshot.heightMm` (0–900 mm today) |
| 2 | flags | bit0 `inhibit`, bit1 `atTop`, bit2 `atBot`, bit3 `tofOk`, bit4 `relayUpPulse`, bit5 `relayDnPulse`, bits6–7 `motion` (0=STOP/1=UP/2=DOWN) |
| 3 | seq (u8) | increments per publish — lets the base detect drops distinct from `PRESENT`/`AUTHORIZE` staleness |

DLC = 4 (bytes 4–7 reserved). **10 Hz**, flat rate (no idle/moving rate switching — not needed
while this module rides WiFi/UDP rather than a bandwidth-constrained CAN wire).

### `LIFT_CMD` — `0x081`, base → module

Edge-triggered, not a target position — the real firmware is pulse-toggle (same direction again
= stop; see `LiftController::moveCmd_`), not continuous setpoint tracking.

| Byte | Field | Notes |
|---|---|---|
| 0 | cmd (u8 enum) | 0=STOP, 1=UP, 2=DOWN |
| 1 | seq (u8) | increments per operator press — retransmit-safe edge detection over a lossy transport |

DLC = 2. Sent once per operator action, not periodic. **No ack/retry** — a dropped command
packet reads the same as a missed physical button press (the operator taps again); this is a
convenience gap, not a safety one, because safety is enforced by `AUTHORIZE` staleness, not by
command delivery.

### `LIFT_FAULT` — `0x082` (uses the common `FAULT` format)

Fault codes: `NONE`, `TOF_FAULT`, `STUCK_TIMEOUT`, `LIMIT_CONFLICT`, `RELAY_FAULT`.

### `LIFT_PRESENT` — `0x083` (uses the common `PRESENT` format)

## Module presence and required-module alerting

`PRESENT` staleness alone can't distinguish "this robot build doesn't have this module" from
"this module is supposed to be here and isn't talking" — both look identical on the wire
(silence). Telling them apart needs an expectation to compare reality against.

The base carries a small static table per robot build, e.g.:

```
{ MODULE_LIFT, required: true }
```

Compared against live `PRESENT` staleness:

| Declared | `PRESENT` state | Base behavior |
|---|---|---|
| required | fresh | normal |
| required | stale | **fault surfaced** ("lift not responding") |
| not required | stale | quiet — correct default, nothing installed |
| not required | fresh | anomaly flagged — unexpected module on this build |

This table is a firmware constant per build, not a runtime UI — with one module type today
there's one entry. A config *screen* was considered and deferred: `PRESENT` staleness already
drives per-panel graceful-no-data rendering on the dashboard (matches the existing pattern of
isolated per-frame renderers) without needing the required-table; the table's only job is
deciding whether absence should raise an alarm.

## Non-goals

- **TLV / self-describing fields.** Considered for the discovery/capability-announce case, but
  with module scope fixed to a single, statically-addressed module type there's no variable
  field set to describe generically — a fixed struct per message type is simpler and has zero
  parse overhead. Revisit if a module type with genuinely optional/variable telemetry fields
  shows up.
- **Dynamic runtime module discovery** (an unknown device announcing itself on the bus).
  Module IDs are static, assigned per robot build at compile time on both ends; `PRESENT` +
  the required-table above covers the actual need (know if an expected module is talking).
- **Arm (OpenArm) message catalog.** Out of scope for this pass — this spec is validated only
  against the lift module, the one real target. A second module type is the natural next
  validation point for the schema's generality, not a currently-defined catalog.
- **CAN wire adapter implementation, and send/receive plumbing generally.** This document
  specifies the protocol; implementing the transport adapters (the WiFi/UDP one now, CAN one
  later) and wiring them into `firmware/tab5_face`'s `commsTask` and the StampPLC firmware is
  separate follow-on work.

## See also

- [Power & data interface](power-data.md) — the physical rails/buses a payload may use.
- [Command & telemetry interface](command-telemetry.md) — the base-level control-authority
  contract (RC/tablet/autonomy → base), a different layer than base ↔ payload-module comms.
- [`notes/punch-list.md`](../../notes/punch-list.md) §F — payload buildout tracking.
