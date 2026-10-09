# 11 · OTOS Module

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The OTOS mounted at 10 mm, level-shifted and adapted to the M5 Grove bus |
    | **Time** | *TODO* |
    | **Difficulty** | Beginner–intermediate (a little wiring) |
    | **Prerequisites** | [Base Frame](base-frame.md) |
    | **Mates to** | Frame underside; M5 I2C (Grove) |
    | **Install timing** | Assemble any time; it mounts to the frame **underside**, so install *after* the [wheels](wheel-install.md) are on (step 7) — or with the robot jacked up for clearance |
    | **Optional?** | Yes — one of several odometry sources |

<div class="embed-facade" markdown="1" style="--embed-aspect: 78%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811c1903895fbcdd3cf?mode=embed" data-title="OTOS module — Fusion 3D viewer">
![OTOS sensor module — click to open the interactive Fusion 3D viewer](../assets/build/otos/hero.png)
<span class="embed-facade__play">▶ Open the interactive 3D model</span>
</div>

*The OTOS module — **click to orbit/zoom the live Fusion model**. The sensor and Adafruit level
converter sit in a printed enclosure that holds the sensor at its 10 mm ride height above the floor.*

!!! info "Optional module — one of several odometry options"
    The OTOS is **optional**. It's one of several spatial / odometry sources for the robot
    (alongside the [Pinpoint dead-wheel system](pinpoint.md), and others) — build it if you
    want optical floor-tracking odometry, or skip it.

The **OTOS** — a [SparkFun Optical Tracking Odometry Sensor](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html)
(PAA5160E1) — reports X / Y / heading by watching the floor. It mounts at the sensor's
recommended **10 mm** height above the ground plane.

!!! warning "3.3 V sensor on a 5 V bus — level-shift required"
    The OTOS runs its I2C at **3.3 V**, but the **M5 (StamPLC) I2C bus is 5 V**. Do **not**
    wire it straight in. This build uses an
    [Adafruit logic level converter](https://www.adafruit.com/product/5637) to shift
    3.3 V ↔ 5 V, and converts the SparkFun/Adafruit **Qwiic** connector over to the **M5
    Grove** connector on the bus side.

## CAD & 3D model

- **View in browser (Fusion):** <https://a360.co/3UyyBKq> — or click the hero above to load it inline.
- **Download:** *TODO — export STEP + the enclosure STLs into `hardware/`*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = otos`), then run `tools/bom.py --inject`. WAGOs are
    listed as the default join method — you can solder instead (see the tools note).

<!-- BOM-AUTO:build:otos -->
**Sensor & level shifting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [SparkFun Optical Tracking Odometry Sensor (OTOS)](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html) | — | 1 | PAA5160E1, Qwiic; 3.3 V — needs level-shifting to the 5 V M5 I2C bus |
| [Adafruit logic level converter](https://www.adafruit.com/product/5637) | `Adafruit 5637` | 1 | Shifts OTOS 3.3 V I2C up to the 5 V M5 bus |

**Enclosure & mounting**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| 3D-printed enclosure set | — | 1 | A few small printed parts; STLs in hardware/ TBD |
| [M4 x 55 mm socket-head screw](https://www.amazon.com/iExcell-Stainless-Socket-Screws-Wrench/dp/B089NGGLZQ) | — | 4 | Long standoff screws for the 10 mm sensor mount; confirm qty |

**Wiring**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [WAGO lever connectors](https://a.co/d/09VO6gwP) | — | a few | Qwiic<->Grove and I2C/power joins — or solder instead |
<!-- /BOM-AUTO:build:otos -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md#tools--consumables).
If you **solder** the joins instead of using WAGOs, add a soldering iron + heat-shrink.

## Assembly

The wiring harness — the 5 V M5 Grove cable from the PaHub, joined through WAGOs to the
[Adafruit logic level converter](https://www.adafruit.com/product/5637), then over Qwiic to the OTOS:

<figure markdown="span">
  ![OTOS wiring harness — M5 Grove cable from the PaHub, four WAGOs, the Adafruit level converter, then the OTOS](../assets/diagrams/otos-wiring-harness.png)
  <figcaption>The PaHub Grove cable (SCL / SDA / 5 V / GND) joins through four WAGOs to the Adafruit
  logic level converter; its 3.3 V side runs over Qwiic to the OTOS. The level converter is what
  keeps the 5 V bus off the 3.3 V sensor.<br><small>Product photos: SparkFun Optical Tracking Odometry Sensor, © SparkFun Electronics, licensed CC BY-NC-SA 3.0; Adafruit QT 5V to 3V Level Shifter, © Adafruit Industries, all rights reserved. They are not covered by this site's CC BY 4.0 licence.</small></figcaption>
</figure>

The build splits cleanly in two, and the photos follow that split: **Construction** is all
bench work and can be done any time, then **Attachment** fits the finished module to the
robot. Do them in that order — the module is far easier to wire on a desk than upside down
under a frame.

!!! danger "Never connect the OTOS directly to the 5 V bus"
    The sensor's I²C runs at **3.3 V**; the M5 StamPLC bus is **5 V**. Every step below keeps
    the Adafruit converter in line between them. There is no point in the build where the
    sensor should see the bus directly, not even briefly for a test.

!!! warning "Class 1 laser — don't touch the optic"
    The board is silkscreened **`Class 1 Laser 850 nm` / `DO NOT TOUCH!`** across the sensor.
    Handle the PCB by its edges. A fingerprint on that window degrades tracking, and it is
    awkward to clean once the housing is closed.

## Construction

### Step 1 — Unwrap the sensor

The OTOS ships with an amber protective film over the optic. It comes off just before the
sensor goes into its cradle — not earlier, and not left on.

<figure markdown="span">
  ![The OTOS board with an amber protective film still covering the optical sensor](../assets/build/otos/01-otos-board-film-on.webp)
  <figcaption>As it arrives: amber film over the optic, Qwiic lead already fitted. The header runs <code>GND · 3V3 · SDA · SCL · IO9 · IO10</code>; this build uses the Qwiic connector, not the header.</figcaption>
</figure>

<figure markdown="span">
  ![The same board with the film removed, exposing the optical sensor window](../assets/build/otos/02-otos-board-film-off.webp)
  <figcaption>Film removed. The bare optic is what reads the floor — from here on, hold the board by its edges only.</figcaption>
</figure>

### Step 2 — Seat the sensor in its cradle

The printed enclosure is a two-part affair: an inner cradle that grips the PCB, sitting
inside the outer body that later bolts to the frame. The sensor faces **down**, through the
aperture in the base.

<figure markdown="span">
  ![The OTOS board dropped into the printed cradle inside the enclosure](../assets/build/otos/03-sensor-in-cradle.webp)
  <figcaption>Board dropped into the cradle. The Qwiic lead exits upward through the slot so it is not trapped when the body closes.</figcaption>
</figure>

<figure markdown="span">
  ![The cradle seated down into the enclosure body](../assets/build/otos/04-cradle-seated-in-housing.webp)
  <figcaption>Cradle seated into the body. It should drop in without force — if it fights you, check for elephant's foot on the first printed layer rather than pressing harder.</figcaption>
</figure>

<figure markdown="span">
  ![The enclosure closed around the sensor, seen end-on](../assets/build/otos/05-housing-closed-around-sensor.webp)
  <figcaption>Closed up, seen end-on. The PCB is visible through the side slot — that gap is the cable route, not a mistake.</figcaption>
</figure>

<figure markdown="span">
  ![The underside of the enclosure showing the sensor aperture](../assets/build/otos/06-sensor-aperture-underside.webp)
  <figcaption>The underside aperture. Check the optic is centred in it and square to the base before going further — this is the face that will look at the floor.</figcaption>
</figure>

### Step 3 — Fit the standoff screws

Four **M4 × 55 mm** socket-head screws pass right through the module. Their length is what
sets the sensor's **10 mm ride height** above the floor, so they are structural, not just
fasteners.

<figure markdown="span">
  ![Two long M4 socket-head screws inserted through the top of the enclosure](../assets/build/otos/07-standoff-screws-inserted.webp)
  <figcaption>The 55 mm screws dropped through from the top. They are long enough to look wrong — that is correct.</figcaption>
</figure>

<figure markdown="span">
  ![The screws seated flush with their heads against the enclosure](../assets/build/otos/08-standoff-screws-seated.webp)
  <figcaption>Seated, heads down onto the printed face. Do not torque them yet; final height gets set when the module meets the rail.</figcaption>
</figure>

!!! warning "The screw length IS the ride height"
    The OTOS wants **10 mm** between its optic and the floor. Substituting a shorter or
    longer screw changes that gap and the sensor's scale factor goes with it. If you must
    substitute, re-measure the standoff and expect to recalibrate.

### Step 4 — Prepare the level converter

The [Adafruit 5637](https://www.adafruit.com/product/5637) is a 4-channel bidirectional
shifter with a Qwiic/STEMMA-QT connector on each side. It is the only thing standing between
a 5 V bus and a 3.3 V sensor, so read its silkscreen before wiring anything.

<figure markdown="span">
  ![The level converter board beside the assembled enclosure and its Qwiic lead](../assets/build/otos/09-shifter-and-housing-laid-out.webp)
  <figcaption>The shifter laid out with the assembled module. The sensor's Qwiic lead reaches it directly; the other side faces the robot bus.</figcaption>
</figure>

<figure markdown="span">
  ![Close view of the level converter silkscreen showing both voltage sides](../assets/build/otos/10-level-shifter-silkscreen.webp)
  <figcaption>The silkscreen is the whole safety story: <code>5V logic level</code> with <code>5V 3V G SCL SDA</code> on one edge, <code>3V logic level</code> with <code>VIN 3V G 3CL 3DA</code> on the other. <strong>Sensor to the 3 V side.</strong></figcaption>
</figure>

### Step 5 — Build the lever-nut junction

Rather than splice the Qwiic leads directly, this build fans them through a block of four
lever-nut connectors — one per conductor. The join stays serviceable, and a lead can be
swapped without re-crimping.

<figure markdown="span">
  ![A block of four lever-nut connectors on the bench](../assets/build/otos/11-lever-nut-block.webp)
  <figcaption>Four lever nuts, ganged into a block — one for each of SCL, SDA, V+ and GND.</figcaption>
</figure>

<figure markdown="span">
  ![The first conductor landed in the lever-nut block](../assets/build/otos/12-first-lead-landed.webp)
  <figcaption>First conductor in. Strip only as much as the jaw takes — bare copper standing proud of the housing is what shorts to its neighbour.</figcaption>
</figure>

<figure markdown="span">
  ![The block with one lead fully seated and its lever closed](../assets/build/otos/13-block-with-lead.webp)
  <figcaption>Lever closed. Tug it — a nut that did not bite releases under light pull, which is much better discovered now.</figcaption>
</figure>

<figure markdown="span">
  ![All four conductors landed into the block](../assets/build/otos/14-four-leads-landed.webp)
  <figcaption>All four in, colours kept in order down the block: yellow, blue, red, black.</figcaption>
</figure>

<figure markdown="span">
  ![The block with conductors landed on both faces](../assets/build/otos/15-block-both-sides-landed.webp)
  <figcaption>Both faces landed. Keep the colour order identical on each side — this is a straight-through join, not a crossover.</figcaption>
</figure>

### Step 6 — Bench build complete

<figure markdown="span">
  ![The finished module, level converter and lever-nut block wired together on the bench](../assets/build/otos/16-bench-harness-complete.webp)
  <figcaption>Construction done: module, shifter and lever-nut block wired as one assembly. The Adafruit <code>5V-&gt;3V Shifter QT (P5637)</code> bag in shot is the part to check yours against.</figcaption>
</figure>

## Attachment

### Step 7 — Make up the bus-side lead

The robot-bus end needs terminating to match whatever it plugs into. Here the 4-conductor
JST lead is crimped to individual header sockets.

<figure markdown="span">
  ![Crimp pins, housings and tweezers laid out with the four-conductor lead](../assets/build/otos/17-crimp-pins-and-lead.webp)
  <figcaption>Crimp pins, single-way housings and fine tweezers. The pins are small enough that tweezers are not optional.</figcaption>
</figure>

<figure markdown="span">
  ![The finished lead with a JST connector at one end and header sockets at the other](../assets/build/otos/18-bus-lead-terminated.webp)
  <figcaption>Terminated: JST at one end, individual header sockets at the other. Separate sockets let each signal be moved independently on the bus side.</figcaption>
</figure>

### Step 8 — Fit the converter into the housing

The shifter tucks into the module body so it is protected and travels with the module as one
part, rather than dangling on the harness.

<figure markdown="span">
  ![The level converter being placed into its recess in the printed housing](../assets/build/otos/19-shifter-into-housing.webp)
  <figcaption>The shifter dropped into its recess, Qwiic leads on both sides, with the lever-nut block still outboard. The two 55 mm screws protrude ready for the rail.</figcaption>
</figure>

### Step 9 — Dress the wiring

<figure markdown="span">
  ![The harness folded and routed neatly inside the module body](../assets/build/otos/20-wire-management.webp)
  <figcaption>Harness dressed into the body: shifter at the top, lever-nut block folded down into the lower bay, one lead leaving for the robot. Nothing crosses the sensor aperture.</figcaption>
</figure>

!!! tip "Dress it before it goes under the robot"
    Everything here is reachable on a bench and awkward once the module is bolted beneath the
    frame. Get the harness folded, the block seated and the exit lead pointed the right way
    now.

### Step 10 — Mount to the middle cross rail

The module bolts to the **middle cross rail** of the lower frame, hanging beneath it so the
sensor looks straight down at the floor.

<figure markdown="span">
  ![The module offered up to the middle U-channel cross rail](../assets/build/otos/21a-mounted-to-cross-rail.webp)
  <figcaption>Offered up to the middle cross rail. The two 55 mm screws pass up through the channel's hole grid and take nuts on top.</figcaption>
</figure>

<figure markdown="span">
  ![Top view through the rail perforations showing the module below](../assets/build/otos/21b-mounted-top-view.webp)
  <figcaption>From above: the module's internals are visible through the channel perforations, which is how you check nothing is pinched after tightening.</figcaption>
</figure>

<figure markdown="span">
  ![Angled top view showing the mounting screws and nuts](../assets/build/otos/21c-mounted-top-angled.webp)
  <figcaption>Angled view of the fixings — screws up through the rail, nuts on the upper face. Snug, not crushed; over-torquing distorts the printed body and tilts the sensor.</figcaption>
</figure>

<figure markdown="span">
  ![Side view of the module hanging below the cross rail](../assets/build/otos/21d-mounted-side-view.webp)
  <figcaption>Side on. This is the view that shows the standoff gap — the sensor face should sit parallel to the floor, not nose-down.</figcaption>
</figure>

<figure markdown="span">
  ![The frame inverted, showing the module on the underside of the cross rail](../assets/build/otos/21e-mounted-underside.webp)
  <figcaption>Frame inverted. The module sits mid-span on the cross rail, well clear of the drive pods and their leads.</figcaption>
</figure>

<figure markdown="span">
  ![Zoomed out view of the whole frame with the module mounted centrally](../assets/build/otos/21f-mounted-in-frame.webp)
  <figcaption>In context: centred in the frame, roughly over the robot's origin. Its offset from the origin is what you feed into the odometry config.</figcaption>
</figure>

!!! warning "Install after the wheels, or jack the robot up"
    The module hangs below the frame at a 10 mm ride height. With the robot sitting on the
    floor there is no room to get at it — fit it after the [wheels](wheel-install.md) are on,
    or with the frame supported clear of the ground.

## Verify — done when…

- [ ] Protective film removed and the optic clean — no fingerprints on the sensor window.
- [ ] Sensor sits flat at ~10 mm, square to the floor, clear view through the aperture.
- [ ] Level converter in-line, sensor on the **3 V** side (no direct 3.3 V→5 V connection).
- [ ] Every lever-nut conductor survives a firm tug; colour order matches on both faces.
- [ ] Harness dressed clear of the sensor aperture and the drive pods.
- [ ] Module centred on the middle cross rail; its offset from the origin recorded for the
      odometry config ([naming-frames](../naming-frames.md)).
- [ ] OTOS shows up on the I²C scan.
