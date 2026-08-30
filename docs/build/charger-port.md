# 4 · Charger Port Integration

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
goRAIL T-slot from the top** — they are not bolted through the frame. Each panel is captured
between two vertical goRAIL uprights and locked with corner brackets, so it can be slid along
the rail to reposition and taken out again without drilling anything.

!!! danger "Wire it de-energized"
    The charge port lands on the battery side of the system. Build and wire it with the
    **battery disconnected** — see [Power architecture](../power-architecture.md).

!!! tip "Fit it before the upper frame closes the rails"
    The panel goes in from the open end at the top of the uprights. Once the upper frame caps
    those rails there is no way in.

### Step 1 — Offer the panel up to the uprights

The connector is a flanged receptacle behind a black faceplate, carried in the printed
surround. It sits proud of the frame line so a charge lead can be mated from outside.

Bring the panel to the frame and line its edges up with the T-slots in the two uprights,
faceplate **outward**. Feed its leads through into the frame before the panel goes in.

<figure markdown="span">
  ![The charge-port panel held up against the two goRAIL uprights, not yet slid into the slots](../assets/build/charger-port/01-panel-before-slide-in.webp)
  <figcaption>Offered up, not yet engaged, leads already fed through to the inside.</figcaption>
</figure>

### Step 2 — Slide the panel down into the T-slots

<figure markdown="span">
  ![The charge-port panel seated in the uprights and held by corner brackets](../assets/build/charger-port/02-panel-slid-in.webp)
  <figcaption>Seated. Corner brackets top and bottom hold the height once the panel is slid to position.</figcaption>
</figure>

### Step 3 — Check the port is reachable, then lock it down

The whole point of the panel is that a charger can be plugged in without disassembly. Check
the connector presents through its aperture and that a lead mates and releases with the robot
assembled — **then** tighten.

<figure markdown="span">
  ![A powered driver tightening the panel's mounting hardware on the frame](../assets/build/charger-port/03-tightening-the-panel.webp)
  <figcaption>Locking the panel down once the height and reach are right. A powered driver makes short work of the bracket screws.</figcaption>
</figure>

!!! warning "Try the actual charge lead, not just a look"
    A connector that is visible through the aperture is not necessarily one you can get a plug
    into — the latch needs clearance to release too. Mate and unmate the real lead before the
    panel is locked and the frame closes up.

### Step 4 — Land the charger wiring

<figure markdown="span">
  ![The rear of the charge-port connector inside the frame, showing its leads](../assets/build/charger-port/04-connector-rear.webp)
  <figcaption>The connector from inside the frame. The body sits in the printed pocket with its leads exiting toward the bay — this is the face you work from when landing the wiring.</figcaption>
</figure>

<figure markdown="span">
  ![Close view along the rail of the installed charge-port panel](../assets/build/charger-port/05-charge-port-installed.webp)
  <figcaption>Installed, seen along the rail. Leads run inboard along the rail toward the power bay.</figcaption>
</figure>

<figure markdown="span">
  ![The lower frame with both the charge-port panel and the fan panel fitted](../assets/build/charger-port/06-both-panels-in-frame.webp)
  <figcaption>Both panels in context — charge-port panel on the left-hand rail, fan panel on the right.</figcaption>
</figure>

Route the charge leads along the inside of the rail back to the [Power Bay](power-bay.md),
clear of the frame opening and of anything that moves.

## Verify — done when…

- [ ] Panel fully seated between the uprights and locked — it should not slide under hand pressure.
- [ ] The real charge lead mates **and releases** with the robot assembled.
- [ ] Charge leads routed along the rail, correct polarity, no strain at either end.
- [ ] **Charger connected ⇒ drive inhibited.** Verify the interlock before the robot is ever
      driven while plugged in.

!!! warning "The interlock is NOT documented here — it is not in the photos"
    The page header specifies **charger connected ⇒ drive inhibited**, and that is a safety
    behaviour, not a nicety: without it the robot can drive off while tethered to a charger.
    The photos in the "3 - Fan and Charge Port Install" Drive folder show the **mechanical**
    install and the back of the connector, but nothing showing how charger-present is sensed
    or how that signal reaches the drive path. So nothing about that wiring is written above
    rather than guessed at. Whoever wired it: please add that step.

!!! note "Still to document"
    This module has **no rows in `hardware/parts.csv`** yet, so the page has no generated BOM —
    the connector, its printed panel and the T-slot hardware all need adding, then
    `python3 tools/bom.py --inject`. The exact connector part is also not identified above; the
    rear view shows the body and its leads but no legible marking.
