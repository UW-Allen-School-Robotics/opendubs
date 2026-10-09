# 5 · Mini Power Module (logic bus)

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The 24 V logic bus — six fused branches out of the REV Mini Power Module to Thor, the StamPLC, the E-stop system, the accessory bus, and the PRISM |
    | **Time** | *TODO* |
    | **Difficulty** | Intermediate (wiring; one connector must be re-pinned) |
    | **Prerequisites** | [Electronics Bay](electronics-bay.md) — the module is mounted on the bay's grid plate |
    | **Mates to** | Thor and the PRISM (lower frame); the StamPLC, E-stop system, and accessory bus (upper frame) |

<figure class="amap amap--electronics-bay" markdown="1">
  <div class="amap-frame" markdown="1">
--8<-- "amap-map.html"
  </div>
  <figcaption>The Mini Power Module lives on the <strong>Electronics Bay</strong> grid plate — see the <a href="../lower-frame/">assembly overview</a>.</figcaption>
</figure>

The **REV Mini Power Module** (MPM) is the robot's **24 V "logic" bus**: everything that thinks,
senses, or signals is fed from it, and nothing that *drives* is. The drive bus is a separate
distribution behind the e-stop contactor — see the [Power Bay](power-bay.md).

The module is **mounted in the electronics bay**, but only two of its six branches stay in the
lower frame. The rest run up into the upper frame, which is why this step is split out: **the
branch wiring is finished after the upper and lower frames are joined**, not while the bay is
on the bench.

## The six branches

The MPM has **six fused branch circuits**, each on a latching WAGO output terminal. Main power
comes in on the larger WAGO input terminal.

| Position | Feeds | Fuse | Lives in |
|---:|---|---:|---|
| **1** | StamPLC | 5 A | Upper frame |
| **2** | E-stop system | 5 A | Upper frame |
| **3** | Accessory bus → USB hub + Wi-Fi router | 5 A | Upper frame |
| **4** | *Spare* | — | — |
| **5** | **Thor** | **10 A** | Lower frame |
| **6** | PRISM light controller (via XT30) | 5 A | Lower frame |

**Every branch is 5 A except Thor, which is 10 A.** Leave position 4 unfused until you actually
use it — an unfused spare bay is an obvious "not in service" marker, and it can't quietly
energise a half-finished circuit.

The module takes **ATM (mini) blade fuses**. Its channels are rated well above what we use here
(15 A each); the 5 A / 10 A values are chosen to protect *the branch wiring and the load*, not
the module.

!!! note "Position 3 fans out through an accessory bus"
    The USB hub and the Wi-Fi router share **one** fused branch. Position 3 lands on a small
    +/− accessory bus in the upper frame, and both loads tap off it. If you later add a third
    accessory, add it to that bus — don't consume the spare bay unless the combined draw
    approaches 5 A.

## Wiring the branches — join the frames first

!!! tip "Recommended order: land the upper-frame branches last"
    Run **only the main supply line** into the MPM while the bay is accessible. For the four
    other branches — and especially the three that go up (positions 1, 2, 3) — **route the wire
    to its load in the upper frame first, then insert it into the MPM's WAGO terminal after the
    upper and lower frames are joined.**

    The WAGO 2601 outputs are tool-less and latching, so landing a wire late is trivial —
    whereas guessing a length across a joint you haven't made yet is not. This also keeps you
    from pulling on seated conductors while the frames are being mated.

Working order:

1. Land the **main supply** on the MPM input terminal (WAGO 2606) while the bay is on the bench.
2. Land the two **lower-frame branches** — position 5 (Thor) and position 6 (PRISM) — since both
   loads are in the lower frame.
3. Join the upper and lower frames.
4. Route positions **1, 2, 3** to the StamPLC, the E-stop system, and the accessory bus, cut to
   the real length, then **insert into the MPM WAGOs**.
5. Fuse the module *last* — work through the **Verify** checklist at the foot of this page first.

## Thor's Micro-Fit connector — read this before you plug anything in

!!! danger "Pre-made Micro-Fit cables will not match Thor's pinout"
    Thor's DC input is a **Molex Micro-Fit 3.0, 2×2**. The housing is common, so a pre-made
    cable *will* plug in — but the **pin assignment on off-the-shelf cables is very likely
    different**, and **wire colors mean nothing here**. Verify every conductor with a meter and
    re-pin the housing as needed **before** first power-on.

Thor's expected pinout, **viewed looking into the board-side connector**:

```
   ┌─────┬─────┐
   │  4  │  3  │     4 = GND      3 = GND
   ├─────┼─────┤
   │  2  │  1  │     2 = PWR      1 = PWR
   └─────┴─────┘
```

| Pin | Function |
|---:|---|
| 1 | PWR (`VCC_DCIN`) |
| 2 | PWR (`VCC_DCIN`) |
| 3 | GND |
| 4 | GND |

Both power pins are tied together on the board, and both grounds likewise.

!!! warning "Use both power pins and both grounds"
    Don't single up onto one PWR and one GND. The current splits across the pair, which is what
    makes a 20 AWG cable acceptable on a 10 A branch. Populating one pin of each puts the whole
    load on a single 20 AWG conductor.

!!! note "Input rating"
    Thor's DC input is rated **28 V, 8 A max**. The branch is fused at 10 A, which protects the
    *wiring* — it will not protect the connector against a sustained overload. Thor's normal
    draw is well under this; the fuse is there for fault current.

**The cable we use** is listed in the BOM below. It is sold as an *ATX 4-pin 12 V* cable — the
ATX standard assigns those four pins differently from Thor, which is exactly the trap this
section exists to prevent. Buy it for the housing and the 20 AWG conductors, then re-pin it.

## Wiring diagram

The full branch map, fuse values, and the Micro-Fit detail — pan/zoom the live board:

<div style="position: relative; padding-bottom: 60%; height: 0; overflow: hidden; border: 1px solid var(--md-default-fg-color--lightest); border-radius: 0.2rem; margin: 1em 0;">
  <iframe style="position: absolute; inset: 0; width: 100%; height: 100%; border: 0;" src="https://embed.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=10-430&embed-host=opendubs-docs" allowfullscreen loading="lazy" title="Mini Power Module fuse connections (Figma)"></iframe>
</div>

- **Diagram source (Figma):** [OpenDubs board — Mini PM fuse connections](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=10-430) — anyone with the link can view and export it.

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = mini-power-module`), then run `tools/bom.py --inject`.
    The **[REV Mini Power Module](https://www.revrobotics.com/rev-11-1956/)** itself is listed
    once, on the [Electronics Bay](electronics-bay.md#bill-of-materials) BOM, since that's where
    it is mounted.

<!-- BOM-AUTO:build:mini-power-module -->
**Thor branch**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Micro-Fit 3.0 4-pin cable (Thor DC input)](https://a.co/d/06nenwIA) | `B07Z6BJCVH` | 1 | Sold as an ATX 4-pin 12 V cable — the housing fits Thor, the PIN ASSIGNMENT DOES NOT. Verify against Thor's pinout and re-pin before first power-on; do not trust wire colors. |

**Fuses**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| ATM (mini) blade fuse, 5 A | — | 4 | Positions 1/2/3/6 — StamPLC, E-stop, accessory bus, PRISM. Leave the spare bay unfused until it is used. |
| ATM (mini) blade fuse, 10 A | — | 1 | Position 5 — Thor only. Every other branch is 5 A. |

**Lighting branch**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| XT30 connector pair (PRISM branch) | — | 1 | Position 6 lands on the PRISM light controller through an XT30; see the wiring board |
<!-- /BOM-AUTO:build:mini-power-module -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools-consumables).
You will also want a **multimeter** for the Micro-Fit continuity check, and a **Micro-Fit
extraction tool** (or a small precision pick) if you need to depin the Thor cable.

## Assembly

!!! note "Photos pending"
    Drop step photos into `docs/assets/build/mini-power-module/`, then add numbered steps here.

## Verify — done when…

- [ ] Each branch lands on its intended load — check against the table above, not against wire color.
- [ ] Fuse values correct: **10 A on position 5 (Thor) only**; 5 A on 1, 2, 3, 6; position 4 unfused.
- [ ] **Micro-Fit verified with a meter**: pins 1 & 2 = PWR, pins 3 & 4 = GND, measured at the
      Thor end of the finished cable — *before* it is ever plugged into Thor.
- [ ] Both PWR pins and both GND pins populated on the Thor cable.
- [ ] The PRISM branch's XT30 is polarity-correct.
- [ ] No conductor is under tension across the upper/lower frame joint.
- [ ] With fuses in and the bus live: each load powers up; no fuse runs warm.
