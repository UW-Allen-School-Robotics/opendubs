# Arm protections in three layers

**Status: design, 2026-10-21. Not built.** Written with a collaborator who maintains the gafro
geometric-algebra library. The project team agrees with the split. Numbers that set clearance or
trip thresholds go to the project team before they're used.

## The independent layer underneath: the StamPLC

The StamPLC is the robot's safety controller, and it stays the **independent** layer:

- **The e-stop:** its contactor removes power from the arms.
- **The deadman:** the RC deadman it reads stops motion when released.

Nothing below replaces or bypasses it. **Layers 1–3 are software limits.** They make the arms
well-behaved and catch mistakes. They are not safety-rated, and this note never calls them a safety
function.

## The three layers

| | Layer | What belongs here | Who owns the code |
|---|---|---|---|
| 1 | **Driver**: the OpenArm hardware interface (`openarm_hardware` in `enactic/openarm_ros2`) | Everything any OpenArm needs, whatever controller runs: limits on what is sent to a motor, what happens when commands stop or a motor goes quiet, the engage ramp, a permit to move | Upstream. Our changes go there as normal pull requests, carried meanwhile as overlays in `software/openarm/image/overlay/openarm_ros2/` |
| 2 | **Controller**: model-based (the impedance controller today, with gafro added) | Limits that need the arm's model: tool speed, wrench, the workspace, clearance to the robot's own body, a joint-limit objective. The OpenDubs geometry (head, lift) is supplied as data | The controller's repository, plus gafro |
| 3 | **OpenDubs arm service** | Authority and context, which are specific to this robot: CH5, `[AUTH]`, leases, presence, the keeper, the tablet, the pinch guard | This repository |

Layers 1 and 2 must work on any OpenArm. Anything about who may move what, and when, is layer 3.

## Today's code, layer by layer

### Layer 1: the driver

**At our pin, `c15982d`, the upstream driver does almost nothing protective.** It enables and
disables all motors, reads them, and writes each joint's MIT command (position, velocity,
feed-forward torque, gains). It has:
- no clamps;
- no handling of a stale command;
- no fault handling after activation.

**Ours, as an overlay** (`0001-activation-holds-measured-pose.patch`, plan A1, verified on the
robot):
- at activation, two reads; it refuses unless every motor answered, none is in error, and the reads
  agree;
- a zero-stiffness engage, ramped over 0.5 s;
- commands seeded with the measured pose, so activation never drives to zero.

**Outside the driver, in our tooling** (`software/openarm/openarm-up`): the per-arm motor check
after activation (`arm_nmotors`), the CAN link heal (A7), and the power-restored reset. These run
once per activation, not every cycle.

### Layer 2: the controller

`openarm_impedance_control` at `b5179993`. It **already holds most of the runtime protections**:

- **Goal validation:** every waypoint inside the joint limits (URDF ± margin) and the workspace box
  (z ≤ 1.30 m since #214); otherwise the goal is refused whole.
- **Runtime trips, each of which freezes the arm at its measured pose and aborts the goal:**
  - measured joint velocity over the URDF velocity limit;
  - TCP speed (`cartesian_velocity_limit`, off on opendubs2);
  - estimated joint torque over `joint_torque_limits`;
  - Cartesian wrench (not configured).
- **Also:** an effort clamp, and a 0.1 s timeout on a streamed command.

### Layer 3: the OpenDubs service

- `openarm-up`: activate, park, hold, down.
- `backend/openarm.py`: the tablet's arm operations and `effector_status()`.
- The teleop bridge: ownership registry, grip clutch, `webxr_teleop/authority.py`.
- The show runner's preflight: CH5 AUTO, the deadman, effectors.
- [`arm-authority.md`](arm-authority.md): the modes (`HOLD`, `TELEOP`, `AUTO`) and leases, A10
  (not built).
- The keeper, A8 (not built).
- [`pinch-guard-consent.md`](pinch-guard-consent.md).
- [`arm-degraded-mode.md`](arm-degraded-mode.md).

**Body-part leases plug in here.**

## What moves or is added, per layer

### Layer 1 (driver), new

1. **Runtime silent, disabled and fault handling.** If a motor stops answering, goes disabled, or
   reports a fault while active, the whole arm holds its measured pose and reports which motor.
   Today this is checked only at activation; a motor that answered once and then went quiet keeps
   its stale values (plan 0.22's lesson, A1's documented gap).
2. **Hold on a stale command.** If no controller has written for N cycles, command the measured pose
   at the current gains.
3. **Command clamps** from a per-robot limits file: position, velocity and torque. This is where a
   **J2 cap** lands, as a per-robot `upper_limit`. On opendubs2 the shoulder meets the head at
   J2 ≈ 182° with the elbow folded. The cap's value is a clearance number, so it goes to the project
   team. It stands in only until layer 2's live head clearance exists.
4. **A trip on measured velocity and a torque slew limit, in the driver as a backstop** to the
   controller's own trips. Their thresholds sit above the controller's, so the controller trips
   first and the driver's fires only if a controller misbehaves or isn't the impedance controller.
   They are enabled only after the no-nuisance check below.
5. **A permit lease:** see *One permission concept*.

### Layer 2 (controller), new: gafro admissible sets

- Tool speed, wrench, the workspace and **clearance to the head**, plus a joint-limit objective,
  replacing the box and the J2 cap.
- **Head clearance must follow the live neck pan and tilt, not a fixed shape.** Today only the
  dashboard knows them (the neck's replies, read in `backend/neck_follow.py`); nothing publishes them
  in ROS. A topic for the neck's state is a prerequisite, owned by the Tablet and head lane.
- **The head geometry in the description is still a placeholder** (the tablet head). Clearance
  numbers computed from it go to the project team before any limit is set from them.

### Layer 3: unchanged in kind

It issues leases, sets modes, and runs the tablet, the keeper and the pinch guard. It never enforces
a joint limit itself.

## One permission concept

The driver's **permit** and our planned **body-part leases** are the same mechanism at two levels.

- **A lease** is `(holder, parts, expiry)`. Layer 3 grants it per part (`arm_left`, `grip_left`, …):
  to the teleop, to a show, or to a person at the tablet.
- **The permit** is that lease as layer 1 enforces it. Layer 3 renews a per-arm permit while a lease
  on that arm is held. The driver accepts motion only while the permit is fresh; when it lapses, the
  arm holds its measured pose. That matches `arm-authority.md`'s rule that expiry belongs to the
  primitive, so no source can forget it.
- **One word in all three layers: lease.** "Permit" names only the driver's view of it.
- **How the permit reaches the driver is open.** A ros2_control hardware interface has no node of
  its own, so the likely route is a command interface written by a small permit controller. To be
  settled with the collaborator.

## No nuisance trips in shows

Before the driver's measured-velocity trip or torque slew limit is enabled on the robot:

1. Run every show through Choreography's clearance sweep and twin, recording each joint's peak
   velocity and the steepest torque-command change.
2. Set the thresholds above those peaks with margin. The thresholds go to the project team.
3. Ship each one **log-only** first, so it reports what it would have tripped on during real
   shows; then enable it.

The same applies to any layer-2 limit that could abort a show.

## Dependency and licence

- **gafro:** MPL-2.0 (`idiap/gafro`). Using it as a library in the arm image is fine. Changes to
  gafro's own files would have to be shared under MPL-2.0.
  - It's pinned by commit in `software/openarm/image/repos.lock` (DEC-27) and built into the arm image
    on the Thor (aarch64), like the rest of the stack.
- **Driver changes** go to `enactic/openarm_ros2` as normal pull requests from a branch, carried
  meanwhile as overlays. They are never pushed straight to the org fork's `opendubs` branch. Opening
  a pull request upstream is publishing, so each one waits for the project team's word.

## First code step: layer 1

In order, each its own upstreamable change:
1. Runtime silent, disabled and fault handling. It's the smallest, and it closes a known gap.
2. Hold on a stale command.
3. Clamps from a per-robot limits file (the J2 cap's value waits for the project team).
4. The measured-velocity trip and torque slew limit, log-only first.
5. The permit, after its transport is settled.

Building and testing needs a ROS 2 Jazzy host: the non-robot Thor, or the arm image's build, with a
virtual CAN bus for the no-hardware cases. Anything on opendubs2 waits for the project team's word.

## Open questions for the collaborator

1. The driver features described as existing (clamps, hold on a stale command, fault handling):
   which driver or version? They aren't in `enactic/openarm_ros2` at `c15982d`. If a newer upstream
   or your branch has them, we move the pin instead of writing them again.
2. Which gafro commit to pin, and does it build on ROS 2 Jazzy, aarch64?
3. The permit's transport into the hardware interface (above).
