# 3 · Power Bay

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

!!! warning "This page's scope and these photos disagree — reviewers, please settle it"
    The text above says the bay "holds the **drive-bus distribution** and nothing else."
    The build photos show considerably more mounted on one back plate: the ODrive regen
    clamp and its brake resistor, **two goBILDA 2×80 A ESCs**, an 8-channel servo/PWM
    breakout, a logic level shifter, the goBILDA Prism RGB driver, and the REV Mini Power
    Module. The steps below document **what was actually built**. Either the scope
    sentence needs widening or some of this belongs on
    [Electronics Bay](electronics-bay.md) — that is a call for whoever owns the split.

!!! danger "Wire it de-energized"
    Everything below is done with the **DC/DC input disconnected and the bus dead**. The
    ESC power leads terminate in XT90s that are easy to mate by reflex — leave them
    unmated until the [verify](#verify--done-when) checks pass.

!!! note "On the ordering of these steps"
    On Drive these photos are grouped into folders by **process**, not numbered across
    the build, and only "1 Back Plate Screw Mount" carries an explicit number. The order
    below is inferred from the photographs — each process picks up from the state the
    previous one left the plate in, which is what pins the sequence. Steps 4 and 5 are
    the clearest case: the level-shifter photos show lever-nut blocks on the left of the
    plate that are absent from every light-wiring photo, so the lights were wired first.

### Step 1 — Mount the back plate and lay the modules out

Everything in the bay lands on one perforated aluminium back plate, which bolts to the
printed bay tray. Dry-fit every module **before** driving a single screw — the plate's
hole grid is fixed and the modules only fit one way round without fouling each other.

<figure markdown="span">
  ![Bare perforated aluminium back plate resting on its printed bay tray](../assets/build/power-bay/01a-backplate-bare.webp)
  <figcaption>The bare back plate with the printed side brackets. The REV Mini Power Module sits above it, not yet placed.</figcaption>
</figure>

<figure markdown="span">
  ![Underside of the back plate showing the printed brackets and mounting screws](../assets/build/power-bay/01b-backplate-underside.webp)
  <figcaption>Underside — the plate screws down onto the printed brackets at the corners. Get this solid now; it is awkward to reach once the modules are on.</figcaption>
</figure>

<figure markdown="span">
  ![All the modules dry-fitted onto the plate before being screwed down](../assets/build/power-bay/01c-module-dry-fit.webp)
  <figcaption>Dry fit, left to right: ODrive regen clamp on its lime carrier, logic level shifter, 8-channel servo breakout, two goBILDA 2×80 A ESCs, then the REV Mini Power Module and Prism RGB driver on the adjacent tray.</figcaption>
</figure>

<figure markdown="span">
  ![The same layout with every module screwed down to the plate](../assets/build/power-bay/01d-modules-screwed-down.webp)
  <figcaption>Screwed down. Note the gap left between the ESCs and the regen clamp — that is where the brake resistor goes, and it needs the air.</figcaption>
</figure>

!!! tip "Leave the resistor room to breathe"
    The brake resistor is a 100 W part and gets genuinely hot in use. Don't close the gap
    around it to win back plate space, and don't run signal leads across its face.

### Step 2 — Build the regen clamp sub-assembly

The **ODrive regen clamp** dumps regenerated braking energy into a power resistor instead
of pushing it back up the bus. It mounts to its own printed carrier, which then bolts to
the back plate as a unit. See [regen clamp sizing](../regen-clamp-sizing.md) for why this
resistor and not another.

<figure markdown="span">
  ![ODrive regen clamp bolted to a lime green printed carrier](../assets/build/power-bay/02a-regen-clamp-carrier.webp)
  <figcaption>The clamp on its carrier. The green 6-way screw terminal is the whole interface: <code>IN+ IN− OUT− OUT+ R+ R−</code>.</figcaption>
</figure>

<figure markdown="span">
  ![The 100 W brake resistor mounted on the carrier wing beside the clamp](../assets/build/power-bay/02b-regen-clamp-resistor.webp)
  <figcaption>The gold-cased <strong>100 W 1.5 Ω</strong> resistor mounted to the carrier's wing and landed on <code>R+</code>/<code>R−</code>. It is a bare aluminium body — treat it as a hot surface.</figcaption>
</figure>

<figure markdown="span">
  ![Underside of the carrier showing slots exposing the clamp's terminal legend and DIP switches](../assets/build/power-bay/02c-regen-clamp-underside.webp)
  <figcaption>The carrier's underside slots deliberately expose the board's silkscreen legend and the <code>S1</code>–<code>S3</code> DIP switches, so the clamp can be set and checked without unbolting it.</figcaption>
</figure>

!!! warning "Check the DIP switches before the carrier goes on the plate"
    The clamp's threshold DIP block is only reachable through those slots once mounted.
    Set it while the assembly is loose and confirm it through the slot afterwards.

### Step 3 — Wire the ESC signal and power

Each **goBILDA 2×80 A ESC** drives two channels, so two ESCs cover all four wheels. Each
channel takes a 3-pin servo-style lead (signal / +6 V BEC / ground) from the 8-channel
breakout, and each ESC takes bus power on an XT90.

<figure markdown="span">
  ![Close view of the pins inside a 3-pin servo connector](../assets/build/power-bay/03a-servo-lead-pins.webp)
  <figcaption>A 3-pin servo lead. Note which pin is signal before you plug anything in — the housings are not keyed against being flipped.</figcaption>
</figure>

<figure markdown="span">
  ![A servo connector with brown and yellow conductors](../assets/build/power-bay/03b-servo-connector.webp)
  <figcaption>The connector as it presents on the ESC side. In this build brown is ground and yellow is signal.</figcaption>
</figure>

<figure markdown="span">
  ![A brown-and-yellow two-conductor extension lead with a connector at each end](../assets/build/power-bay/03c-servo-extension-lead.webp)
  <figcaption>An extension lead made up to reach from the breakout across to the far ESC. Make these to length rather than coiling slack on the plate.</figcaption>
</figure>

<figure markdown="span">
  ![Both ESCs mounted with servo leads running to their BEC and signal headers](../assets/build/power-bay/03d-esc-bec-headers.webp)
  <figcaption>Both ESCs landed. The <code>(6V BEC)</code> and <code>S</code> headers sit along the lower edge of each ESC, one pair per channel.</figcaption>
</figure>

<figure markdown="span">
  ![XT90 power leads joining both ESCs to a single bus connector](../assets/build/power-bay/03e-esc-xt90-power.webp)
  <figcaption>The ESC power pair, joined into one XT90 that feeds the bay. Both ESCs are rated 9–30 V (3–7S), comfortably inside the 24 V bus.</figcaption>
</figure>

!!! warning "Keep channel order straight now, not later"
    Which ESC channel drives which wheel is set here and is invisible once the harness is
    tidied. Label each lead as you make it — [drive bring-up](../drive-bringup.md) maps
    channel → wheel → direction, and it is far quicker with the leads already labelled.

### Step 4 — Make up the Prism RGB (light) harness

The **goBILDA Prism RGB driver** takes a 4-wire JST lead. Rather than splice it directly,
the build fans it through a stack of four **lever-nut connectors**, which makes the run
serviceable without re-crimping anything.

<figure markdown="span">
  ![A 4-conductor JST lead being stripped with a hand stripper](../assets/build/power-bay/04a-strip-jst-lead.webp)
  <figcaption>Stripping the 4-conductor JST lead. Take off just enough to seat fully in a lever nut — excess bare copper outside the jaw is what shorts later.</figcaption>
</figure>

<figure markdown="span">
  ![The stripped JST lead with four conductors fanned out](../assets/build/power-bay/04b-jst-lead-stripped.webp)
  <figcaption>All four conductors stripped and separated, connector still intact at the far end.</figcaption>
</figure>

<figure markdown="span">
  ![A stack of four lever-nut connectors between two prepared cable ends](../assets/build/power-bay/04c-lever-nuts-and-leads.webp)
  <figcaption>The parts for the join: four lever nuts in the middle, the driver-side lead left, the bay-side lead right.</figcaption>
</figure>

<figure markdown="span">
  ![One cable landed into the lever-nut stack](../assets/build/power-bay/04d-first-lead-landed.webp)
  <figcaption>First lead landed — one conductor per lever nut, colours kept in order down the stack.</figcaption>
</figure>

<figure markdown="span">
  ![The first lead fully seated with all levers closed](../assets/build/power-bay/04e-first-lead-seated.webp)
  <figcaption>Levers closed. Tug each conductor — a lever nut that did not bite lets go under a light pull, which is exactly what you want to find now.</figcaption>
</figure>

<figure markdown="span">
  ![Both leads landed on opposite sides of the lever-nut stack](../assets/build/power-bay/04f-both-leads-landed.webp)
  <figcaption>Second lead landed on the opposite face. Keep the colour order identical on both sides — the stack is a straight-through join, not a crossover.</figcaption>
</figure>

<figure markdown="span">
  ![The completed light harness with a JST connector at each end](../assets/build/power-bay/04g-harness-complete.webp)
  <figcaption>The finished harness: JST to JST through the lever-nut stack, ready to go on the plate.</figcaption>
</figure>

<figure markdown="span">
  ![The harness installed on the plate running to the Prism RGB driver](../assets/build/power-bay/04h-harness-to-rgb-driver.webp)
  <figcaption>Harness installed, running across to the Prism RGB driver on the adjacent tray. The lever-nut stack is cable-tied down so it cannot flap.</figcaption>
</figure>

<figure markdown="span">
  ![The Prism RGB driver wired, seen with the fuse block and ESCs](../assets/build/power-bay/04i-rgb-driver-wired.webp)
  <figcaption>The driver wired up. Its <code>I²C</code> and <code>PWM</code> headers are both broken out — this build uses I²C, which is what Step 5 exists for.</figcaption>
</figure>

### Step 5 — Add the 5 V ↔ 3.3 V logic level shifter

The REV and goBILDA I²C devices do not share a logic level, so the bus passes through a
**4-channel bidirectional level shifter** (an Adafruit BSS138-style board). Both sides fan
through their own lever-nut block, same pattern as the light harness.

<figure markdown="span">
  ![The level shifter board, two lever-nut blocks and JST leads laid out](../assets/build/power-bay/05a-level-shifter-parts.webp)
  <figcaption>The parts: the shifter in the centre, a lever-nut block for each side, and the JST leads that will reach the two buses.</figcaption>
</figure>

<figure markdown="span">
  ![The same parts arranged with the leads positioned for assembly](../assets/build/power-bay/05b-leads-laid-out.webp)
  <figcaption>Laid out in the order they connect, so the 5 V side and the 3.3 V side do not get swapped mid-build.</figcaption>
</figure>

<figure markdown="span">
  ![Leads stripped and prepared beside the shifter and lever-nut blocks](../assets/build/power-bay/05c-leads-stripped.webp)
  <figcaption>Conductors stripped. The board's silkscreen reads <code>VIN 3V G SCL SDA</code> on one edge and <code>5V 3V G SCL SDA</code> on the other — worth reading before landing anything.</figcaption>
</figure>

<figure markdown="span">
  ![Leads trimmed to their final lengths](../assets/build/power-bay/05d-leads-cut-to-length.webp)
  <figcaption>Cut to length. These runs cross a populated plate, so measure the actual route rather than guessing generously.</figcaption>
</figure>

<figure markdown="span">
  ![The shifter wired between the two lever-nut blocks](../assets/build/power-bay/05e-shifter-between-blocks.webp)
  <figcaption>The shifter sitting between its two blocks — 5 V bus on one side, 3.3 V on the other, with the whole assembly still loose on the bench.</figcaption>
</figure>

<figure markdown="span">
  ![Both lever-nut blocks mounted to the plate with the shifter between them](../assets/build/power-bay/05f-blocks-mounted.webp)
  <figcaption>Blocks mounted to the left end of the back plate — the area deliberately left clear back in Step 1.</figcaption>
</figure>

<figure markdown="span">
  ![The level shifter installed on the plate with the rest of the bay populated](../assets/build/power-bay/05g-shifter-installed.webp)
  <figcaption>Shifter installed. From here the I²C run reaches the regen clamp's sensor and onward to the Prism driver.</figcaption>
</figure>

<figure markdown="span">
  ![The harness cable-tied and dressed down against the plate](../assets/build/power-bay/05h-harness-tidied.webp)
  <figcaption>Harness dressed and cable-tied. Ties go through the plate perforations, which is what the hole grid is for.</figcaption>
</figure>

<figure markdown="span">
  ![The completed power bay with all modules mounted and wired](../assets/build/power-bay/05i-bay-complete.webp)
  <figcaption>The bay as built. Compare against your own before energizing anything.</figcaption>
</figure>

!!! warning "Confirm which side is which before power"
    A level shifter wired backwards will not announce itself — it simply puts 5 V on a
    3.3 V part. Check the silkscreen against your bus on both sides while the bus is dead.

### Step 6 — Remaining leads

<figure markdown="span">
  ![A spare 4-pin JST lead being stripped on the bench](../assets/build/power-bay/06a-strip-spare-jst.webp)
  <figcaption>One of the remaining 4-pin JST leads being made up. Same technique as Step 4.</figcaption>
</figure>

<figure markdown="span">
  ![Overview of the populated bay showing the lever-nut blocks and modules](../assets/build/power-bay/06b-bay-overview.webp)
  <figcaption>Overview of the finished plate — useful as a reference for where each lever-nut block ended up.</figcaption>
</figure>

## Verify — done when…

- [ ] Back plate solidly mounted; every module screwed down, nothing resting on another.
- [ ] Brake resistor has clear air around it and no leads across its face.
- [ ] Regen clamp DIP switches set and readable through the carrier slots.
- [ ] Every lever-nut conductor survives a firm tug.
- [ ] Level shifter's 5 V and 3.3 V sides confirmed against the silkscreen — **before** power.
- [ ] ESC channel → wheel mapping labelled on each lead.
- [ ] Bus fully **de-energizes on e-stop** (contactor drops) — verify before first energize.
- [ ] (After electronics) run [Drive bring-up](../drive-bringup.md) to map each
      channel → wheel → direction.
