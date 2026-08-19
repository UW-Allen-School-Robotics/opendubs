> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this does NOT describe the current robot. RC is now StampPLC-SBUS (Pixhawk/MAVLink eliminated); power is a 52V LiFePO4 + JBD BMS + three-rail split (no DeWalt/shore). See [history/README.md](README.md) and [../system-overview.md](../system-overview.md).

# Power Architecture — Source Transfer, Shore Power & E-Stop

Status: reflects the current build (June 2026). The **authoritative schematic is the
Figma board (OpenDubsV2)**; this is the prose companion. Control split in
`control-architecture.md`; safety hierarchy in `control-authority.md`.

The power spine: two DeWalt packs → switcher → **shore/battery source-OR (Victron DBC)**
→ ODrive regen clamp → **e-stop contactor** → REV PDH → subsystems. Shore: wall → 24 V
PSU → into the DBC. Logic (Thor/StampLC/Pixhawk) is fed from a **separate mini-PDH**
upstream of the e-stop contactor (so an e-stop cuts actuators, not the brain). The REV
PDH is retained (run out-of-spec on voltage but empirically reliable).

## Use-case framing

- **Case 1 — long-range navigation.** Mast lowers CG, arms stowed and powered down,
  everything on batteries; primary RC-driving scenario. Bus 15–21V; arms off.
- **Case 2 — dexterous manipulation.** Robot works a fixed area for extended periods;
  move to shore power for experimental stability and to give the OpenArms ~24V (a 5S
  DeWalt pack never reaches 24V, so the arms are effectively shore-voltage hardware).
  Mobile base stays available but speed-limited for extra DOF (incl. strafe).

## Decisions locked (this revision)

1. **REV PDH retained (primary distribution).** Rated 18V; run at ~20V for months with
   >24V transients and no failures. Out of spec on voltage but empirically reliable for
   the current drive loads, so it stays for now. The NGX migration is held as a
   documented future option (see "Alternative" below).
2. **Charge off-robot.** Packs removed and charged on OEM DeWalt chargers. Smart Dock
   XT90 is output-only (no balance taps), so no onboard charger.
3. **Shore-preferred source-OR via a Victron Argo Diode Battery Combiner (DBC).**
   Shore PSU = 24V > full pack (~21V), so the DBC's passive highest-voltage-wins diode-OR
   makes shore the priority source with zero-gap; pull the cord → bus sags to pack voltage
   → battery leg picks up. The **Argo Diode BCD 802** (80 A, low-loss Schottky, M6 studs,
   12/24 V) is the chosen OTS part — it **replaces the discrete LM74700/LTC4359 + back-to-
   back-FET ideal-diode board entirely** (no PCB to build; the design ethos is minimal
   fabrication). Switching is passive/automatic. The Auto-Switcher can be replaced by a
   second DBC if its limited specs become a problem.
4. **Reverse-block is inherent in the DBC** (a diode per leg) — 24 V shore can never
   back-feed the ~21 V packs, with no gating logic. **StampLC relay R1 is now unassigned.**
   It was the "battery-leg gate" — the *enable* line for the battery-leg FET + ideal-diode
   controller (a signal-level output, no contactor needed). That controller was deleted
   when the source-OR became the passive Victron DBC (decision #3 / "Resolved" below), which
   has no control input — so R1 has nothing left to actuate, and there is **no** software
   battery-leg isolation in this build. To add it back you'd need to re-introduce the
   switching element: **(a)** an EN-pin ideal-diode controller + FETs in the battery leg
   (R1 → EN, cheapest), or **(b)** a second contactor (R1 → 24 V coil, like R0 drives the
   GIGAVAC) — we have neither today. Consequence of the passive DBC: regen can no longer
   flow into the packs at all → the ODrive Regen Clamp must absorb 100 % of braking regen
   (size its resistor to measured peak autobrake current).
5. **ODrive Regen Clamp for regen/overvoltage (locked part).** See dedicated section.
   Replaces the discrete TL431/comparator/FET/snubber/thermal-cutout design entirely.
6. **Two-layer E-stop.** Software (controller drops actuator power, logged/recoverable) +
   firmware-independent hardware contactor in the motion path. StampPLC relay output can
   drive the contactor coil directly.
7. **Low-rated fusing as intentional torque/current limit.** See "Motors & fusing."
8. **Low-level controller: hybrid Pixhawk 6C + StampPLC** (committed). Pixhawk = pilot
   (RC/IMU/GPS/H-Flow/PWM); StampPLC = plant controller + safety authority; motion
   authority Option 1 (Pixhawk commands, StampPLC gates). See "Controller" section and
   `control-architecture.md`.

## Motors & fusing

Drive + mast motors are goBILDA/ServoCity 5303-2402-0100 (RS-560 brushed, 99.5:1), ×5
(4 drive + 1 mast). At 24V per motor: no-load 1.3A, **current at rated torque 11A**,
**stall 60A** (45A @ 20V; momentary stall damages the motor). Duty-limited to 25% on /
15 min max continuous even at rated torque.

- **Real operating envelope is small.** Each goBILDA 2×80A ESC (2 motors) runs on a **10A
  fuse that has never blown** across months — so each ESC pair lives under ~10A, and the
  whole base operates in a ~20–30A envelope. The 60A stall / 11A rated figures are regimes
  the robot never actually enters.
- **Intentional low fusing is the chosen philosophy.** If the robot is pushing hard against
  something, it is already failing its task; a blown fuse is an acceptable, cheap sacrifice
  that protects the gearbox, motor, and whatever it is pushing. Fuses respond to I²t, so
  brief accel/brake surges pass while *sustained* shove current eventually opens the fuse —
  the fuse naturally discriminates "transient" from "stuck."
- **Set fuse value off measured peak autobrake current**, not stall math. Hard regenerative
  autobraking is the largest supply-side surge and sets the floor; fuse just above measured
  peak to avoid nuisance blows. Use REV per-channel telemetry to measure it.
- **Motion-bus sizing** follows from the low-fuse envelope, not stall: real continuous is
  tens of amps. If currents are held low by fusing, drive could in principle run through a
  smart PDM later — pending a regen-through-high-side-switch check — but with REV retained
  this is moot for now.

## ODrive Regen Clamp (locked)

Purpose-built robotics part; replaces the entire discrete clamp BOM.

- **Auto-detects supply voltage** — sets its own overvoltage trip from the bus it sees, no
  threshold tuning. (Verify the auto-trip lands **below Thor's 28V** on the 24V shore bus.)
- Ships with a **2Ω 50W** resistor; accepts **0.1–10Ω**, up to **80A peak** regen
  dissipation, scalable to **1000W** resistors. Upgrade to a larger 2Ω (100–250W) if
  sustained hard braking warrants — no other change.
- **Ultra-low conduction loss** (~0.5W at 20A). **PT1000 RTD + multi-threshold OTP
  (120–475°C)** = built-in over-temp / stuck-on protection (replaces the Klixon).
- **Contains a series ideal-diode pass element**: passes source current downstream, blocks
  regen backflow, shunts it to the resistor.

### Placement (important — two ideal diodes in the system)

The Regen Clamp must sit **downstream of the OR junction (system bus) and upstream of the
REV PDH + ESCs**. There it clamps regen coming back from the ESCs and its internal diode
helps isolate the regen event from the sources. The **DBC's own per-leg Schottky diodes**
do the back-feed-to-pack protection **upstream** at the OR junction (passive, not gated —
this is not R1's job; see decision #4). The two diode stages are in series, each doing its
own job — they do not fight as long as the Regen Clamp is downstream of the OR node.

**Do NOT** place the Regen Clamp upstream of the OR junction or anywhere its blocking diode
would interrupt the battery sourcing the bus.

### Verify before final wiring

1. **Forward continuous current rating** of the Clamp's pass path ≥ real drive load (the
   80A figure is the regen/dissipation peak, not necessarily the forward pass-through
   rating — confirm separately).
2. **Auto-detected trip voltage < 28V** on the 24V shore bus (Thor's hard ceiling).

## Transfer circuit

Two source legs feed the **Victron Argo Diode Battery Combiner (DBC)** = the OR junction:

- **Battery leg:** two DeWalt packs (each + PWR switch) → goBILDA Smart Docks → Auto-Switcher
  hot-swap (~15–21 V) → DBC input.
- **Shore leg:** shore inlet (AC) → 24 V current-limited PSU → inrush limiter (NTC / active
  soft-start) → DBC input.
- **DBC output** (highest-voltage-wins, reverse-blocking) → ODrive Regen Clamp → e-stop
  contactor → REV PDH. Presence sensing → StampLC **DI3** (shore), **DI4/DI5** (L/R battery)
  — passive, information only.

**Surge strategy:** size the shore PSU for steady-state Case-2 load and current-limit it;
on transients the bus sags below pack voltage, the battery diode opens, and the pack
supplies the surge. Battery is the surge reservoir; PSU stays small. Caveat: speed-limiting
the base bounds steady-state but not stall current, so a coincident hard base-stall on shore
briefly drops the bus to ~21V and loses the "true 24V for arms" guarantee — acceptable;
confirm against OpenArm brownout spec.

## E-stop architecture (two-channel hardware + software)

Per the Figma **SAFETY LOGIC** inset. A **normally-open (NO) main contactor — GIGAVAC
GX11CAB** (sealed DC, 150 A cont / 800 V, **24 Vdc coil**, **SPDT aux contact**) — sits in
the power path (`FROM POWER BUS → CONTACTOR → REV PDH`); its coil must be energized to pass
power, so loss of power / e-stop = power cut (fail-safe).

- **Hardware channel (authority):** **dual-pole NC e-stop buttons in series.** One pole of
  each is wired into the **contactor coil circuit**, in series with **StampLC Relay 0**.
  Press any e-stop → that pole opens → coil drops → contactor opens → PDH dies, independent
  of firmware. ("OPEN ON NO POWER / HW / SW E-STOP.")
- **Sensing channel:** the *other* pole of each e-stop, in series → **StampLC DI0**
  ("DI0 LOW = ESTOP PUSHED" — active-low, fail-safe: a wire break also reads pushed).
- **Software e-stop:** **Relay 0 (NO)** in series with the coil lets the StampLC drop the
  contactor on command / on a sensed condition, independent of the buttons.
- **Weld detection:** the GX11's **SPDT aux contact** (BLUE=NO / WHITE=COM / ORANGE=NC) →
  **StampLC DI6** detects a welded/stuck-closed contactor (commanded-open but aux-still-
  closed ⇒ welded → fault).
- **Logic stays up:** Thor/StampLC/Pixhawk are on the **separate mini-PDH upstream of the
  contactor** — an e-stop cuts actuators but not the brain, so the StampLC keeps sensing/
  logging and the software-e-stop path stays alive.
- **Mast** lead-screw + belt drive assumed non-backdrivable (no separate brake), pending
  final arm-mass design.

Dashboard mapping (`kiosk-dashboard/backend/status.py`): DI0 e-stop btn (flip to active-low
when wired), DI6 contactor weld; R0 e-stop contactor, R1 unassigned (ex-battery-leg gate).

## Controller — hybrid Pixhawk 6C + StampPLC (committed)

Full detail in **`control-architecture.md`**. Summary of the committed decision:

Both devices are on hand and used together, split by **bus protocol and safety role**:

- **Pixhawk 6C = pilot** — RC/SBUS ingest, onboard IMU, GPS, **H-Flow optical flow + ToF**
  (DroneCAN on the Pixhawk CAN bus), and drive/mast ESC **PWM generation**.
- **StampPLC = plant controller + safety authority** — REV PDH proprietary-CAN telemetry,
  Pinpoint deadwheel odometry (I2C), PRISM LED driver (I2C/PWM), **mast limit switches**
  (opto in), **E-stop contactor coil** (relay out; R1 spare — ex-battery-leg gate), INA226/
  LM75 bus sense. 6–36V native (on-bus).

**Motion authority = Option 1 (committed): Pixhawk commands, StampPLC gates.** Pixhawk
PWM is enable-gated by the StampPLC so the StampPLC can force-neutral / cut on limit-switch
trip, E-stop, or fault — keeping the StampPLC the safety authority per `control-authority.md`
(safety > supervisor > autonomy > RC). PWM physically routes through / is enable-gated by
the StampPLC, not straight Pixhawk→ESC.

Why hybrid (not one box): the H-Flow is DroneCAN-only, so it claims the Pixhawk's CAN bus;
ArduPilot/PX4 won't mix CAN protocols on a bus, which exiles the REV PDH's proprietary CAN
to the StampPLC. Each device hosts the buses and I/O it is actually good at.

Links: Pixhawk ↔ StampPLC over MAVLink/UART (mode, enable, faults, setpoints); Thor ↔
Pixhawk over MAVLink (supervisor setpoints + vehicle state); StampPLC → Thor for PDH/
Pinpoint/limit telemetry.

Odometry: Pinpoint deadwheels (StampPLC/I2C) primary short-term; H-Flow optical flow
(Pixhawk/DroneCAN, EKF-fused) being added; intent is to retire the deadwheels once flow
odometry is trusted. EKF fusion topology (Pixhawk vs Thor vs primary/backup) is **open**.

## Verified component facts

- Jetson AGX Thor dev kit: 9–28V, up to 8A (140W) Micro-fit input — spans the bus, no
  buck/boost. At 15V/140W = 9.3A > 8A limit → set LVC ~16–17V (≈3.2–3.4 V/cell).
- goBILDA/ServoCity 2×80A ESC: tolerates 30V; brakes on signal loss; feeds regen back to
  supply. (goBILDA and ServoCity are the same company.)
- goBILDA/ServoCity 5303-2402-0100 motor: 24V, no-load 1.3A, rated-torque 11A, stall 60A.
- REV PDH: 18V rated; retained, run out-of-spec on voltage by empirical track record.
- Step-downs tolerant to 24V; 12V rail removed.
- M5StampPLC: 6–36V input, isolated CA-IS3050G CAN, onboard INA226 + LM75.
- Pixhawk 6C/6X: 16 PWM out, FMU GPIO @3.3V, power module 4.1–5.7V, ArduPilot Rover capable.
- **Victron Argo Diode Battery Combiner BCD 802** (source-OR, chosen): 80 A continuous,
  Schottky (~0.3 V @ low / 0.45 V @ rated), 12/24 V, 2 inputs → 1 output, M6 studs. Passive
  highest-voltage-wins + inherent reverse-block. ("hot surface" — mount on non-flammable
  surface w/ airflow.)
- **GIGAVAC GX11CAB** (e-stop main contactor, chosen): EPIC sealed DC, Form-X SPST-**NO**
  main, **150 A cont @75 °C / 225 A @50 °C, 12–750 Vdc**, M8 power studs. `C` = **24 Vdc
  coil** (internal suppression, ~280 mA, pickup ≤15 V / dropout ≥0.5 V), **SPDT (Form-C) aux
  contact** rated 2 A/24 Vdc (BLUE NO / WHITE COM / ORANGE NC) → DI6 weld sense. Way
  over-spec for the ~30 A bus = bulletproof; the 24 V coil matches the bus-sourced e-stop
  loop. Datasheet: GIGAVAC GX11 series (Sensata).

## Open items

1. **OpenArm current draw + min voltage / brownout** — sizing of the shore PSU and arm
   power path; what a mid-task arm does when the cord is pulled.
2. **Measured peak autobrake current** (REV telemetry) — locks fuse value and the Regen
   Clamp resistor wattage (now carrying 100 % of regen since the DBC blocks regen-to-pack).
3. **Regen Clamp forward pass-through current rating** and **auto-trip voltage < 28V** —
   confirm before final wiring.
4. **Shore PSU wattage** — once steady-state Case-2 load is summed.
5. **Pack topology** (parallel vs A/B) — interacts with surge capability and transfer.

Resolved: source-OR part = Victron DBC BCD 802 (was "battery-leg FET + ideal-diode
controller p/ns"); logic-power tap = separate mini-PDH upstream of the e-stop contactor.

## Sources

- goBILDA Smart Dock 3104-0020-0001 (output-only XT90): https://www.gobilda.com/gobilda-smart-dock-for-20v-battery-dewalt-20v-max-compatible/
- goBILDA/ServoCity 2×80A ESC 3105-0202-0080: https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf
- goBILDA/ServoCity 5303-2402-0100 motor spec: https://www.servocity.com/content/spec_sheets/5303-2402-0100_spec-sheet.pdf
- ODrive Regen Clamp datasheet: https://docs.odriverobotics.com/v/latest/hardware/regen-clamp-datasheet.html
- ODrive Regen Clamp shop: https://shop.odriverobotics.com/products/odrive-regeneration-clamp
- REV PDH (REV-11-1850): https://www.revrobotics.com/rev-11-1850/
- Jetson AGX Thor power input: https://forums.developer.nvidia.com/t/voltage-input-for-nvidia-jetson-agx-thor-development-kit-power-via-microfit-port/369186
- M5Stack StamPLC: https://www.cnx-software.com/2025/03/22/m5stack-stamplc-esp32-s3-plc-opto-isolated-inputs-relay-ouputs-rs485-can-bus/
- Holybro Pixhawk 6C: https://docs.holybro.com/autopilot/pixhawk-6c/technical-specification
- ArduPilot Rover mecanum (no auto-strafe): https://discuss.ardupilot.org/t/rover-with-mecanum-omnidirectional-wheels/54546
- Infinitybox J1939 POWERCELL NGX: https://infinitybox.com/wp-content/uploads/2025/04/POWERCELL-NGX-Standard-J1939-Set-Up-Instructions-V0.6.pdf
- TI LM74700-Q1 ideal diode controller: https://www.ti.com/product/LM74700-Q1
- ADI LTC4359 ideal diode controller: https://www.analog.com/en/products/ltc4359.html
