# Arm authority and modes

Settles who may command the arms in **normal operation**, how an operator sees and changes that,
and where a different arm vendor is swapped in. The e-stop case is not in scope and is not
negotiable: the arms sit on the drive bus, so an e-stop de-energizes them like every other driven
element.

The drive has an authority model ([Control authority](../../docs/control-authority.md)). The arms
have had none — the whole mode surface was `openarm-up activate left|right|both`, and once an arm
was active anything publishing to its controller moved it.

## What already exists

Most of the arm-side mechanism is built, in `EricJGoossen/openarm` (reached from
`openarm_robot-code`'s `setup.sh` as a sibling clone, not a submodule — there is no `.gitmodules`).
Worth knowing before building anything:

| Mechanism | Where | What it does |
|---|---|---|
| **Ownership registry** | `robot.py:125`, `ownership.abort_all()` / `clear_all()` | a per-arm lease, with a global abort |
| **Grip clutch** | `webxr_teleop/webxr_teleop.py:163` | engage on grip press, capturing the engage-time EE and controller poses; **release disengages**. Motion is relative to the engage point |
| **Tracking expiry** | `webxr_teleop/bridge.py:53` (`0.5 s`), `static/index.html:258` (`400 ms`) | a side whose pose stops arriving reports untracked |
| **Global abort** | `robot.py:692` `request_abort()` | aborts all arms via the registry **and deactivates teleop, so the operator must re-engage** |
| **Streaming + trajectory** | `common/ros_helpers.py:77`; `FollowJointTrajectory` | two distinct command paths into the impedance controller |
| **Staged validation** | `hw_validation/stage1..8` | including `stage1_estop_verification` and `stage5_abort_and_estop` |

So the **deadman decision below is already implemented**, in two layers: a voluntary clutch (grip)
and an involuntary one (tracking expiry). Nothing needs inventing there.

**What does not exist** is any connection between this and the robot. `request_abort()` is a Python
flag set from a REPL or Viser; it logs `"E-Stop activated"` but has nothing to do with the e-stop,
the contactor, or CH5. That seam is the work.

**A naming collision worth fixing:** `request_abort()` logging `E-Stop activated` will mislead
anyone reading a log next to a robot that has actual e-stop mushrooms. It is a software abort.
Rename at the integration seam.

## Decisions

### CH5 is the arm authority too

Not a new concept — an honest reading of the existing one. CH5 answers *may autonomy move this
robot*, not *may autonomy move the wheels*.

| CH5 | Arms |
|---|---|
| `OFF` | no source may command |
| `MANUAL` | a **human** may command — a teleoperator is a human commanding |
| `AUTO` | a planner or policy may command |

WebXR teleop is MANUAL. A learned policy or a cuMotion plan is AUTO. No new switch, and the
question that must never be ambiguous stays on one physical control.

**CH5 `OFF` means HOLD, not de-energize.** Authority off is "nobody may command", not "cut power".
If `OFF` dropped the arms they would be dropped as a matter of routine procedure, dozens of times a
day. De-energizing stays an explicit action and an e-stop consequence.

**The arms are not in CH7.** CH7 multiplexes the right stick, and a 7-DOF arm is not stick-driven.

### The teleop session's own engagement is the deadman

CH6 is **not** required for arm motion. The teleoperator is in a headset, not holding the
transmitter, so requiring CH6 would mean a second person holding it — a spotter model that is
defensible for early bring-up and unusable for routine work. A deadman held by someone who cannot
see what the teleoperator sees is not a safety device.

The clutch and the tracking expiry already satisfy the property that matters: motion requires a
live, deliberately engaged human, and **omission stops it** — the same fail-safe-by-omission as
`kRosCmdTimeoutMs` and the 250 ms `MAST CMD` heartbeat.

### Modes

| Mode | Meaning |
|---|---|
| `OFF` | motors disabled, limp. The power-on state |
| `HOLD` | energized, holding position, accepting nothing |
| `TELEOP` | a teleop source holds the lease |
| `AUTO` | a planner or policy holds the lease |
| `FREE` | energized, zero stiffness — hand-guiding and kinesthetic teaching |

**Everything falls back to `HOLD`.** Lease expiry, source dropout, mode exit, CH5 to `OFF`, a
software abort — all land in `HOLD`, never `OFF`. That is the controlled stop, and making it the
default is what makes the rest legible. It also provides the control that does not exist today:
*stop what you are doing without dropping anything*.

**Mode and source are separate.** Mode says what kind of commanding is permitted; source says which
specific publisher holds the lease. Select-one with hand-off, per
[behavior API scope](behavior-api-scope.md) — trajectories are stateful, so a second writer
mid-trajectory is a collision, not a blend.

`FREE` has no implementation today: the arms run an onboard P+D with gravity compensation and
nothing exposes a zero-stiffness mode. It is in the enumeration because the adapter below declares
which modes it honours, and adding a mode to that contract later is more disruptive than carrying
one that reports unsupported.

## The adapter contract

This enumeration is deliberately vendor-free — `OFF`/`HOLD`/`TELEOP`/`AUTO`/`FREE` are meaningful
for an xArm or a Franka. The OpenArms are today's default arm set, not the only possible one, so
everything vendor-specific lives behind one adapter, which declares:

1. how to command motion — **both paths**: streaming for teleop, trajectory for planned motion.
   These are different contracts and conflating them is how a stack ends up unable to swap either
2. what its safe stop is
3. which modes it honours

This is also the boundary at which SSIK is replaced by something else, so goals 3 and 4 are one
piece of work rather than two. `FollowJointTrajectory` already covers the planned-motion path and
is what cuMotion speaks; the streaming path has no agreed contract yet.

## Operator surface

Per-arm, on the tablet — left and right are independently useful, one teleoperated while the other
holds a fixture being a real case. Each arm shows mode, lease holder and liveness. One-tap `HOLD`
per arm, and **both to `HOLD`** as the primary control.

This also closes the annunciation gap left open in
[Control authority](../../docs/control-authority.md): the operator's eyes are on the machine, and
an arm's mode is currently invisible.

## The authority broadcast — done

The StamPLC publishes `[AUTH]` every 100 ms on the same tick as `[DRIVE]`
(`rc_serial_probe/src/main.cpp`, `publishAuth()`), carrying the conditions **separately** rather
than the AND it computes for the wheels. Contract and field table:
[Command & telemetry](../../docs/interfaces/command-telemetry.md).

Two things made this more than a printf:

- **`computeRcMode()` could not answer it.** It short-circuits — `local_estop_active`, a dead link
  and failsafe each return before CH5 is read — so its enum can never report the authority switch
  independently of link health. `publishAuth()` reads CH5 on its own.
- **A stale switch position is worse than an absent one.** `rcRaw()` holds the last frame, so
  `ch5` reports `unknown` whenever the link is untrustworthy and `deadman` reports `0`. Same
  discipline as `drive_bridge.py` publishing no odometry when `[ODO]` reports `ok=0`.

While documenting it, `docs/interfaces/command-telemetry.md` was found asserting *"The arms are not
on that bus"*. They are, and an e-stop de-energizes them; corrected in the same change.

## Open

- **Reconciling two lease models.** Eric's ownership registry and the firmware's lease model were
  designed independently. One of them has to become the arbiter the other defers to.
- **Whether `HOLD` survives a container restart.** `openarm-up down` makes the arms limp. A mode
  model whose fallback is `HOLD` needs a bring-down that does not drop the arms.
