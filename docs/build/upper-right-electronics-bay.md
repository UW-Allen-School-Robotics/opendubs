# Right Upper Electronics Bay

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | Mount the **StampPLC** and a **DIN rail of WAGO connectors** in the right upper bay |
    | **Time** | *TODO* |
    | **Difficulty** | Intermediate (mounting + field wiring) |
    | **Prerequisites** | [Upper Frame Assembly](upper-frame.md) (upper frame built) |
    | **Mates to** | Right side of the upper frame; the e-stop / power / SBUS harnesses |
    | **Part of** | [Upper Frame Assembly](upper-frame.md) — step 2 |

<div class="embed-facade is-placeholder" style="--embed-aspect: 62%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811ec105dcaca0a20d0?mode=embed" data-title="Upper integration — Fusion 3D viewer">
  <span class="embed-facade__play">▶ Open the interactive 3D model — upper integration</span>
</div>

*The right upper electronics bay carries the **StampPLC** (the mobile base's edge controller) and a **DIN rail
of WAGO connectors** that serves as the wiring junction for the upper-frame harnesses. **Click
the model above** to orbit the whole upper integration this bay is part of.*

## What lives in the bay

- **StampPLC** — the mobile base's edge controller (SBUS RC in, relay + isolated-DI safety I/O,
  telemetry).
  It mounts to the DIN rail via a 3D-printed clip. See [StampPLC connections](../stamplc-connections.md)
  for the full pinout and [E-stop system](../e-stop.md) for the safety wiring that lands here.
- **StampPLC PoE module** — clips onto the StampPLC and gives it **PoE Ethernet**. This is the
  link the [StampPLC ↔ Thor transport](../stamplc-ethernet-transport.md) rides on, so the robot
  runs without a USB tether.
- **DIN-rail WAGO connectors** — a row of WAGO connectors on a DIN rail (both come from the one
  assortment in the BOM), used as the **field-wiring junction** for the upper frame: power
  distribution taps, the e-stop coil-chain jumpers, and signal joins. Keeping them on a rail
  makes the joins serviceable and keeps the harness dress clean.

## Wiring

<div style="position: relative; padding-bottom: 60%; height: 0; overflow: hidden; border: 1px solid var(--md-default-fg-color--lightest); border-radius: 0.2rem; margin: 1em 0;">
  <iframe style="position: absolute; inset: 0; width: 100%; height: 100%; border: 0;" src="https://embed.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=83-3575&embed-host=opendubs-docs" allowfullscreen loading="lazy" title="OpenDubs right upper electronics bay wiring (Figma)"></iframe>
</div>

- **Diagram source (Figma):** [OpenDubs board — right upper electronics bay](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=83-3575) — anyone with the link can view and export it.

## Mounting

The DIN rail runs along the **right side of the upper frame**; the StampPLC and the WAGO carriers
all snap to it, so the bay is built up on the bench and dropped in as a unit.

1. Cut the **35 mm top-hat DIN rail** to length and fasten it to the right upper frame rail.
2. Clip the **StampPLC** to the rail on its 3D-printed DIN mount.
3. Snap the **WAGO mounting carriers** onto the rail and seat the WAGO connectors.
4. Land the upper-frame harnesses into the WAGOs and the StampPLC terminals (see *Wiring* above).

## CAD & 3D model

- **View in browser (Fusion):** [Upper integration (whole assembly)](https://a360.co/4zEiFGw) —
  this bay is a part of it.
- **Download:** *TODO — export the DIN-rail + StampPLC-clip STLs into `hardware/`.*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = upper-right-electronics-bay`), then run
    `tools/bom.py --inject`.

<!-- BOM-AUTO:build:upper-right-electronics-bay -->
**Controller**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [M5Stack StamPLC controller](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/K141/26459381) | `K141` | 1 | The mobile base's edge controller. Available from many sources; DigiKey (shown) stocks well for US-based teams. |
| [M5Stack StamPLC PoE module](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/A165/28968801) | `A165` | 1 | Adds PoE Ethernet to the StampPLC — the StampPLC<->Thor transport rides this. Many sources; DigiKey stocks well for US teams. |

**Mounting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| StampPLC DIN-rail clip (3D-printed) | — | 1 | Clips the StampPLC to the DIN rail; STL in hardware/ TBD. |

**Wiring**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [WAGO DIN-rail assortment (rail + rail-mount connectors)](https://a.co/d/07yKPglw) | — | 1 | Includes the DIN rail AND the WAGO connectors — the bay's field-wiring junction. |
<!-- /BOM-AUTO:build:upper-right-electronics-bay -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools--consumables).

## Assembly

!!! note "Photos pending"
    Drop step photos into `docs/assets/build/upper-right-electronics-bay/`, then add numbered
    steps here.

## Verify — done when…

- [ ] DIN rail solidly fastened to the right upper frame.
- [ ] StampPLC clipped down and its harnesses landed; powers up and reports telemetry.
- [ ] WAGO carriers seated; all field joins made and dressed.
- [ ] *TODO*
