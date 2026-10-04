# Moving an arm by hand: freedrive, frames, and click-to-reach

How an operator positions an arm *without* a headset. Teleop is the primary interaction and is
covered in [arm telemetry and visualization](arm-telemetry-and-viz.md); this is the rest — the
controls the dashboard needs for the ordinary case of "put that there".

The vocabulary is borrowed deliberately from industrial teach pendants, because operators arrive
already knowing it. UR's Move tab is **3D view · Feature and Tool Position · Move Tool · Move
Joints · Freedrive**, with Feature selectable as Base / Tool / View / custom
([jogging guide](https://www.universal-robots.com/articles/ur/programming/how-to-jog-the-robot/),
[custom feature frames](https://www.universal-robots.com/articles/ur/ur-polyscope-move-with-respect-to-a-custom-featureframe/)).
Prompted by [moveit#2899](https://github.com/moveit/moveit/issues/2899), which asks for the same
thing in RViz.

**What NOT to borrow: the programming model.** A pendant exists to *teach a program* — waypoints,
a program tree, move types. We *operate and supervise*. Copying it wholesale buys a program tree
nobody asked for while missing what this robot actually needs: teleop session health, the arms'
relationship to a **moving** base, and state a bystander can read.

---

## 1 · Freedrive (the `FREE` mode)

Zero stiffness with gravity compensation, so the arm is pushed into place by hand. It is a mode in
[arm authority](arm-authority.md); this is how an operator engages it.

**It may already exist.** The controller is `openarm_impedance_controller`, so
zero-stiffness-plus-gravity-comp is plausibly a parameter change rather than a new capability.
Check before building anything.

### Press-and-hold does not survive the move from pendant to tablet

UR holds the button because the pendant is in the operator's hand. Freedrive's entire purpose is
that **both hands are on the arm**, so an on-screen press-and-hold is self-defeating. On-screen
freedrive is therefore **latched**, which is a forced divergence from the pendant pattern rather
than a shortcut — and a latched mode that looks like a button is how people get surprised, so its
active state has to be unmissable.

### Freedrive is the one mode where "the motors hold forever" stops protecting us

Measured 2026-10-02: the motors keep their last command indefinitely, with no timeout. That is why
a dead stack *freezes* an arm instead of dropping it. But in zero stiffness the last command **is**
near-zero torque:

| mode | if the stack dies | result |
|---|---|---|
| `HOLD` / `TELEOP` / `AUTO` | motors hold last command | arm freezes in place |
| **`FREE`** | motors hold last command | **arm stays limp and falls** |

So an automatic exit is a requirement, not a nicety. And even while healthy the arm **sags**: the
~1° steady-state error from imperfect gravity compensation is not zero.

### Settled

- **Per arm.** Freedriving both at once, with two hands on one of them, is a way to drop the other.
- **Requires CH5 in `MANUAL`.** A human physically moving an arm is manual commanding; it must
  refuse outright in `AUTO`.
- **Ends on:** the lease expiring, ~60 s without joint motion, or any other arm command.
  Freedrive is the first real consumer of the lease expiry the wrapper adds to
  `OwnershipRegistry`, which has none of its own.

### What "it then holds" actually requires

**It holds WHERE IT IS**, and that is not automatic. The transition must:

1. read **measured** joint positions,
2. make those the hold target,
3. **ramp** stiffness up rather than stepping it.

Raise stiffness against the target the controller held *before* freedrive began and the arm snaps
back to a pose from thirty seconds ago, with the operator's hands on it. It holds in place rather
than returning to stow, for the same reason activation-to-zero is hazardous from an arbitrary pose.

**And one case where "it holds" is a lie.** If the lease ended *because the stack died*, nothing is
left to execute that transition and the arm goes limp. That cannot be fixed above the motors. It
means freedrive is the mode where **the operator's hands are the safety system** — true of every
compliant robot, but worth stating rather than discovering.

### The physical button is cheaper than it looks

A cuff button (Baxter's answer) is genuinely new hardware. A **body-mounted** one is not: the
StamPLC has spare digital inputs — DI0 is the e-stop chain, DI1 the contactor weld, leaving
DI2–DI7 — and `[AUTH]` is already the path from StamPLC state to the Thor. A button, two wires,
and one more field in a telemetry line that already exists. The arm is light enough to hold
one-handed, so body-mounted is workable; a cuff is better ergonomics for later.

---

## 2 · The frame selector

**Jogging is meaningless until you say in which frame.** "10 cm forward" in the world and 10 cm
along the gripper's approach axis are different motions, and the gap between them is where people
crash arms. This is the single idea most worth taking from the pendants.

But UR and xArm are bolted to a floor, so their "Base" is unambiguous. **Ours drives**, which
forces a distinction they never have to make, and bimanual adds one they have no use for:

| frame | meaning | who else has it |
|---|---|---|
| `base` | the robot's own body — moves when the robot moves | UR, xArm |
| `tool` | the gripper's own axes; approach along the jaw | UR, xArm |
| `odom` / world | fixed in the room, **diverges from `base` the moment anyone drives** | neither — they cannot move |
| **other arm's tool** | arm A relative to arm B — the handover primitive | neither — single-arm |

The third row is the one that will bite: on a mobile manipulator "base" and "world" agree only
while stationary, and a frame selector that omits the distinction is actively misleading.

---

## 3 · Click-to-reach, which may beat jogging entirely

Hello Robot's Stretch uses **click-in-the-camera-image to reach** instead of axis jogging. It needs
no frame selector at all, because the operator points at the thing rather than describing a
displacement — and we have a RealSense on the head, so a click plus its depth is a 3D point
directly.

For a mobile manipulator whose primary interaction is already teleop, this is plausibly a better
fit than a pendant-style jog panel: an operator wanting to grasp something cares about the object,
not about which axis moves them toward it.

**Prototype it against jogging rather than after it.** If click-to-reach works, the jog panel's
real gift reduces to the frame selector and the held-freedrive semantics — not the ± buttons.

Open where it gets hard, and none of it is UI: the reachability check (the click may be outside the
envelope), the approach direction (a point is not a pose), and confirmation before motion — a
single tap that sends an arm somewhere is exactly the affordance
[arm authority](arm-authority.md) argues against.

---

## Not now

**No jog panel yet.** Teleop is the primary interaction and is not yet good — the loop was missing
23% of its deadlines until 2026-10-03. Build Cartesian control when something needs it, and bring
the frame concept in the moment any Cartesian control appears, because retrofitting frames to a
control that assumed one is how the misleading version ships.
