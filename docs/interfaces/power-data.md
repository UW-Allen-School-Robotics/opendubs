# Power & data interface

The **contract a payload builds to** — the rails it may draw on and the buses it may speak. This
page is only the payload-facing side; for the whole-robot picture see the
[system overview](../system-overview.md) bus map, and for the power spine (battery → e-stop
contactor → rails → fusing) see [power architecture](../power-architecture.md).

## Rails a payload may tap

| Rail | Where it sits | Use it for | Budget |
|---|---|---|---|
| **Logic 24 V** | always-on, upstream of the e-stop contactor | compute/sensors that must **survive an e-stop** | *TODO — per-payload budget* |
| **Drive 24 V** | behind the e-stop contactor (de-energizes on e-stop) | actuators / motors | *TODO — per-payload budget* |
| **52 V add-on** | direct off the ~52 V pack | a payload may run its own **48 V-class bus** | *TODO* |
| **AC (future)** | via an inverter off the battery | payloads needing AC | *not yet spec'd* |

Both 24 V buses expose **fused branches** — logic from the **REV Mini Power Module** in the
[Electronics Bay](../build/electronics-bay.md), drive from the **REV PDH** in the
[Power Bay](../build/power-bay.md).

*TODO: how a payload requests/adds a fused branch, and the payload **connector standard** + pinout.*

## Buses a payload may speak

How a payload integrates over the base's buses:

- **Ethernet** — the robot LAN (also the StampPLC ↔ Thor USB-less link).
- **WiFi** — on-robot wireless (dashboard / tablet, offboard links).
- **CAN** — the **StampPLC is the CAN master**; join as a node (the REV PDH is a telemetry node, not a controller).
- **RS485** — from the StampPLC (currently the **battery / BMS** only; spare capacity for other devices).
- **USB** — to Thor; prefer another bus where you can (USB layout matters — see [Thor setup](../thor-setup.md)).
- **I²C** — the M5 bus is **5 V**; 3.3 V devices need a level converter (pattern: [OTOS](../build/otos.md) / [Pinpoint](../build/pinpoint.md)).

## The contract

A payload that **stays within its rail's current budget** and **speaks one of the buses above through
the standard connector** is electrically compatible with the base — with no changes to the robot.
