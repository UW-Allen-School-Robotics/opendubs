# Pan/Tilt HMI Module

!!! warning "In development"
    Tilt is characterised and working; pan needs a servo configuration change first. The
    structure, interfaces and commissioning procedure are recorded here; the assembly steps,
    photos, CAD and parts list are not yet. The complete build today is the
    [lower frame](lower-frame.md), steps 1–13. The working notes live in
    [`firmware/tab5_pantilt/README.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/firmware/tab5_pantilt/README.md).

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The robot's head: a touchscreen face on a pan/tilt mount, with a rangefinder and a camera |
    | **Time** | *TODO* |
    | **Difficulty** | Intermediate (servo commissioning, one servo needs reconfiguring before use) |
    | **Prerequisites** | A Z-axis to carry it — [Scissor Lift](scissor-lift.md) or a mast |
    | **Mates to** | Z-axis platform; its own 24 V feed from the drive bus |
    | **Optional?** | Yes — the robot drives without a head |

## What this is

An M5Stack Tab5 (ESP32-P4) is the **face**: a touchscreen that shows robot state and
serves as the local HMI. It sits on a two-axis pan/tilt mount so the head can point,
with a time-of-flight rangefinder and the Tab5's own camera behind it.

The pan/tilt exists to *point something*. If the only thing pointed is a face, you have
an expressive head; put the camera behind it and the head becomes a gaze platform — the
ToF gives range, the camera gives bearing, and together the head can track a subject
rather than just emote at one.

Unlike the base, this module is a **payload**: it has its own controller and its own
power, and it talks to the robot over the network rather than through the StamPLC.

## Architecture

```
Tab5 (ESP32-P4)  ── Grove I²C ── PaHub ─┬─ 8Servo ── pan servo
                                        │           tilt servo
                                        └─ VL53L0X rangefinder
```

The Tab5 runs the face, drives the servos and reads the sensors. Everything in this
module sits on **one power domain** fed from the drive bus, and the only link to the
rest of the robot is wireless — so there is no logic/drive ground crossing here, and
none of the isolation the base needs applies.

!!! danger "Servo current must not come from the Grove cable"
    With nothing on the 8Servo's terminal block, its servo rail is fed from the Grove
    5 V — which is the Tab5's USB rail. **Running servos that way destroyed a Tab5's
    peripheral 5 V output during bring-up.**

    Feed the 8Servo's terminal block instead, on the **HV** pin (9–24 V) rather than
    the 5 V pin. With HV present the unit's internal FETs isolate its servo rail from
    the Grove supply and its onboard buck powers the servos, while the Tab5 supplies
    only a few mA of logic. Feeding the 5 V pin leaves those FETs conducting and
    back-feeds your supply into the Tab5.

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

The Tab5 carries an SC2356 2 MP MIPI-CSI camera. It is **not** reachable from the
Arduino framework — CSI support for the ESP32-P4 is still an open upstream request.
Reaching it needs a dual-framework build (`framework = arduino, espidf`), which has
been proven to compile with the camera driver available while Arduino and M5Unified
keep working. The sensor driver itself is not yet written.

## CAD & 3D model

*TODO: Fusion permalink and STL exports.*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = pan-tilt-hmi`), then run `tools/bom.py --inject`.

<!-- BOM-AUTO:build:pan-tilt-hmi -->
_(no parts for module 'pan-tilt-hmi')_
<!-- /BOM-AUTO:build:pan-tilt-hmi -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md).
You will also want either the **goBILDA 3102 dual-mode servo programmer** or an
**Arduino Uno** running [`firmware/servo_mode_programmer`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/main/firmware/servo_mode_programmer).

## Assembly

*TODO: mount construction, servo horn indexing, Tab5 mounting, ToF aim, cable routing
and the service loop for the umbilical.*

## Verify — done when…

- [ ] Both servos confirmed in **positional** mode (settle to idle at a commanded angle).
- [ ] 8Servo fed from its terminal block **HV** pin, not the Grove 5 V.
- [ ] Travel limits recorded for both axes, with margin off each measured hard stop.
- [ ] Home position looks right by eye on both axes.
- [ ] Axis directions confirmed — increasing pulse moves the way the code expects.
- [ ] Pan sweeps its full working range without straining the umbilical.
- [ ] ToF reports plausible distance; face renders and the touchscreen responds.
