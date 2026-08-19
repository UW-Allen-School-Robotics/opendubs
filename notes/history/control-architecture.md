> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this does NOT describe the current robot. RC is now StampPLC-SBUS (Pixhawk/MAVLink eliminated); power is a 52V LiFePO4 + JBD BMS + three-rail split (no DeWalt/shore). See [history/README.md](README.md) and [../system-overview.md](../system-overview.md).

# Control Architecture — Hybrid Pixhawk 6C + M5StampPLC

Status: decision recorded, May 2026. Companion: `control-authority.md` (who may command
actuators), `system-overview.md` (Thor/edge split), `power-architecture.md` (power).

The low-level control + I/O job is split across **two** edge devices we already own —
the **Pixhawk 6C** (flight-controller-class pilot) and the **M5StampPLC** (industrial
I/O + safety + proprietary-bus host) — under **Thor AGX** as high-level supervisor. The
split is by **bus protocol and real-time/safety role**, not by device count.

## Why hybrid (not one box)

- The Pixhawk natively does the things that are painful elsewhere: RC/SBUS ingest, onboard
  IMU, GPS, optical-flow/ToF, and servo-PWM generation to ESCs.
- The StampPLC natively does the things the Pixhawk is bad at: 6–36V on-bus operation,
  opto-isolated digital inputs, relay outputs, general I2C device hosting, and **plain CAN
  for the REV PDH's proprietary, reverse-engineered protocol**.
- ArduPilot/PX4 will **not mix CAN protocols on one bus** and are built around DroneCAN.
  The H-Flow is DroneCAN, so it claims the Pixhawk's CAN bus — which firmly exiles the REV
  PDH's proprietary CAN to the StampPLC. This single fact drives the partition.

## Function groups

### Group A — Pixhawk 6C: state estimation + RC + actuator command ("pilot")

| Function | Interface |
|---|---|
| RC in (manual / Case 1) | SBUS (native) |
| IMU | onboard |
| GPS | native serial/DroneCAN (unit on hand) |
| H-Flow optical flow + ToF + IMU | **DroneCAN on CAN1** (DroneCAN-only device) |
| Drive ESC PWM ×2 (R/L), mast ESC PWM | servo-PWM out, **gated by StampPLC** (see authority) |

Pixhawk CAN1 = the DroneCAN sensor bus (H-Flow now; GPS / a DroneCAN power module may join
later). Self-contained, documented, no reverse-engineering. Flow gives low-speed ground
velocity + height fused in the EKF alongside GPS/IMU.

### Group B — M5StampPLC: machine I/O + proprietary buses + power telemetry ("plant controller")

| Function | Interface |
|---|---|
| REV PDH telemetry (proprietary CAN) | plain CAN — only sensible host |
| Pinpoint deadwheel odometry | I2C |
| PRISM RGB LED driver (6–30V) | I2C (or PWM); non-critical lighting |
| Mast limit switches ×2 | opto-isolated digital in |
| Bus voltage / current sense | onboard INA226 / LM75 |
| E-stop contactor coil drive | relay out (see Group C) |
| R1 (ex-battery-leg gate) | unassigned — gate removed with passive DBC (see power-architecture.md #4) |

### Group C — Safety interlock layer (hardware-dominant, spans both)

- **HW E-stop contactor** in the motion path — opens independent of both controllers.
  StampPLC relay drives the coil, **with a hardwired E-stop button in series** so neither
  firmware is in the trip path.
- **ESC brake-on-signal-loss** — inherent to the goBILDA ESCs; an actuator-layer property.
- **Battery-leg reverse-block gate** — asserted by StampPLC; fails to safe (block) on loss.

## Motion authority — Option 1 (committed)

**Pixhawk commands, StampPLC gates.** The Pixhawk generates drive/mast PWM, but that PWM
is **enable-gated by the StampPLC** (pass-through or hardware enable the StampPLC controls).
The StampPLC can force-neutral / cut on a limit-switch trip, E-stop, or fault, keeping it
the **safety authority** as `control-authority.md` requires (precedence: safety interlocks
> supervisor > autonomy > RC).

Rejected alternative (Option 2: Pixhawk sole motion authority, StampPLC monitoring-only) —
would put safety interlocks inside flight firmware (Lua/params), a weaker story for
arms-near-people and contrary to the control-authority model.

Practical consequence: drive/mast PWM must physically route through or be enable-gated by
the StampPLC, not run straight from Pixhawk to ESC.

## Links between devices

- **Pixhawk ↔ StampPLC:** MAVLink over a serial UART (Pixhawk TELEM ↔ StampPLC UART) —
  carries mode, enable/disable, fault state, and Thor's autonomy setpoints down to the
  pilot; carries the safety/enable handshake.
- **Thor ↔ Pixhawk:** MAVLink over the other TELEM/USB — Thor is the supervisor sending
  autonomy setpoints and reading vehicle state.
- **StampPLC → Thor:** PDH / Pinpoint / limit telemetry (directly or relayed via Pixhawk).

## Odometry (two sources, short term)

- **Pinpoint deadwheel odometry** (StampPLC / I2C) — primary in the **short term**.
- **H-Flow optical flow + ToF** (Pixhawk / DroneCAN, fused in EKF) — being added.
- **Direction:** intend to **eliminate the deadwheels down the road** once flow-based
  odometry (plus GPS/IMU EKF) is trusted; keep deadwheels in the interim.
- **Open (EKF):** where the two fuse — on the Pixhawk EKF, on Thor, or one primary / one
  backup — is **not yet decided**. Parked as a sensor-fusion question; not a wiring one.

## Summary table

| Function | Home | Bus / Interface |
|---|---|---|
| RC in, IMU, GPS, H-Flow | Pixhawk 6C | SBUS / onboard / DroneCAN (CAN1) |
| Drive + mast ESC PWM | Pixhawk → gated by StampPLC | servo-PWM |
| REV PDH telemetry | StampPLC | proprietary CAN |
| Pinpoint deadwheel odom | StampPLC | I2C |
| PRISM LED driver | StampPLC | I2C (or PWM) |
| Mast limit switches | StampPLC | opto digital in |
| E-stop contactor coil | StampPLC relay + hardwired button | relay / contactor |
| R1 (ex-battery-leg gate) | StampPLC — unassigned | spare (gate removed w/ passive DBC) |
| Pixhawk ↔ StampPLC | — | MAVLink / UART |
| Thor supervision | Thor ↔ Pixhawk | MAVLink / UART or USB |

## Open items

1. **EKF/odometry fusion topology** (Pixhawk vs Thor vs primary/backup) — undecided.
2. **PWM gating mechanism** — pass-through vs hardware-enable line; pick during build.
3. **Limit-switch / contactor conditioning** confirmed on StampPLC I/O (its strength).
4. Deadwheel retirement criteria once flow odometry is trusted.

## Sources

- Holybro H-Flow (DroneCAN optical flow + ToF + IMU): https://holybro.com/products/h-flow ; https://docs.holybro.com/peripherals/h-flow-dronecan/overview
- ArduPilot raw CAN via Lua scripting: https://github.com/ArduPilot/ardupilot/blob/master/libraries/AP_Scripting/examples/CAN_write.lua
- PX4/ArduPilot CAN = DroneCAN, no protocol mixing: https://docs.px4.io/main/en/can/
- REV PDH CAN is proprietary (library-layer support only): https://docs.wpilib.org/en/stable/docs/software/can-devices/power-distribution-module.html
- Holybro Pixhawk 6C spec: https://docs.holybro.com/autopilot/pixhawk-6c/technical-specification
- M5Stack StamPLC: https://www.cnx-software.com/2025/03/22/m5stack-stamplc-esp32-s3-plc-opto-isolated-inputs-relay-ouputs-rs485-can-bus/
- ServoCity PRISM RGB LED driver (6–30V, I2C/PWM): https://www.servocity.com/prism-rgb-led-driver-6-30v-input-i2c-pwm-control/
