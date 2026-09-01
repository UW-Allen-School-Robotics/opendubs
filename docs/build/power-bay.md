# 6 · Power Bay

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The drive-bus distribution (REV PDH *or* fuse block) mounted in the bay |
    | **Time** | *TODO* |
    | **Difficulty** | Beginner |
    | **Prerequisites** | [Base Frame](base-frame.md) |
    | **Mates to** | Frame; DC/DC input (from outside); e-stop contactor |

<figure markdown="span">
  ![OpenDubs power bay — CAD render](../assets/build/power-bay/hero.png)
</figure>

The power bay is **simple**: it holds the **drive-bus distribution** and nothing else. The
**24 V 60 A DC/DC converter** feeds into the bay from outside, and everything in the bay sits
**behind the e-stop contactor** — so the whole bus de-energizes on e-stop.

<figure class="amap amap--power-bay" markdown="1">
  <div class="amap-frame" markdown="1">
--8<-- "amap-map.html"
  </div>
  <figcaption>Where the <strong>Power Bay</strong> sits in the lower frame — see the <a href="../lower-frame/">assembly overview</a>.</figcaption>
</figure>

!!! danger "Live power — wire it de-energized"
    Build and wire the power bay with **the DC/DC input disconnected / bus de-energized**.
    Everything here is on the **drive-24 V rail behind the e-stop contactor** (see the
    contactor / e-stop wiring on the [Hardware BOM](../hardware-bom.md)).

## Distribution — REV hub *or* a fuse block

!!! note "We use the REV PDH because we already owned it"
    This build uses the [REV Power Distribution Hub](https://www.revrobotics.com/rev-11-1850/)
    (`REV-11-1850`) mainly because it was **purchased earlier in the project**. It has a CAN
    bus and reports potentially-useful telemetry — but it's arguably **overkill** for simple
    distribution. A plain **[fuse block](https://www.amazon.com/dp/B0FLJV9BDD)** is perfectly
    acceptable. We provide **3D-printed mounting parts for either**, so pick whichever you have.

## CAD & 3D model

- **View in browser (Fusion):** *TODO — add permalink*
- **Download:** *TODO — export STEP + the mounting STLs (both PDH and fuse-block variants) into `hardware/`*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = power-bay`), then run `tools/bom.py --inject`. The
    distribution is a **choose-one** (REV PDH or fuse block); the mounting parts fit either.

<!-- BOM-AUTO:build:power-bay -->
**Distribution — choose one**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [REV Power Distribution Hub (PDH)](https://www.revrobotics.com/rev-11-1850/) | `REV-11-1850` | 1 | Used because we bought it earlier in the project; CAN bus + useful telemetry, but potentially overkill. CHOOSE this OR the fuse block. |
| [Fuse block (blade fuses)](https://www.amazon.com/dp/B0FLJV9BDD) | — | 1 | Simpler, perfectly acceptable alternative to the REV PDH |

**Power input**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| 24 V 60 A DC/DC converter | — | 1 | Mounted OUTSIDE the bay, feeds in; the bay/bus sits behind the e-stop contactor |

**Mounting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| 3D-printed mounting parts | — | 2 | Mount EITHER the REV PDH or the fuse block; STLs in hardware/ TBD |

**Wiring tools**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Wire crimper](https://a.co/d/0dhX4RmB) | — | 1 | For battery connections; also if you crimp instead of WAGO/solder |

**Wiring**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Crimp connector assortment (butt joints + ring terminals)](https://a.co/d/08GtC4Ql) | — | 1 | Butt joints + ring connectors |
<!-- /BOM-AUTO:build:power-bay -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools--consumables) —
**plus a crimper** for the battery / high-current connections (see *Wiring tools* above).

## Assembly

!!! note "Photos pending"
    Drop step photos into `docs/assets/build/power-bay/`, then add numbered steps here.

Outline (fill in once photos are in):

1. Mount the REV PDH or fuse block to its 3D-printed carrier.
2. Mount the carrier into the power bay.
3. Wire the 24 V DC/DC input to the distribution.

## Verify — done when…

- [ ] Distribution block solidly mounted.
- [ ] Input power wired correctly and secure.
- [ ] Bus fully **de-energizes on e-stop** (contactor drops) — verify before first energize.
