# Power & data interface

*(Draft.)* This page is **part architecture, part interface** — it describes how the robot's
power and comms are organized, and what an add-on payload can draw on and talk to.

## Power architecture + what a payload can tap

- **~52 V nominal battery** — an **ECO-WORTHY LiFePO4 (16S), JBD BMS** pack is the **locked
  baseline** source. A 52→24 V DC/DC derives the robot's working rails from it. Because the pack
  is 52 V nominal, a builder can also **run a 48 V-class robotics system on their own add-on
  bus** off the battery if they want.
- **24 V drive rail** — behind the e-stop contactor; de-energizes on e-stop. For actuators/motors.
- **24 V logic rail** — always-on, upstream of the contactor. For compute/sensors that must
  survive an e-stop.
- **AC (future, not yet spec'd)** — the battery can drive an **inverter** to provide AC to
  payloads that need it. TODO: rating + placement.

**Fused branch circuits exist on *both* buses:**

- **Logic bus** — fused branches from the **REV Mini Power Module** in the
  [Electronics Bay](../build/electronics-bay.md).
- **Drive bus** — fused branches from the **REV PDH** (or a fuse block) in the
  [Power Bay](../build/power-bay.md).

*TODO: per-payload current budget per rail; how a payload requests/adds a fused branch; the
payload **connector standard** + pinout.*

## Data / comms options

A payload (or the base's own subsystems) can integrate over any of these:

- **Ethernet** — the robot's LAN (also how the StampPLC reaches Thor, USB-less).
- **WiFi** — on-robot wireless (dashboard/tablet, offboard links).
- **CAN** — the **StampPLC is the CAN master**; the **REV PDH is a node** that *provides
  telemetry*, not a controller.
- **RS485** — from the StampPLC (currently used only for the **battery / BMS**; spare capacity
  for other RS485 devices).
- **USB** — to Thor (mind the [reliability notes](../build/lidar.md); prefer another bus where
  you can).
- **I2C** — the M5 bus is **5 V**; 3.3 V devices need a level converter (see
  [OTOS](../build/otos.md) / [Pinpoint](../build/pinpoint.md) for the pattern).

## The contract

*TODO:* a payload that stays within the current budget on a named rail and speaks one of the
buses above through the standard connector is electrically compatible with no changes to the
base.
