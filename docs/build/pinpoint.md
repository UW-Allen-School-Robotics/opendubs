# Pinpoint Odometry

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The Pinpoint + 2 pods installed in the 3D-printed housing, rear-mounted via thumb screws |
    | **Time** | *TODO* |
    | **Difficulty** | *TODO* |
    | **Prerequisites** | [Base Frame](base-frame.md); riv-nuts set in the rail ([step 2 · Rivnut Installation](rivnuts.md)) |
    | **Mates to** | Rear rail (exterior); I2C out to the [Electronics Bay](electronics-bay.md) |
    | **Optional?** | Yes — one of several odometry sources |

<div class="embed-facade is-placeholder" style="--embed-aspect: 62%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b6028114c8db9bea74064c0?mode=embed" data-title="Pinpoint odometry module — Fusion 3D viewer">
  <span class="embed-facade__play">▶ Open the interactive 3D model — Pinpoint module</span>
</div>

!!! info "Optional module — one of several odometry options"
    Like the [OTOS sensor](otos.md), the Pinpoint dead-wheel system is **optional** — one of
    several spatial / odometry sources. Build it if you want wheel-independent odometry.
    **Install timing:** the dead-wheel pods ride against the floor, so this goes on **toward the
    end** of the build, once the frame is up on its wheels.

The **Pinpoint** system — the goBILDA
[4-Bar Odometry Pack](https://www.servocity.com/4-bar-odometry-pack-2-pods-1-pinpoint-computer/)
(Pinpoint computer + IMU + two dead-wheel pods) — gives X / Y / heading from two dead
wheels. This module is a **self-contained housing that bolts to the exterior (rear) of the
base frame**, so it can be added or removed as a unit.

!!! note "No level-shifter in this module"
    The Pinpoint I2C is **3.3 V**. Unlike the OTOS build, the 3.3 V ↔ 5 V step-down for this
    sensor is **handled centrally in the [Electronics Bay](electronics-bay.md)** — so there's
    no level converter in this housing, just the sensor wiring out to the bay.

## Mounting — riv-nuts in the rail

The module hand-mounts to the rear rail with **3D-printed M10 thumb screws** that thread into
**M10 riv-nuts set into the rail's 14 mm holes**.

!!! note "Riv-nuts are installed earlier, in step 2"
    Setting the riv-nuts into the base-frame rail is done on the bare frame in
    **[step 2 · Rivnut Installation](rivnuts.md)** — not part of this housing build. By the time
    you mount this module the inserts are already there; you just need the **M10 thumb screws**.
    (For the riv-nut install itself, use **either** the
    [manual riv-nut tool](https://a.co/d/0am8KbEV) **or** a
    [drill-driven tool](https://a.co/d/07xBUxUD).)

## CAD & 3D model

- **View in browser (Fusion):** *TODO — add permalink*
- **Download:** *TODO — export STEP + the housing / thumb-screw STLs into `hardware/`*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = pinpoint`), then run `tools/bom.py --inject`.

<!-- BOM-AUTO:build:pinpoint -->
**Odometry computer**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [4-Bar Odometry Pack — Pinpoint computer + 2 dead-wheel pods](https://www.servocity.com/4-bar-odometry-pack-2-pods-1-pinpoint-computer/) | `3203-3110-0002` | 1 | Pinpoint (IMU + odometry) + 2 pods; I2C at 3.3 V — the step-down lives in the electronics bay, NOT this module. Same pack on the Core BOM. |

**Mounting hardware**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| 3D-printed module housing | — | 1 | Holds the Pinpoint computer + pods; STLs in hardware/ TBD |
| M10 thumb screw (3D-printed) | — | 2 | Hand-tightens the module to the rear rail; confirm qty |
| [M10 riv-nut](https://a.co/d/07JbuGAl) | — | 2 | Set into the rail's 14 mm holes; rivnut install is a FOLLOW-ON base-frame step; confirm qty |

**Mounting tools**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Riv-nut tool (manual)](https://a.co/d/0am8KbEV) | — | 1 | Sets the M10 riv-nuts — CHOOSE this OR the drill attachment |
| [Riv-nut drill attachment](https://a.co/d/09zmlaXE) | — | 1 | Drill-driven alternative to the manual riv-nut tool |
<!-- /BOM-AUTO:build:pinpoint -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools--consumables) —
**plus a riv-nut tool** for the mount (manual or drill attachment; see the *Mounting tools*
group in the BOM above).

## Assembly

!!! note "Photos pending"
    Drop step photos into `docs/assets/build/pinpoint/`, then add numbered steps here.

Outline (fill in once photos are in):

1. Print the housing and install the **Pinpoint computer + two pods** into it.
2. Set the **M10 riv-nuts** into the rear rail's 14 mm holes (follow-on base-frame step).
3. Hand-mount the housing to the rear rail with the **M10 thumb screws**.
4. Route the I2C to the electronics bay (step-down is handled there).

## Commissioning — the device forgets everything

!!! danger "The Pinpoint loses its configuration on every power cycle"
    Straight from goBILDA's user guide: *"Device Configurations are lost on device power
    cycles, so your I²C Controller device should send the config information over I²C once
    every time the code starts."*

    The StampPLC does that for you — it applies the stored configuration at boot, and again
    whenever the device reappears on the bus. **But an unconfigured Pinpoint does not fail
    visibly.** It returns smooth, plausible, wrong numbers. `PPCAL SHOW` reports
    `NOT COMMISSIONED` until someone does the steps below; treat pose as untrustworthy
    until it doesn't.

These are physical facts about *your* robot — where the pods are bolted and which way round
they ended up — so they are stored on the robot, never in the repo.

**Do it in this order.** Direction comes first because getting it wrong invalidates
everything measured afterwards.

**1. Encoder directions.** Push the robot **forward** by hand and watch `ex` in the `[ODO]`
telemetry: it must *increase*. Push the robot **left**: `ey` must *increase*.

```
PPCAL DIRX FWD     (or REV if X counted backwards)
PPCAL DIRY FWD     (or REV)
```

If neither value moves, that pod or its cable is the problem, not the direction.

**2. Encoder resolution** — ticks per mm, which is your encoder's CPR divided by the tracking
wheel's circumference:

```
PPCAL POD 4BAR       goBILDA 4-bar pod: 2000 CPR / 100.53 mm = 19.894
PPCAL POD SWINGARM   goBILDA swingarm pod:                     13.263
PPCAL TICKS <n>      anything else
```

**3. Pod offsets.** Each offset is the **perpendicular** distance from a pod to the tracking
point — the point on the robot whose position the Pinpoint reports. Distance *along* a pod's
own direction of travel doesn't matter.

| | Measures | Positive when |
|---|---|---|
| `x` | how far **sideways** the X (forward) pod sits | pod is **left** of the tracking point |
| `y` | how far **forwards** the Y (strafe) pod sits | pod is **forward** of the tracking point |

```
PPCAL OFFSETS <x_mm> <y_mm>
```

!!! warning "The usual mistake is a sign, not a measurement"
    goBILDA call this out directly: the most common error is an offset that is positive when
    it should be negative. Both offsets are frequently negative on a normal build.

**4. Save and check.**

```
PPCAL SAVE
```

Then **spin the robot in place around the tracking point**. `x` and `y` should barely move —
drift over ~100 mm during a full rotation means an offset is wrong, and a *sign* is the first
thing to suspect. Then drive a measured straight line and compare against a tape measure; a
large disagreement is the ticks-per-mm.

`PPCAL RESET` zeroes the pose and re-measures the IMU's zero offset — the robot must be
**stationary**, since a bad zero offset shows up as heading drifting while it sits still.

## Verify — done when…

- [ ] Both pods held so each dead wheel keeps constant floor contact (preload set).
- [ ] Housing solid on the rear rail; thumb screws seated in the riv-nuts.
- [ ] Pinpoint enumerates on the I2C bus (after the electronics-bay step-down).
- [ ] Status LED is **green** — purple/blue/orange means a pod isn't detected.
- [ ] Pushing the robot forward increases `ex`; pushing it left increases `ey`.
- [ ] `PPCAL SHOW` reports **commissioned** and **live**.
- [ ] Spinning in place leaves `x`/`y` nearly unchanged.
- [ ] A measured straight-line drive matches a tape measure.
- [ ] Heading holds steady while the robot sits still (no IMU drift).
