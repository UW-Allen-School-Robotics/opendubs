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
