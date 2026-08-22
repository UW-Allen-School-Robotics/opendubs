# Design philosophy

OpenDubs is a **super-modular, open mobile-manipulation base** meant to be **remixed** by the
community — not a fixed product. The goal is that someone can take the base, bolt on their own
payload, arm, or sensor suite, and have it work without reverse-engineering the robot.

## Interfaces, not modules

Modularity doesn't come from having separate parts — it comes from **stable, published
interfaces** that parts plug into. The assembly guide gives you swappable modules; these
interface contracts are what let a *stranger's* module or arm drop in:

- **[Mechanical mount](interfaces/mechanical-mount.md)** — how anything bolts to the base
  (top-deck goBILDA pattern grid + the opengrid cladding ecosystem).
- **[Power & data](interfaces/power-data.md)** — the rails, current budget, connectors, and
  comms options a payload can draw on.
- **[Command & telemetry](interfaces/command-telemetry.md)** — the failsafe-gated command +
  telemetry contract that *every* control source speaks (RC, on-robot touchscreen, WiFi
  tablet, or an autonomy/LeRobot policy).
- **[Coordinate frames & naming](naming-frames.md)** — REP-103 and the named TF frames, so a
  mounted arm knows where it is.

If your addition honors those four, it works. That's the whole contract.

## Why goBILDA and 3D printing

The structure is **goBILDA** — an aluminum building system of extruded channel, pattern-hole
plates, hubs, and shafts (sold through **ServoCity**) — combined with **3D-printed** brackets and
enclosures for the parts that don't need metal. We chose this pairing deliberately:

- **Modular, no fabrication machinery.** goBILDA parts bolt together on a regular hole grid into
  rigid frames — no milling, welding, or lathe work. A hobby 3D printer covers the custom
  brackets. The whole robot is buildable with hand tools and a printer.
- **A large, proven ecosystem.** goBILDA is the standard structural kit for **FIRST Tech
  Challenge (FTC)** and is widely used across FIRST Robotics — so it's battle-tested by thousands
  of teams, and there's a deep catalog of parts that all fit together.
- **Abundant resources.** Because of that adoption, there's CAD, tutorials, and community
  know-how for almost anything you'd want to build — you're rarely the first to try it.

!!! tip "goBILDA Product Insight"
    goBILDA's [**Product Insight**](https://www.servocity.com/product-insights) is an excellent
    technical reference — it walks through the many ways the system assembles and the design
    patterns behind it. Worth reading before you extend the structure. Build details and how to
    read goBILDA part numbers are on the [Base Frame](build/base-frame.md) page.

## The drive system

The baseline is **mecanum drive** — four independently-driven mecanum wheels. Mecanum lets the
robot **strafe in any direction and rotate in place**, which is ideal for a mobile-manipulation
base working in tight indoor spaces.

**It's not locked to mecanum.** The goBILDA hubs use a standard bolt/shaft pattern, so you can
swap the mecanum wheels for other goBILDA-compatible wheels — including
[all-terrain tires](https://www.servocity.com/all-terrain-wheels-tires/) — and reconfigure the
mixer for **tank / 4WD** drive. The base is meant to be reconfigured, not to lock you in.

**Why not swerve?** Swerve gives independent steer + drive at each corner, but swerve modules
typically cost **2–3× per wheel** versus a fixed drive, and add real mechanical and control
complexity. For the baseline we chose the simpler, cheaper mecanum system; swerve is a great
community extension (below), not a baseline requirement.

### Load limits — where the drivetrain gives out

The goBILDA **8 mm REX** shaft-and-hub drivetrain is expected to be **good enough for most
OpenDubs builds**, but it's worth understanding where it gives out.

**Static loading is comfortable.** Take a loaded baseline robot at ~25 kg (frame + Thor + a 52 V
LiFePO4 pack + electronics — the battery and payload dominate the mass). Spread over four wheels
that's about **25 kg × 9.81 ÷ 4 ≈ 61 N (~6 kgf) per wheel** of vertical load, well within what
the 8 mm REX shafts, hubs, and bearings carry statically. Even a heavy top-deck payload keeps the
*static* margins healthy. *(Estimate — confirm against your actual mass.)*

**Dynamic loading is the real limit.** The failure cases aren't standing still — they're the
transients:

- **Hard / dynamic braking** and direction slams drive a **torque spike** through the drivetrain.
  The weakest links there are the **miter gears** (the 90° turn at each wheel) and the
  **planetary gearsets inside the motors** — shock-loading strips gear teeth long before the frame
  cares.
- **Off-roading and impacts** shock-load the **bearings, hubs, and mecanum rollers**. Mecanum
  wheels especially have small rollers that don't like curbs, drops, or debris.
- **Overloading the chassis** (a payload well beyond the baseline) pushes the *static* load toward
  the hub/bearing limits and makes every dynamic event worse.

**Rule of thumb:** drive it like a lab robot — smooth accel/decel, indoor floors, sane payloads —
and the 8 mm REX system is fine. Treat it like an off-road RC truck — hard braking, jumps,
overloading — and expect to damage **hubs, bearings, and mecanum wheels** (static/impact) or
**miter and planetary gears** (dynamic torque) first.

### Contribute a drive system

We plan to — and encourage the community to — develop **alternative drive systems**: off-road
patterns, higher-load drivetrains, and **swerve** modules. If you build one, contribute it back.

A heavier or more demanding design might step up to goBILDA's **12 mm REX** drive system, whose
hex drive is compatible with **12 mm RC-style wheels and hubs** — a stronger shaft/hub interface
with a broader selection of off-road tires.

## Where OpenDubs fits in the ecosystem

Projects like [LeRobot](https://github.com/huggingface/lerobot) (and the ~$300 SO-101 arm) have
made open, low-cost manipulation widely accessible. OpenDubs is a different piece of that
stack — a **mobile base** with autonomy compute, power, and safety, plus a Z-axis and arm
mounts. It costs more because it does more, and the intent is to **complement** that ecosystem,
not compete with it.

In practice, "fitting in" means a manipulator is treated as a **payload, not a fixed part of the
robot**; ROS 2 is the integration substrate; and the base is exposed as a controllable robot
(action / observation) so teleoperation, dataset recording, and learned policies apply the same
way they do to a tabletop arm.

**Payload classes we design for:**
- **Top-deck payloads** — anything on the goBILDA pattern grid (sensors, compute, bins).
- **Z-axis options** — a lift/mast to raise a manipulator's workspace.
- **Arms** — [OpenArm](https://github.com/enactic/OpenArm), [YAM](https://i2rt.com/pages/product),
  low-cost arms (SO-101), and COTS arms (Universal Robots, Franka) via the mount + power/data +
  ROS 2 interfaces.

## The remix ethos

Open CAD (Fusion + STLs), a single-source-of-truth [BOM](hardware-bom.md), permissive
licensing, and honest module boundaries. To extend the robot, you don't fork the whole thing —
you build to an interface and publish your module. That's the point.

!!! note "Living document"
    This captures direction that's still being refined. The interface specs below are the
    contracts we hold ourselves and remixers to — help us tighten them.
