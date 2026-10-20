# Pan/Tilt HMI Module

!!! warning "In development"
    Tilt is characterised and working; pan needs a servo configuration change first. The
    structure, interfaces and commissioning procedure are recorded here; the assembly steps,
    photos, CAD and parts list are not yet. The complete build today is the
    [lower frame](lower-frame.md), steps 1–13. The neck controller's notes live in
    [`firmware/neck_dial/README.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/firmware/neck_dial/README.md).

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The robot's head: a tablet face on a pan/tilt mount, moved by its own neck controller |
    | **Time** | *TODO* |
    | **Difficulty** | Intermediate (servo commissioning, one servo needs reconfiguring before use) |
    | **Prerequisites** | A Z-axis to carry it — [Scissor Lift](scissor-lift.md) or a mast |
    | **Mates to** | Z-axis platform; its own 24 V feed from the drive bus |
    | **Optional?** | Yes — the robot drives without a head |

## What this is

A Samsung Galaxy Tab A11 tablet is the **face**. On its mount it shows the robot's face and
state and plays its sound; lifted off, the same tablet is the operator's dashboard. The mount
is a two-axis pan/tilt, moved by an M5Dial neck controller, so the head can point.

The pan/tilt exists to *point something*. If the only thing pointed is a face, you have an
expressive head. With the tablet's own camera looking out from the face, the head becomes a
gaze platform that can turn toward a subject rather than just emote at one.

Unlike the base, this module is a **payload**: it has its own controller and its own
power, and it talks to the robot over the network rather than through the StamPLC.

## Architecture

```
M5Dial neck controller (ESP32-S3) ── Port A, I²C ── 8Servo (0x25) ─┬─ ch 0: tilt servo
                                                                    └─ ch 1: pan servo
```

The neck controller, an M5Stack **M5Dial**, drives the pan and tilt servos through **its own
8Servo unit** at I²C address `0x25` on its Port A. This is a second 8Servo, separate from the
StamPLC's in the [Electronics Bay](electronics-bay.md), which drives the wheel ESCs. The neck
takes the head's commands over the network and holds its per-robot tilt limits itself. No
rangefinder is fitted to the neck.

!!! danger "Servo current must not come from the Grove cable"
    With nothing on the 8Servo's terminal block, its servo rail is fed from the Grove
    5 V — the controller's own 5 V rail. **Servo current drawn that way can destroy the
    controller's 5 V output.**

    Feed the 8Servo's terminal block instead, on the **HV** pin (9–24 V) rather than
    the 5 V pin. With HV present the unit's internal FETs isolate its servo rail from
    the Grove supply and its onboard buck powers the servos, while the controller supplies
    only a few mA of logic. Feeding the 5 V pin leaves those FETs conducting and
    back-feeds your supply into the controller.

## Commissioning — values discovered per robot

!!! danger "These are per-robot and must be discovered, not copied"
    Servo horn indexing, travel limits and axis direction are **set by how the horn
    happened to be pressed on**. A 25-tooth spline has no index mark, so one tooth of
    difference is 14.4°, and every one of these values differs between builds.
    Copying them will drive a head into its own stop.

**1. Confirm both servos are in positional mode.** These are goBILDA dual-mode servos
and ship either way. A servo in continuous-rotation mode cannot be commanded to an
angle at all — it takes speed, not position, and will keep turning until something
stops it.

Mode is changed either with the goBILDA 3102 programmer, or with
[`firmware/servo_mode_programmer`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/main/firmware/servo_mode_programmer)
— an Arduino Uno sketch that speaks the servo's own configuration protocol. The Uno is
used specifically because it drives the signal line at **5 V**; an attempt from a 3.3 V
GPIO had no effect at all. Run its `PING` first: a reply proves the link before anything
writes EEPROM, and reports the servo's ID.

Tell them apart by commanding a value and watching current: a positional servo arrives
and settles to idle; a continuous one draws steadily and never settles.

!!! warning "Pan has an umbilical"
    Nothing in continuous mode stops at 270°. If pan is not in positional mode, do not
    command it away from centre with the umbilical attached.

**2. Find the travel limits**, one axis at a time, with the horns free. Creep in small
steps and watch servo current: a sharp rise is a hard stop. Back off and record the
value short of it. Then find the home position and confirm it visually.

For reference, one commissioned tilt axis measured:

```
 920 µs   full up      ~90°
1545 µs   horizontal   home
1650 µs   full down    hard stop measured at 1680 µs
7.14 µs per degree     (280° of travel over 500–2500 µs)
```

**Those are that robot's numbers, not yours.** They are printed to show the shape of
the result, not to be copied.

**3. Confirm axis direction** — that increasing pulse tilts the way you expect, and
likewise for pan.

*TODO: replace with the firmware-guided procedure once the `CFG` command set and its
NVS storage exist, so discovered values are stored on the robot rather than kept as
source constants.*

## Camera

The head's camera is the tablet's own screen-side camera, looking out from the face. Its
position and direction on the mount are not yet measured. The neck carries no rangefinder.

## CAD & 3D model

*TODO: Fusion permalink and STL exports.*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = pan-tilt-hmi`), then run `tools/bom.py --inject`.

<!-- BOM-AUTO:build:pan-tilt-hmi -->
**Neck controller**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [M5Stack Dial v1.1 (ESP32-S3)](https://shop.m5stack.com/products/m5stack-dial-v1-1) | `K130-V11` | 1 | The neck controller: takes the head's commands over the network and drives the neck's 8Servo. |
| [M5 8Servo unit](https://docs.m5stack.com/en/unit/8Servos%20Unit) | — | 1 | The neck's own, at I2C 0x25 on the M5Dial neck controller's Port A: channel 0 tilt, channel 1 pan. Separate from the StamPLC's in the electronics bay. |
| [goBILDA 2000 Series Dual Mode Servo (25-2, Torque)](https://www.servocity.com/2000-series-dual-mode-servo-25-2/) | `2000-0025-0002` | 2 | Pan and tilt, on the neck 8Servo's channels 1 and 0. Dual-mode: confirm each is in positional mode before fitting (see Commissioning). |

**Chest indicator light**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [goBILDA RGB Indicator Light (PWM Controlled)](https://www.servocity.com/rgb-indicator-light-pwm-controlled/) | `3118-0808-0002` | 1 | Lights the chest badge from behind in the robot's state colour; the PWM pulse width sets the colour. Fixed with M4 screws through its own holes. |
| 3D-printed chest plate set (face, gear, diffuser, centre mask, light box) | — | 1 | About 59 g of PLA in black, purple, gold, transparent and white; files and print settings in hardware/chest-plate/. |
| M4 x 20 mm socket-head cap screw (ISO 4762) | — | 4 | Through the light box floor and the light into the inserts; snug only. Socket heads, not button heads: the light's cover leaves exactly 7 mm for a head. |
| M4 heat-set insert | — | 4 | In the chest piece; about 5.6 mm hole x 8.1 mm long. Check yours against the CAD parameters. |
| 6 x 2 mm disc magnet (N52 or similar) | — | 8 | Four pairs, poles attracting: they hold the face on, so it lifts off to reach the screws. |
| CA glue | — | 1 | Fixes the magnets and the gear, and tacks the diffuser to the face. |
<!-- /BOM-AUTO:build:pan-tilt-hmi -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md).
You will also want either the **goBILDA 3102 dual-mode servo programmer** or an
**Arduino Uno** running [`firmware/servo_mode_programmer`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/main/firmware/servo_mode_programmer).

## Assembly

*TODO: mount construction, servo horn indexing, tablet mounting, cable routing and the
service loop for the umbilical.*

## Verify — done when…

- [ ] Both servos confirmed in **positional** mode (settle to idle at a commanded angle).
- [ ] 8Servo fed from its terminal block **HV** pin, not the Grove 5 V.
- [ ] Travel limits recorded for both axes, with margin off each measured hard stop.
- [ ] Home position looks right by eye on both axes.
- [ ] Axis directions confirmed — increasing pulse moves the way the code expects.
- [ ] Pan sweeps its full working range without straining the umbilical.
- [ ] The neck answers on the network, and the face shows on the tablet.
