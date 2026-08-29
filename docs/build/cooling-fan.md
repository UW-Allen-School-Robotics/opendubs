# 5 · Cooling Fan Integration

## At a glance

| | |
|---|---|
| **You'll do** | Mount the fan(s), duct/aim airflow over the hot components, wire fan power |
| **Prerequisites** | [Electronics Bay](electronics-bay.md) |

!!! note "Stub — assembly steps coming"
    Install the cooling fan(s) for the electronics / compute and wire their power, so the bay
    stays within temperature under sustained load.

<figure class="amap amap--fan" markdown="1">
  <div class="amap-frame" markdown="1">
--8<-- "amap-map.html"
  </div>
  <figcaption>Where the <strong>Fan panel</strong> sits in the lower frame — see the <a href="../lower-frame/">assembly overview</a>.</figcaption>
</figure>

<div class="embed-facade is-placeholder" style="--embed-aspect: 62%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811ca7102b2532fb949?mode=embed" data-title="Fan integration panel — Fusion 3D viewer">
  <span class="embed-facade__play">▶ Open the interactive 3D model — fan integration panel</span>
</div>

## Assembly

Both this panel and the [charge port](charger-port.md) are **printed panels that slide into
the goRAIL T-slot from the top** — they are not bolted through the frame. Fit them before
the upper frame closes off the top of the rails, or you will have to back a rail off to get
them in.

!!! tip "Slide in from the top"
    The panel's edges are profiled to the 1118-series open goRAIL T-slot. Drop it in from
    the open end at the top of the upright, slide it down to its position, then lock it —
    no drilling, and it can be repositioned later by loosening and sliding.

### Step 1 — Slide the fan panel into the rail

<figure markdown="span">
  ![The printed fan panel slid into the goRAIL T-slot with the axial fan mounted to it](../assets/build/cooling-fan/01-fan-panel-in-rail.webp)
  <figcaption>The white printed panel located in the goRAIL T-slot, with the axial fan already mounted to it. The panel carries the fan — the fan never fastens to the frame directly.</figcaption>
</figure>

!!! tip "Mount the fan to the panel on the bench"
    Fit the fan to the panel before the panel goes into the rail. Both fan screws are easy
    on a desk and awkward once the assembly is inside the frame.

### Step 2 — Set the airflow direction

Axial fans are directional and almost every case has an arrow moulded into the housing for
airflow and another for rotation. Decide **before** you slide the panel down whether this
fan is pulling air in or pushing it out, because reversing it later means pulling the panel
back out of the rail.

!!! warning "Check the arrow, not the blades"
    You cannot reliably tell a fan's direction by looking at the blades. Find the moulded
    arrow on the housing edge, and aim the flow across the electronics that actually get hot
    — see [Electronics Bay](electronics-bay.md).

### Step 3 — Route and land the fan power

<figure markdown="span">
  ![The lower frame with both the fan panel and the charge-port panel fitted](../assets/build/cooling-fan/02-both-panels-in-frame.webp)
  <figcaption>Both panels in context — fan panel on the right-hand rail, charge-port panel on the left. The fan's supply runs back along the rail toward the bay rather than across the frame opening.</figcaption>
</figure>

Run the fan lead along the inside of the rail and back to its supply in the bay, keeping it
clear of the frame opening and anything that moves.

## Verify — done when…

- [ ] Panel fully seated in the T-slot and locked — it should not slide under hand pressure.
- [ ] Fan solidly mounted to the panel; no contact between blades and any cable.
- [ ] Airflow direction confirmed against the housing arrow, aimed at the hot components.
- [ ] Fan lead routed along the rail, not across the frame opening, with no strain at either end.
- [ ] Fan spins up on power and runs without rubbing or rattling.

!!! note "Still to document"
    This module has **no rows in `hardware/parts.csv`** yet, so the page has no generated BOM
    — the fan, its printed panel and the T-slot hardware all need adding, then
    `python3 tools/bom.py --inject`. The fan's supply voltage and where in the bay it lands
    are also not shown in the photos and are not stated here.
