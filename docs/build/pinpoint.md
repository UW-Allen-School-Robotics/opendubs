# 3 · Pinpoint Odometry

<figure markdown="span">
  ![Pinpoint odometry module — placeholder](../assets/build/placeholder-hero.svg)
  <figcaption><em>Hero — replace with a CAD render or completed-module photo.</em></figcaption>
</figure>

!!! info "Optional module — one of several odometry options"
    Like the [OTOS sensor](otos.md), the Pinpoint dead-wheel system is **optional** — one of
    several spatial / odometry sources. Build it if you want wheel-independent odometry.

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

!!! note "Riv-nuts are installed in a follow-on step"
    Setting the riv-nuts into the base-frame rail is a **follow-on base-frame step**, not part
    of this housing build — but you need the riv-nuts and a **riv-nut tool** on hand. Choose
    **either** the [manual riv-nut tool](https://a.co/d/0am8KbEV) **or** the
    [drill attachment](https://a.co/d/09zmlaXE).

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The Pinpoint + 2 pods installed in the 3D-printed housing, rear-mounted via thumb screws |
    | **Time** | *TODO* |
    | **Difficulty** | *TODO* |
    | **Prerequisites** | [Base Frame](base-frame.md); riv-nuts set in the rail (follow-on step) |
    | **Mates to** | Rear rail (exterior); I2C out to the [Electronics Bay](electronics-bay.md) |
    | **Optional?** | Yes — one of several odometry sources |

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

## Verify — done when…

- [ ] Both pods held so each dead wheel keeps constant floor contact (preload set).
- [ ] Housing solid on the rear rail; thumb screws seated in the riv-nuts.
- [ ] Pinpoint enumerates on the I2C bus (after the electronics-bay step-down).
- [ ] *TODO*
