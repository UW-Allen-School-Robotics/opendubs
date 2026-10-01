# Power & data interface

The **contract a payload builds to** — the rails it may draw on and the buses it may speak. This
page is only the payload-facing side; for the whole-robot picture see the
[system overview](../system-overview.md) bus map, and for the power spine (battery → e-stop
contactor → rails → fusing) see [power architecture](../power-architecture.md).

## Rails a payload may tap

| Rail | Where it sits | Use it for | Budget |
|---|---|---|---|
| **Logic 24 V** | always-on, upstream of the e-stop contactor | only what must **survive an e-stop** | **scarce — see below** |
| **Drive 24 V** | behind the e-stop contactor (de-energizes on e-stop) | actuators, motors, and a payload's own compute unless it must outlive an e-stop | the default for payloads |
| **52 V add-on** | direct off the ~52 V pack | a payload may run its own **48 V-class bus** | *TODO* |
| **AC (future)** | via an inverter off the battery | payloads needing AC | *not yet spec'd* |

Both 24 V buses expose **fused branches** — logic from the **REV Mini Power Module** in the
[Electronics Bay](../build/electronics-bay.md), drive from the distribution in the
[Power Bay](../build/power-bay.md).

**The logic bus is nearly full.** The Mini Power Module has **six** branches and five are
committed: the StamPLC, the e-stop signal, an accessory bus, the Thor (10 A) and the PRISM. Every
branch but the Thor's is fused at 5 A. That leaves **one spare position** plus whatever the shared
accessory branch can carry — so a payload wanting always-on power has to justify it, and may have
to share. Actuators were never candidates for it.

*TODO: the payload **connector standard** + pinout.*

## Buses a payload may speak

How a payload integrates over the base's buses:

- **Ethernet** — the robot LAN (also the StamPLC ↔ Thor USB-less link).
- **WiFi** — on-robot wireless (dashboard / tablet, offboard links).
- **CAN** — the **StamPLC is the CAN master**; join as a node. A build using a fuse block for
  drive distribution has no other node on this bus, so it is effectively all yours.
- **RS485** — from the StamPLC (currently the **battery / BMS** only; spare capacity for other devices).
- **USB** — to Thor; prefer another bus where you can (USB layout matters — see [Thor setup](../thor-setup.md)).
- **I²C** — the M5 bus is **5 V**; 3.3 V devices need a level converter (pattern: [OTOS](../build/otos.md) / [Pinpoint](../build/pinpoint.md)).

## The contract

Six rules. A payload that honours them mounts on the base with no changes to the robot.

**1 · Take power from the drive bus.** It de-energizes when the e-stop opens, and that is the
point: a payload's motion must stop when the robot's does. Ask for logic power only for the part
that genuinely has to outlive an e-stop — a controller that must keep reporting, not a motor.

**2 · Isolate.** Galvanic isolation on every data connection into the base is mandatory, not a
nicety. The M5 I²C bus is 5 V and un-isolated, grounds are shared across the frame, and a payload
is the one part of the robot its builder did not design. Isolation is what stops a payload fault
propagating into the base's buses, and what stops a ground loop between two subsystems that each
believe they are the ground reference.

**3 · Be self-contained.** Bring your own controller. A payload must be able to reach a safe state
using only what it carries — never by asking the base's compute to intervene. The base may be busy,
rebooting, or unreachable exactly when it matters.

**4 · Fail safe when the power vanishes.** An e-stop is a **power cut, not a message**: the drive
bus drops with no warning and no orderly shutdown. Whatever the payload was doing mid-motion, the
state it lands in with the power gone must be a safe one. Design for that rather than expecting to
be notified first.

**5 · If you keep running, watch for the state broadcast.** A payload on logic power survives the
e-stop and therefore has to be *told*. Robot state is broadcast by the StamPLC rather than polled,
and it is the only way an always-on payload learns the robot has stopped. Treat a stale broadcast
the same as a stop.

**6 · Claiming an axis goes through the arbiter.** A payload that commands motion is a source like
any other: it holds a lease with an expiry, it is bounded by the same monitors, and it gets no
private path to the actuators. See [Control authority](../control-authority.md).

For the message layout itself — CAN framing, the 11-bit module/type ID, and heartbeat semantics
where presence grants and absence revokes — see
**[Payload communications](payload-comms.md)**. That specification and its unit tests are in the
tree; neither firmware speaks it yet, so today this page is the half of the contract the robot
actually enforces.
