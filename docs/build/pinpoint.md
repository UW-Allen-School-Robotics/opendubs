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

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools-consumables) —
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

    The StamPLC does that for you — it applies the stored configuration at boot, and again
    whenever the device reappears on the bus. **But an unconfigured Pinpoint does not fail
    visibly.** It returns smooth, plausible, wrong numbers. `PPCAL SHOW` reports
    `NOT COMMISSIONED` until someone does the steps below; treat pose as untrustworthy
    until it doesn't.

These are physical facts about *your* robot: where the pods are bolted and which way round
they ended up. The StamPLC stores them in its own memory, where they survive a firmware update;
they are never in the repo. The procedure below follows goBILDA's
[Pinpoint user guide](https://www.gobilda.com/content/user_manuals/3110-0002-0001%20User%20Guide.pdf)
(part 3110-0002-0001), with the StamPLC's `PPCAL` commands in place of goBILDA's FTC driver.

**Do it in this order.** Direction comes first because getting it wrong invalidates
everything measured afterwards.

**0. Check the status LED.** It should be **green** before you start.

| LED | Means | Do |
|---|---|---|
| Green | Ready, working normally | — |
| Red | Starting up after a power cycle, or measuring the IMU's zero offset | Wait a moment. If it stays red, check that the Pinpoint's reset button isn't stuck down |
| Purple | No pods detected | Check both pod cables |
| Blue | The X pod isn't detected | Swap the two pod cables: if the colour changes to orange, the cable or pod moved with it |
| Orange | The Y pod isn't detected | As for blue |

**1. Encoder directions.** Push the robot **forward** by hand, without turning it, and watch
`ex` in the `[ODO]` telemetry: it must *increase*. Push the robot **left**: `ey` must
*increase*.

```
PPCAL DIRX FWD     (or REV if X counted backwards)
PPCAL DIRY FWD     (or REV)
```

If neither value moves, that pod or its cable is the problem, not the direction. If pushing
forward moves `ey` more than `ex`, the pod plugged into the X port is the sideways one: swap
the cables.

**2. Encoder resolution** — ticks per mm, which is your encoder's CPR divided by the tracking
wheel's circumference:

```
PPCAL POD 4BAR       goBILDA 4-bar pod: 2000 CPR / 100.53 mm = 19.894
PPCAL POD SWINGARM   goBILDA swingarm pod:                     13.263
PPCAL TICKS <n>      anything else
```

**3. Pod offsets.** Each offset is the **perpendicular** distance from a pod to the tracking
point — the point on the robot whose position the Pinpoint reports. Distance *along* a pod's
own direction of travel doesn't matter. The tracking point can be anywhere; the centre of the
robot is the usual choice.

| | Measures | Positive when |
|---|---|---|
| `x` | how far **sideways** the X (forward-tracking) pod sits from the tracking point | the pod is **left** of it |
| `y` | how far **forwards** the Y (sideways-tracking) pod sits from the tracking point | the pod is **forward** of it |

```
PPCAL OFFSETS <x_mm> <y_mm>
```

!!! warning "The usual mistake is a sign, not a measurement"
    goBILDA call this out directly: the most common error is an offset that is positive when
    it should be negative. In goBILDA's own example robot, with both pods at the back and the X
    pod right of centre, both offsets are negative.

**4. Save.**

```
PPCAL SAVE
```

**5. Check it, with goBILDA's tests.**

1. **Still.** With the robot untouched, `x`, `y` and `h` should not move. If they drift,
   `PPCAL RESET` with the robot stationary: it zeroes the pose and re-measures the IMU's zero
   offset, and a bad zero offset shows up as heading drifting while the robot sits still.
2. **Spin in place.** Turn the robot around the tracking point without sliding it. `x` and `y`
   should stay within about **100 mm**. More means an offset is wrong, and a *sign* is the first
   thing to suspect.
3. **One turn.** Turn the robot one full turn **counter-clockwise**. `h` (radians) should rise by
   very nearly one positive turn: 6.28.
4. **Tape measure.** Push the robot a measured distance and compare it with what the Pinpoint
   reports. A large difference means the ticks per mm is wrong.
5. **Back to the start.** `PPCAL RESET`, drive the robot around, and bring it back to where it
   started. The pose should read within about **10 mm** of zero. More means something above is
   wrong.

**Leave the yaw scalar alone.** Each Pinpoint's heading scale is calibrated at the factory, and
goBILDA say tuning it is not normally needed. The StamPLC never changes it.

A slight heading wobble is normal; the IMU notices someone walking past. Steady drift of more
than about 1° a minute with the robot still, after a `PPCAL RESET`, is a fault to raise with
goBILDA.

!!! note "Recording your robot's values"
    Write down the values you set, so they can be restored if the StamPLC is ever replaced. The
    project keeps its own robots' values, and how each was established, in
    [`notes/per-robot-calibration.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/notes/per-robot-calibration.md).
    They are a record of those robots, not values to copy: measure your own.

## Verify — done when…

- [ ] Both pods held so each dead wheel keeps constant floor contact (preload set).
- [ ] Housing solid on the rear rail; thumb screws seated in the riv-nuts.
- [ ] Pinpoint enumerates on the I2C bus (after the electronics-bay step-down).
- [ ] Status LED is **green** — purple/blue/orange means a pod isn't detected.
- [ ] Pushing the robot forward increases `ex`; pushing it left increases `ey`.
- [ ] `PPCAL SHOW` reports **commissioned** and **live**.
- [ ] Spinning in place keeps `x`/`y` within about 100 mm.
- [ ] One counter-clockwise turn reads one positive turn in `h`.
- [ ] A measured straight-line push matches a tape measure.
- [ ] A drive around and back to the start reads within about 10 mm of zero.
- [ ] Heading holds steady while the robot sits still (no IMU drift).
