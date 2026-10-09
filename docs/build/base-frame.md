# 1 · Base Frame

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | The 432 × 624 mm goBILDA frame + the four drive pods (motor + miter gear + hub) |
    | **Time** | ~TODO |
    | **Difficulty** | Beginner |
    | **Prerequisites** | None — this is the first build |
    | **Mates to** | Mast, electronics tray, payload deck (later modules) |

<figure markdown="span">
  ![OpenDubs base frame — CAD, wheels shown for context only](../assets/build/base-frame/hero.png)
  <figcaption>Assembly shown with wheels attached for visualization purposes — it is recommended <strong>NOT</strong> to install the wheels until significantly later in the build.</figcaption>
</figure>

The base frame is the goBILDA aluminum chassis every other module mounts to. It
also **defines the robot's coordinate origin** (see
[Coordinate frames & naming](../naming-frames.md)), so get the orientation right
before anything else goes on.

<figure class="amap amap--base-frame" markdown="1">
  <div class="amap-frame" markdown="1">
--8<-- "amap-map.html"
  </div>
  <figcaption>Where the <strong>Base Frame</strong> sits in the lower frame — see the <a href="../lower-frame/">assembly overview</a>.</figcaption>
</figure>

## The goBILDA (ServoCity) system

This robot is built on **goBILDA** structural components (sold through
**ServoCity**). goBILDA is widely used in **FIRST Tech Challenge (FTC)** robotics,
so there's a deep catalog of parts, CAD, and tutorials to lean on.

!!! tip "goBILDA / ServoCity resources"
    - [gobilda.com](https://www.gobilda.com) — parts, CAD downloads, kits
    - [servocity.com](https://www.servocity.com) — same catalog plus user manuals
    - [Product Insight](https://www.servocity.com/product-insights) — goBILDA's guide to how the system assembles and the design patterns behind it
    - [Strafer Chassis Kit build guide (PDF)](https://www.servocity.com/content/user_manuals/3209-0012-0002_assembly-instructions.min.pdf) — the drivetrain in this build follows it

**Reading goBILDA part numbers.** Structural parts follow the pattern
`XXXX-YYYY-ZZZZ`:

| Field | Meaning |
|-------|---------|
| `XXXX` | part family — e.g. `1120` = U-channel |
| `YYYY` | for structure, the number of **large holes** |
| `ZZZZ` | length in **mm** |

For example, **`1120-0025-0624`** is a U-channel (`1120`) with **25 large holes**,
**624 mm** long — and this step uses **qty 2** of them for the long sides of the frame.

## CAD & 3D model

- **View in browser (Fusion):** <https://a360.co/4xbG4gZ>

!!! tip "The viewer is interactive — use it"
    The Fusion link isn't just a picture. Drag to orbit/zoom in 3D, and hit
    **Explode Model** (the box icon in the bottom toolbar) to pull the assembly
    apart and see how the pieces fit together — often the fastest way to understand
    a step before you pick up a tool.

<div class="embed-facade" markdown="1" style="--embed-aspect: 52%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811afb16efe62183c83?mode=embed" data-title="Basic Lower Frame — Fusion 3D viewer">
![Base frame CAD — click to open the interactive Fusion 3D viewer (orbit, zoom, explode)](../assets/build/base-frame/cad-explode.png)
<span class="embed-facade__play">▶ Open the interactive 3D model</span>
</div>

*Click the model above to load the live Fusion viewer in place — then orbit/zoom, and hit
**Explode Model** (the box icon in the bottom toolbar) to pull the assembly apart.*

- **Download:** export a STEP from the Fusion link into `hardware/` for offline reference. *(TODO: add exported STEP/STL)*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Part numbers are goBILDA SKUs — order from [servocity.com](https://www.servocity.com)
    (**educational discount** — see the [Hardware BOM](../hardware-bom.md)). Read them with
    the `XXXX-YYYY-ZZZZ` convention above. **This table is generated — edit the CSV, not the
    table.** Quantities are module totals (each drive assembly, ×4, takes 1 hub shaft, 2 miter
    gears, and so on — see the per-part notes).

!!! tip "Motor choice"
    We recommend the goBILDA **5303-series, 99.5:1, 24 mm** (8 mm REX, 180 RPM, encoder) — a
    good speed/torque mix for this robot — but **any 5303-series motor fits physically.**

!!! warning "Do not install the wheels yet"
    The mecanum wheels are handed (2 left + 2 right) and go on much later — order them now,
    set them aside.

<!-- BOM-AUTO:build:base-frame -->
**Frame structure**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [U-channel, 25-hole, 624 mm](https://www.gobilda.com/1120-series-u-channel-25-hole-624mm-length/) | `1120-0025-0624` | 2 | Frame long side rails |
| [U-channel, 17-hole, 432 mm](https://www.gobilda.com/1120-series-u-channel-17-hole-432mm-length/) | `1120-0017-0432` | 3 | Cross rails (three lower-rail assemblies) |
| [Quad block mount](https://www.servocity.com/1201-series-quad-block-pattern-mount-43-2/) | `1201-0043-0002` | 10 | 6 for lower rails + 4 for drive assemblies |
| [1118 Series Open goRAIL, 192 mm](https://www.servocity.com/1118-series-open-gorail-192mm-length/) | `1118-0024-0192` | 4 | 192 mm open goRAIL (T-slot extrusion); 4 per frame |

**Drive assembly (x4)**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Hub shaft](https://www.gobilda.com/shouldered-8mm-rex-hub-shaft-20mm-hub-offset/) | `2110-0608-0002` | 4 | 1 per drive assembly |
| [Bearing, 6 mm ID](https://www.gobilda.com/1611-series-flanged-ball-bearing-6mm-id-x-14mm-od-5mm-thickness-2-pack/) | `1611-0514-0006` | 4 | 1 per drive assembly |
| [Bearing, 8 mm](https://www.gobilda.com/1611-series-flanged-ball-bearing-8mm-id-x-14mm-od-5mm-thickness-2-pack/) | `1611-0514-0008` | 4 | 1 per drive assembly |
| [Miter gear, 8 mm REX](https://www.gobilda.com/clamping-steel-miter-gear-8mm-rex-bore-24-tooth-mod-1/) | `2320-4008-0024` | 8 | 2 per drive assembly; orientation sets wheel direction |
| [Spacer, 9 mm](https://www.gobilda.com/1522-series-8mm-id-spacer-10mm-od-9mm-length-4-pack/) | `1522-0010-0090` | 4 | 1 per drive assembly |
| [Shim, 6 mm ID](https://www.gobilda.com/2807-series-stainless-steel-shim-6mm-id-x-9mm-od-0-25mm-thickness-12-pack/) | `2807-0609-0250` | 4 | 1 per drive assembly |
| [Shim, 8 mm ID](https://www.gobilda.com/2807-series-stainless-steel-shim-8mm-id-x-11mm-od-0-50mm-thickness-12-pack/) | `2807-0811-0500` | 4 | 1 per drive assembly |
| [Washer, 4 mm](https://www.gobilda.com/2801-series-zinc-plated-steel-washer-4mm-id-x-8mm-od/) | `2801-0004-0008` | 4 | 1 per drive assembly |

**Drive motors**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [goBILDA 5303-series motor, 99.5:1, 24 mm](https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/) | `5303-series (99.5:1 / 24 mm)` | 4 | RECOMMENDED for this build (good speed/torque mix); ANY 5303-series motor fits physically. 1 per drive assembly; same motor class reused for the mast |

**Wheels**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Mecanum wheel, LEFT slant](https://www.gobilda.com/140mm-mecanum-wheel-set-70a-durometer-bearing-supported-rollers/) | `3213-3606-0003` | 2 | Handed; do NOT install until late in the build |
| [Mecanum wheel, RIGHT slant](https://www.gobilda.com/140mm-mecanum-wheel-set-70a-durometer-bearing-supported-rollers/) | `3213-3606-0003` | 2 | Handed; do NOT install until late in the build |
<!-- /BOM-AUTO:build:base-frame -->

Structural screws are bought in bulk, not itemized here — each drive assembly uses
~1 × M4 × 10 mm + 4 × M4 × 8 mm socket-head screws. See the fasteners on the
[Hardware BOM](../hardware-bom.md#tools-consumables).

## Tools

goBILDA hardware is **metric**, and the structural screws take a **3 mm hex**. You can
build the whole frame with a hand wrench; a powered driver just makes it much faster.

<!-- BOM-AUTO:shared:tools -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Safety glasses (PPE) | required | 1 | — | Wear during ALL assembly — cutting, wiring, battery handling |
| Work gloves (PPE) | required | 1 | — | Protect hands during assembly, wiring, and battery handling |
| 3 mm hex wrench (4-pack) | required | 1 | [buy](https://a.co/d/028aG2GM) | Minimum tool; builds the frame by hand (~$7) |
| Rechargeable hex driver (incl. 3 mm bit) | recommended | 1 | [buy](https://a.co/d/06aqx7Jc) | Much faster for the bulk of the screws |
| 2.5 mm hex wrench / bit | recommended | 1 | — | For M4 cap-head screws |
| 7 mm ratcheting wrench | optional | 1 | [buy](https://a.co/d/03lJ5n8P) | For nuts |
| Long 3 mm hex bits | optional | 1 | [buy](https://a.co/d/05i2Vbj9) | Reach screws in tight spots |
<!-- /BOM-AUTO:shared:tools -->

**Consumables**

<!-- BOM-AUTO:shared:consumables -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Blue removable threadlocker | recommended | 1 | [buy](https://a.co/d/0igs0Bmw) | Apply to structural screws; BLUE (removable), not red |
<!-- /BOM-AUTO:shared:consumables -->

!!! tip "Manual vs. powered — your choice"
    The ~$7 hex-wrench set is all you *strictly* need. If you're building more than one
    robot (or value your wrist), the rechargeable driver pays for itself fast — it ships
    with a 3 mm bit, and the long bits above extend its reach into tight corners.

!!! warning "Use BLUE threadlocker, not red"
    Blue is **removable** — you'll want to take things apart later. Red is permanent. A
    small drop on the threads is plenty; it keeps screws from backing out under vibration.

*(Third-party tool links are examples — any equivalent works.)*

## Assembly

!!! tip "Reuse: goBILDA Strafer Kit drivetrain"
    The drive pods here — **motor + miter gear + hub** — are assembled **identically
    to the goBILDA Strafer Kit**. If a step is unclear, the Strafer manual has the
    detailed exploded views:
    [Strafer Kit assembly instructions (PDF)](https://www.servocity.com/content/user_manuals/3209-0012-0002_assembly-instructions.min.pdf).

!!! note "Photos are from the first build"
    Every photo below is from the team's first base-frame build, so a few show the
    bench mid-mess or a part not yet final. They are accurate about **order and
    orientation**, which is what the steps below are for.

!!! warning "Build the loaded rails first, then the rectangle"
    It is tempting to bolt the four U-channels into a rectangle and drop the motors
    in afterwards. **Don't.** The motors and hub shafts live *inside* the channel,
    and once the rectangle is closed there is no room to swing a hex driver onto the
    motor-mount screws. Load each side rail first (Steps 2–3), then join them.

### Step 1 — Lay out the rails and hardware

The frame is four goBILDA U-channels: **2 × 624 mm** long side rails and
**432 mm** cross rails. Check the part numbers before you start — a 25-hole and a
17-hole channel look alike at a glance and are not interchangeable.

<figure markdown="span">
  ![A 624 mm goBILDA U-channel on the bench](../assets/build/base-frame/01-long-rail.webp)
  <figcaption>One of the two <strong>624 mm</strong> long side rails (<code>1120-0025-0624</code>) — 25 large holes. Each of these takes two motors.</figcaption>
</figure>

<figure markdown="span">
  ![A 432 mm cross rail with quad block mounts fitted at both ends](../assets/build/base-frame/02-cross-rail.webp)
  <figcaption>A <strong>432 mm</strong> cross rail (<code>1120-0017-0432</code>) with quad block mounts already fitted at both ends — these are what bolt the corners together.</figcaption>
</figure>

<figure markdown="span">
  ![Rails, goRAIL extrusion, motor leads and hand tools laid out on the bench](../assets/build/base-frame/06-parts-laid-out.webp)
  <figcaption>Everything staged before assembly: rails, a length of 1118-series open goRAIL, pre-tinned motor leads, and the 3 mm hex driver.</figcaption>
</figure>

### Step 2 — Mount the motors inside the side rails

Each **624 mm** side rail carries **two** 5303-series motors, seated *inside* the
channel and clamped by a goBILDA motor mount. The miter gear goes onto the motor's
8 mm REX output shaft before the mount is tightened.

<figure markdown="span">
  ![A motor seated inside the U-channel with its mount bolted to the channel face](../assets/build/base-frame/03-motor-in-channel.webp)
  <figcaption>Motor seated inside the channel, mount bolted through the channel face. The yellow-and-black tape is the team's own motor labelling, not a goBILDA part.</figcaption>
</figure>

<figure markdown="span">
  ![Close view of the miter gear on the motor output shaft next to the channel bearing](../assets/build/base-frame/04-motor-miter-gear.webp)
  <figcaption>The 8 mm REX miter gear (<code>2320-4008-0024</code>) on the motor shaft, lined up with the bearing in the channel wall that the hub shaft will drop into.</figcaption>
</figure>

<figure markdown="span">
  ![A full side rail with two motors mounted and their leads exiting the channel](../assets/build/base-frame/05-two-motors-per-rail.webp)
  <figcaption>A finished side rail — <strong>two motors per rail, four per robot</strong>. Note both leads exit toward the middle of the rail.</figcaption>
</figure>

!!! tip "Build the four pods consistently"
    Which way a pair of miter gears meshes decides which way that wheel turns. Build the
    four pods consistently — the left and right sides are mirror images of each other,
    not copies. You do not have to get this right by inspection: any wheel that ends up
    turning the wrong way is found during [drive bring-up](../drive-bringup.md) and
    corrected there in firmware. Nothing needs rebuilding.

### Step 3 — Close the channel and set the hub shaft

With the motor fixed, the second channel face goes on and the **hub shaft**
(`2110-0608-0002`) drops through the bearings so its miter gear meshes with the one
on the motor. Shims and the 9 mm spacer set the mesh depth.

<figure markdown="span">
  ![The second channel plate being lowered over the mounted motor](../assets/build/base-frame/07-closing-channel.webp)
  <figcaption>Closing the channel over the motor. Get the bearing in this face lined up with the hub-shaft bore before pushing it down.</figcaption>
</figure>

<figure markdown="span">
  ![The hub shaft installed vertically through the channel with its flange on top](../assets/build/base-frame/08-hub-shaft-set.webp)
  <figcaption>Hub shaft set through both bearings, flange up. This is the shaft the mecanum wheel eventually bolts to — the wheels stay off until much later.</figcaption>
</figure>

<figure markdown="span">
  ![Close view of the two miter gears meshed at ninety degrees inside the corner](../assets/build/base-frame/13-gear-mesh-detail.webp)
  <figcaption>The two miter gears correctly assembled and meshed through 90°, motor gear driving hub-shaft gear. Spin the shaft by hand — it should turn smoothly and evenly.</figcaption>
</figure>

### Step 4 — Join the rails into the rectangle

Now bolt the loaded side rails to the cross rails through the quad block mounts to
form the **432 × 624 mm** rectangle.

!!! tip "Mark which end is the front"
    The coordinate frame is measured from the **rear-most 432 mm rail**
    ([naming-frames](../naming-frames.md)). Mark the front end before you bolt the
    corners: the frame is symmetric, so once it is closed nothing tells you which end
    was which, and every module above it is positioned relative to that rail.

<figure markdown="span">
  ![A corner joint showing a quad block mount bolted between two rails](../assets/build/base-frame/09-corner-joint.webp)
  <figcaption>A corner: the quad block mount (<code>1201-0043-0002</code>) bridges the two channels, with a length of goRAIL running off it.</figcaption>
</figure>

<figure markdown="span">
  ![Two rails bolted into an L shape on the floor with motor leads loose](../assets/build/base-frame/10-two-rails-joined.webp)
  <figcaption>First corner closed. Working on the floor makes it much easier to keep the assembly flat than working on a bench.</figcaption>
</figure>

<figure markdown="span">
  ![Three sides of the frame joined, motors visible on the right-hand rail](../assets/build/base-frame/11-three-sides.webp)
  <figcaption>Side rails and cross rails bolted together through the quad block mounts.</figcaption>
</figure>

<figure markdown="span">
  ![The partly assembled frame stood on a box with goRAIL uprights attached](../assets/build/base-frame/14-frame-upright.webp)
  <figcaption>Frame propped up to get at the underside. The vertical goRAIL sections are the start of the <a href="upper-frame.md">upper frame</a>, added later.</figcaption>
</figure>

!!! tip "Check the diagonals, then torque"
    The quad block mounts hold each corner at ninety degrees, so the rectangle comes out
    square more or less by construction. Measuring both diagonals before you torque the
    corner screws is a cheap confirmation rather than a critical step — if they match,
    carry on.

### Step 5 — Route the motor leads

Bring each motor's power pair up through a hole in the channel and back toward where
the electronics bay will sit, so nothing is pinched or under tension.

<figure markdown="span">
  ![Motor leads passed up through a hole in the rail, motor part number visible](../assets/build/base-frame/15-leads-through-rail.webp)
  <figcaption>Leads brought up through the rail and routed back toward where the electronics bay will sit.</figcaption>
</figure>


## Verify — done when…

- [ ] Frame is square (equal diagonals) and the front/rear orientation is marked.
- [ ] All four drive pods spin freely by hand, no binding.
- [ ] Motor leads are routed back along the channel with slack, and the left-side leads
      are run across the frame and zip-tied, ready for the ESCs in the electronics bay.

## Reference — completed frame

The completed base frame, before any wheels or upper modules go on. The vertical
goRAIL stubs are the mast mount; the exposed hub-shaft flanges are where the mecanum
wheels attach much later in the build.

<figure markdown="span">
  ![The completed base frame on a white table, seen from above](../assets/build/base-frame/12a-completed-table.webp)
  <figcaption>Completed frame from above — 432 × 624 mm, all four corners closed.</figcaption>
</figure>

<figure markdown="span">
  ![The completed frame at a low angle showing the channel depth](../assets/build/base-frame/12b-completed-low-angle.webp)
  <figcaption>Low angle: the motors and hub shafts sit entirely inside the channel depth, which is what keeps the underside flat.</figcaption>
</figure>

<figure markdown="span">
  ![The completed frame from a three-quarter angle with motor leads visible](../assets/build/base-frame/12c-completed-three-quarter.webp)
  <figcaption>Three-quarter view. Motor leads gathered toward the centre, ready for the electronics bay.</figcaption>
</figure>

<figure markdown="span">
  ![Top-down view of the finished frame on the floor with goRAIL stubs at the corners](../assets/build/base-frame/16a-completed-top-down.webp)
  <figcaption>Top-down on the floor — the clearest look at the rectangle and the inner cross rail.</figcaption>
</figure>

<figure markdown="span">
  ![Second top-down view of the finished frame](../assets/build/base-frame/16b-completed-top-down-alt.webp)
  <figcaption>Same frame, other end toward camera. Use this one to check your own corner hardware against.</figcaption>
</figure>
