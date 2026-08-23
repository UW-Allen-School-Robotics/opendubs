# 2 · OTOS Sensor

<div class="embed-facade" markdown="1" style="--embed-aspect: 78%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811c1903895fbcdd3cf?mode=embed" data-title="OTOS module — Fusion 3D viewer">
![OTOS sensor module — click to open the interactive Fusion 3D viewer](../assets/build/otos/hero.png)
<span class="embed-facade__play">▶ Open the interactive 3D model</span>
</div>

*The OTOS module — **click to orbit/zoom the live Fusion model**. The sensor and Adafruit level
converter sit in a printed enclosure that holds the sensor at its 10 mm ride height above the floor.*

!!! info "Optional module — one of several odometry options"
    The OTOS is **optional**. It's one of several spatial / odometry sources for the robot
    (alongside the [Pinpoint dead-wheel system](pinpoint.md), and others) — build it if you
    want optical floor-tracking odometry, or skip it.

The **OTOS** — a [SparkFun Optical Tracking Odometry Sensor](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html)
(PAA5160E1) — reports X / Y / heading by watching the floor. It mounts at the sensor's
recommended **10 mm** height above the ground plane.

!!! warning "3.3 V sensor on a 5 V bus — level-shift required"
    The OTOS runs its I2C at **3.3 V**, but the **M5 (StampPLC) I2C bus is 5 V**. Do **not**
    wire it straight in. This build uses an
    [Adafruit logic level converter](https://www.adafruit.com/product/5637) to shift
    3.3 V ↔ 5 V, and converts the SparkFun/Adafruit **Qwiic** connector over to the **M5
    Grove** connector on the bus side.

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The OTOS mounted at 10 mm, level-shifted and adapted to the M5 Grove bus |
    | **Time** | *TODO* |
    | **Difficulty** | Beginner–intermediate (a little wiring) |
    | **Prerequisites** | [Base Frame](base-frame.md) |
    | **Mates to** | Frame underside; M5 I2C (Grove) |
    | **Optional?** | Yes — one of several odometry sources |

## CAD & 3D model

- **View in browser (Fusion):** <https://a360.co/3UyyBKq> — or click the hero above to load it inline.
- **Download:** *TODO — export STEP + the enclosure STLs into `hardware/`*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = otos`), then run `tools/bom.py --inject`. WAGOs are
    listed as the default join method — you can solder instead (see the tools note).

<!-- BOM-AUTO:build:otos -->
**Sensor & level shifting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [SparkFun Optical Tracking Odometry Sensor (OTOS)](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html) | — | 1 | PAA5160E1, Qwiic; 3.3 V — needs level-shifting to the 5 V M5 I2C bus |
| [Adafruit logic level converter](https://www.adafruit.com/product/5637) | `Adafruit 5637` | 1 | Shifts OTOS 3.3 V I2C up to the 5 V M5 bus |

**Enclosure & mounting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| 3D-printed enclosure set | — | 1 | A few small printed parts; STLs in hardware/ TBD |
| [M4 x 55 mm socket-head screw](https://www.amazon.com/iExcell-Stainless-Socket-Screws-Wrench/dp/B089NGGLZQ) | — | 4 | Long standoff screws for the 10 mm sensor mount; confirm qty |

**Wiring**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [WAGO lever connectors](https://a.co/d/09VO6gwP) | — | a few | Qwiic<->Grove and I2C/power joins — or solder instead |
<!-- /BOM-AUTO:build:otos -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools--consumables).
If you **solder** the joins instead of using WAGOs, add a soldering iron + heat-shrink.

## Assembly

The wiring harness — the 5 V M5 Grove cable from the PaHub, joined through WAGOs to the
[Adafruit logic level converter](https://www.adafruit.com/product/5637), then over Qwiic to the OTOS:

<figure markdown="span">
  ![OTOS wiring harness — M5 Grove cable from the PaHub, four WAGOs, the Adafruit level converter, then the OTOS](../assets/diagrams/otos-wiring-harness.png)
  <figcaption>The PaHub Grove cable (SCL / SDA / 5 V / GND) joins through four WAGOs to the Adafruit
  logic level converter; its 3.3 V side runs over Qwiic to the OTOS. The level converter is what
  keeps the 5 V bus off the 3.3 V sensor.</figcaption>
</figure>

!!! note "Photos pending"
    Drop step photos into `docs/assets/build/otos/`, then add numbered steps here.

Outline (fill in once photos are in):

1. Print the enclosure set and mount the OTOS at **10 mm** above the ground plane (the 55 mm
   M4 standoff screws set the height).
2. Wire the OTOS **through the level converter** — 3.3 V side to the sensor, 5 V side to the
   M5 bus. **Never** connect the OTOS directly to the 5 V bus.
3. Adapt **Qwiic → M5 Grove** on the bus side (WAGOs or solder).
4. Confirm the sensor enumerates on the M5 I2C bus.

## Verify — done when…

- [ ] Sensor sits flat at ~10 mm, clear view of the floor.
- [ ] Level converter in-line (no direct 3.3 V→5 V connection).
- [ ] OTOS shows up on the I2C scan.
- [ ] *TODO*
