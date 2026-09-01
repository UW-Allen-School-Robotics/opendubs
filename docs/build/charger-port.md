# 7 · Charger Port Integration

## At a glance

| | |
|---|---|
| **You'll do** | Mount + wire the external charge port; land the charger-present sense |
| **Prerequisites** | [Power Bay](power-bay.md) |
| **Interlock** | Charger connected ⇒ drive inhibited (see [Power architecture](../power-architecture.md)) |

!!! note "Stub — assembly steps coming"
    Mount the charge port on the lower frame and wire it into the power system, including the
    **charger-present → drive-inhibit** interlock (the robot must not drive while plugged in).

<figure class="amap amap--charge-port" markdown="1">
  <div class="amap-frame" markdown="1">
--8<-- "amap-map.html"
  </div>
  <figcaption>Where the <strong>Charge Port panel</strong> sits in the lower frame — see the <a href="../lower-frame/">assembly overview</a>.</figcaption>
</figure>

<div class="embed-facade is-placeholder" style="--embed-aspect: 62%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811b26814d71c309a2f?mode=embed" data-title="Charge port integration panel — Fusion 3D viewer">
  <span class="embed-facade__play">▶ Open the interactive 3D model — charge port panel</span>
</div>

## Assembly

Both this panel and the [fan panel](cooling-fan.md) are **printed panels that slide into the
goRAIL T-slot from the top** — they are not bolted through the frame. Fit them before the
upper frame closes off the top of the rails, or you will have to back a rail off to get them
in.

!!! danger "Wire it de-energized"
    The charge port lands on the battery side of the system. Build and wire it with the
    **battery disconnected** — see [Power architecture](../power-architecture.md).

### Step 1 — Slide the charge-port panel into the rail

<figure markdown="span">
  ![The printed charge-port panel slid into the goRAIL T-slot on the frame upright](../assets/build/charger-port/01-charge-port-panel-in-rail.webp)
  <figcaption>The black printed panel located in the goRAIL T-slot. The connector sits behind the upper aperture so it is reachable from outside the robot without opening anything up.</figcaption>
</figure>

!!! tip "Slide in from the top"
    The panel's edges are profiled to the 1118-series open goRAIL T-slot. Drop it in from the
    open end at the top of the upright, slide it down to its position, then lock it — no
    drilling, and it can be repositioned later by loosening and sliding.

### Step 2 — Check the port is reachable from outside

The whole point of the panel is that a charger can be plugged in without disassembly. Before
locking the panel down, check the connector actually presents through its aperture and that a
charge lead can be mated and released with the robot fully assembled.

### Step 3 — Land the charger wiring

<figure markdown="span">
  ![The lower frame with both the charge-port panel and the fan panel fitted](../assets/build/charger-port/02-both-panels-in-frame.webp)
  <figcaption>Both panels in context — charge-port panel on the left-hand rail, fan panel on the right. The charge leads run inboard along the rail toward the power bay.</figcaption>
</figure>

Route the charge leads along the inside of the rail back to the [Power Bay](power-bay.md),
clear of the frame opening and of anything that moves.

## Verify — done when…

- [ ] Panel fully seated in the T-slot and locked — it should not slide under hand pressure.
- [ ] Connector presents cleanly through its aperture; a charge lead mates and releases with
      the robot assembled.
- [ ] Charge leads routed along the rail, correct polarity, no strain at either end.
- [ ] **Charger connected ⇒ drive inhibited.** Verify the interlock before the robot is ever
      driven while plugged in.

!!! warning "The interlock is NOT documented here — it is not in the photos"
    The page header specifies **charger connected ⇒ drive inhibited**, and that is a safety
    behaviour, not a nicety: without it the robot can drive off while tethered to a charger.
    The three photos in the "3 - Fan and Charge Port Install" Drive folder show only the
    **mechanical** install of the panel. Nothing in them shows how charger-present is sensed
    or how it reaches the drive path, so nothing about that wiring is written above rather
    than guessed at. Whoever wired it: please add that step.

!!! note "Still to document"
    This module has **no rows in `hardware/parts.csv`** yet, so the page has no generated BOM
    — the connector, its printed panel and the T-slot hardware all need adding, then
    `python3 tools/bom.py --inject`. The specific connector type is also not identified above,
    because it is only partly visible through the panel aperture in the one photo.
