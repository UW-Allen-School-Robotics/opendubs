# Power & data interface

*(Draft — the contract for what a payload can draw and how it talks to the robot.)*

## Power rails a payload can tap

- **Drive-24 V** — behind the e-stop contactor; de-energizes on e-stop. For actuators/motors.
- **Logic-24 V** — always-on, upstream of the contactor. For compute/sensors that must survive
  an e-stop.
- **Fused branch circuits** — from the REV Mini Power Module (or a fuse block); see the
  [Electronics Bay](../build/electronics-bay.md) and [Power Bay](../build/power-bay.md).

*TODO: a per-payload current budget, and how a payload requests/adds a fused branch.*

## Data / comms options

A payload can integrate over any of the buses the robot already speaks:

- **Ethernet** — the robot's LAN (also how the StampPLC talks to Thor, USB-less).
- **CAN** — via the REV PDH (telemetry / control).
- **USB** — to Thor (mind the [reliability notes](../build/lidar.md); prefer other buses where
  possible).
- **I2C** — the M5 bus is **5 V**; 3.3 V devices need a level converter (see
  [OTOS](../build/otos.md) / [Pinpoint](../build/pinpoint.md) for the pattern).

*TODO: the payload **connector standard** (power + data), and a pinout.*

## The contract

*TODO:* if a payload stays within the current budget on a named rail and speaks one of the
buses above through the standard connector, it's electrically compatible — no changes to the
base.
