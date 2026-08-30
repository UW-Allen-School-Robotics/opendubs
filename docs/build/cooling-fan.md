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
the goRAIL T-slot from the top** — they are not bolted through the frame. Each panel is
captured between two vertical goRAIL uprights and locked with corner brackets, so it can be
slid along the rail to reposition and taken out again without drilling anything.

!!! tip "Fit them before the upper frame closes the rails"
    The panel goes in from the open end at the top of the uprights. Once the upper frame caps
    those rails there is no way in, and you are backing a rail off to fit a panel that would
    have dropped straight in earlier.

### Step 1 — Mount the fan to the panel

The printed panel does all the work: the fan fastens to it, and the panel carries the load
into the frame. The fan never touches the frame directly.

<figure markdown="span">
  ![The axial fan sitting in the aperture of the white printed panel on a bench](../assets/build/cooling-fan/01-fan-and-panel.webp)
  <figcaption>The fan and its printed panel before assembly. The fan sits into the square aperture; the recessed pocket above it is part of the panel, not somewhere the fan goes.</figcaption>
</figure>

!!! tip "Do this on the bench"
    Both fan screws are easy on a desk and awkward once the panel is between two uprights.
    Fit the fan, then take the assembly to the frame.

### Step 2 — Set the airflow direction

Axial fans are directional and every case has an arrow moulded into the housing for airflow,
usually with a second for rotation. Decide **now** whether this fan pulls air in or pushes it
out, because reversing it later means taking the panel back out of the rail.

!!! warning "Check the arrow, not the blades"
    You cannot reliably tell a fan's direction by looking at the blades. Find the moulded
    arrow on the housing edge and aim the flow across the electronics that actually get hot —
    see [Electronics Bay](electronics-bay.md).

### Step 3 — Offer the panel up to the uprights

Bring the assembly to the frame and line its edges up with the T-slots in the two uprights,
fan facing **inward** into the frame. Feed the lead through before the panel goes in.

<figure markdown="span">
  ![The fan panel held up against the two goRAIL uprights, not yet slid into the slots](../assets/build/cooling-fan/02-panel-before-slide-in.webp)
  <figcaption>Offered up, not yet engaged. Check the fan is facing the right way now — turning it round after the panel is seated means taking it all back out.</figcaption>
</figure>

### Step 4 — Slide the panel down into the T-slots

With both edges started in the slots, slide the panel down the uprights to its position, then
lock it with the corner brackets top and bottom.

<figure markdown="span">
  ![The fan panel seated in the uprights and held by corner brackets](../assets/build/cooling-fan/03-panel-slid-in.webp)
  <figcaption>Seated. The corner brackets top and bottom are what hold the height once the panel is slid to position.</figcaption>
</figure>

!!! tip "Slide to height before you tighten"
    The brackets clamp the panel wherever you leave it, so set the height against whatever the
    fan needs to blow across, then lock it. Loosening and sliding later is easy; drilling is not.

### Step 5 — Route and land the fan power

<figure markdown="span">
  ![Close view along the rail of the installed fan panel with its lead routed](../assets/build/cooling-fan/04-fan-installed.webp)
  <figcaption>Installed. The supply runs along the inside face of the rail and back toward the bay — not across the frame opening.</figcaption>
</figure>

<figure markdown="span">
  ![The lower frame with both the fan panel and the charge-port panel fitted](../assets/build/cooling-fan/05-both-panels-in-frame.webp)
  <figcaption>Both panels in context — fan panel on the right-hand rail, charge-port panel on the left.</figcaption>
</figure>

## Verify — done when…

- [ ] Fan solidly fastened to the panel; no contact between blades and any cable.
- [ ] Panel fully seated between the uprights and locked — it should not slide under hand pressure.
- [ ] Airflow direction confirmed against the housing arrow, aimed at the hot components.
- [ ] Fan lead routed along the rail, not across the frame opening, with no strain at either end.
- [ ] Fan spins up on power and runs without rubbing or rattling.

!!! note "Still to document"
    This module has **no rows in `hardware/parts.csv`** yet, so the page has no generated BOM —
    the fan, its printed panel and the T-slot hardware all need adding, then
    `python3 tools/bom.py --inject`. The fan's supply voltage and where in the bay it lands are
    not shown in the photos and are not stated here.
