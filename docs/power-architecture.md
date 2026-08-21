# Power architecture

<figure markdown="span">
  ![OpenDubs power architecture block diagram](assets/diagrams/power-architecture.png)
  <figcaption>Power spine — battery → e-stop contactor → drive &amp; logic buses.</figcaption>
</figure>

- **Diagram source (Figma):** *TODO — add the Figma share link (view / download).*

The block diagram above is the authoritative view of the power spine; the notes below are just
the narrative around it.

## The spine

The diagram is the source of truth; this is the summary.

- **Source** — a single **ECO-WORTHY ~52 V LiFePO4** battery (JBD BMS on RS485), feeding two
  step-down converters: a **35 A** buck for the logic bus and a **60 A** buck for the drive bus.
- **Logic-24 V bus** — from the **35 A** buck, **always-on** (not gated by the contactor). Its
  fused branches — the **REV Mini Power Module** in the
  [Electronics Bay](build/electronics-bay.md) — feed Thor, the StampPLC, the router, USB hub(s),
  and the E-stop signal.
- **Safety contactor + e-stop** — a GIGAVAC GX11 gates the **drive** path. Its coil is held only
  when the **RUN** button is engaged, the **E-STOP** button is not pressed, and the StampPLC's
  **R0** relay is closed — so an e-stop press, a software e-stop (R0 open), or loss of logic
  power all drop it.
- **Drive-24 V bus** — the **60 A** buck sits **behind the contactor**, so the drive bus
  de-energizes on e-stop. Its fused branches — the [REV PDH](build/power-bay.md) — feed the drive
  ESCs; the ODrive **regen clamp** sits at the ESCs, downstream because the buck is one-way (see
  [Regen clamp sizing](regen-clamp-sizing.md)).

## See also

- [Power & data interface](interfaces/power-data.md) — what a payload can tap, and the comms buses.
- [Power Bay](build/power-bay.md) · [Electronics Bay](build/electronics-bay.md) — the builds.
- [Regen clamp sizing](regen-clamp-sizing.md) — the brake-resistor sizing.
