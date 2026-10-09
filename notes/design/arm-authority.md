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
| `FREE` | energized, zero stiffness — hand-guiding and kinesthetic teaching. How an operator engages it, and why it is the one mode where a dead stack drops the arm: [manual control](arm-manual-control.md) |

**Everything falls back to `HOLD`.** Lease expiry, source dropout, mode exit, CH5 to `OFF`, a
software abort — all land in `HOLD`, never `OFF`. That is the controlled stop, and making it the
default is what makes the rest legible. It also provides the control that does not exist today:
*stop what you are doing without dropping anything*.

**Mode and source are separate.** Mode says what kind of commanding is permitted; source says which
specific publisher holds the lease. Select-one with hand-off, per
[behavior API scope](behavior-api-scope.md) — trajectories are stateful, so a second writer
mid-trajectory is a collision, not a blend.

`FREE` has no implementation today — but it may be nearer than it looks: the controller is
`openarm_impedance_controller`, so zero stiffness with gravity compensation is plausibly a
parameter rather than a new capability. Worth checking before building anything. It is in the enumeration because the adapter below declares
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

## Reconciling the two lease models

The question was which of the two becomes the arbiter the other defers to. They are not competing
arbiters — **they arbitrate different things**, and the honest division is:

- **The firmware is the authority source and owns the physical layer** — motion power, and the
  drive and lift axes, which it can physically enforce.
- **`OwnershipRegistry` is the arm arbiter.** The firmware cannot reach the arms (CAN-FD on the
  Thor), and an arbiter that cannot act is fiction.

The seam is `[AUTH]`. Neither defers to the other wholesale.

### What the registry already is

`mj_manipulator/ownership.py` — **the upstream manipulation library's code**, reached through the
upstream teleop's fork. Thread-safe, with four test files:

| | |
|---|---|
| `OwnerKind` | `IDLE` / `TELEOP` / `TRAJECTORY` / `GRIPPER` |
| priority | `IDLE 0 < TRAJECTORY 1 < TELEOP 2 < GRIPPER 3` |
| `acquire` | succeeds only when the arm is `IDLE` |
| `preempt` | higher priority only; **raises `ValueError`** on equal or lower |
| `release` | the current owner only |
| abort | per-arm flags, plus `abort_all` / `clear_all` / `force_release_all` |

Per-arm granularity gives bimanual concurrency for free — teleop one arm while the other runs a
trajectory — which is a real requirement and more than the firmware's model offers.

`OwnerKind` is **not** our mode enumeration and must not be mapped onto it one-to-one. It answers
*who owns*; mode answers *what the arm is doing*, including power state, which `OwnerKind` has no
concept of. `TELEOP` ⟺ mode `TELEOP`, `TRAJECTORY` ⟺ mode `AUTO`, and `IDLE` covers **both**
`HOLD` and `OFF`. `FREE` has no `OwnerKind` at all. `GRIPPER` outranking `TELEOP` is a sub-action
of teleop, not a competing authority.

### What it is missing

**Expiry.** [Control authority](../../docs/control-authority.md) states it as part of the
primitive: *"Every lease expires. Expiry belongs to the primitive so that no future source can
forget it."* The registry has none — confirmed in the teleop's fork **and upstream**. A teleop process
that dies holding an arm holds it forever. Every other lease on this robot is fail-safe-by-omission
(the RC failsafe, `kRosCmdTimeoutMs`, the 250 ms `MAST CMD` heartbeat, `[AUTH]`), so this is the
one place our model is strictly better, and it is not optional.

**An authority precondition.** The registry has no concept of robot-level authority. It must
consume `[AUTH]`, refuse `acquire`/`preempt` when authority does not permit, and drive everything
to `HOLD` when authority drops or the line goes stale.

### A verified gap in abort recovery

`request_abort()` calls `abort_all()`, which sets flags but **retains ownership**. It also calls
`_deactivate_all_teleop()` → `controller.deactivate()`, which clears targets and state but does
**not** release. `force_release_all()` — whose own docstring says it is "used by the global
e-stop" — is reached only from `reset_state()`, a MuJoCo sim-reset helper. The CHANGELOG claims the
E-Stop system uses it; the `robot.py` path does not.

So after a global abort the arm is still owned, and re-engaging the same teleop cannot recover:
`acquire()` refuses because the arm is not `IDLE`, and `preempt()` on equal priority
(`TELEOP` → `TELEOP`) raises `ValueError`. `sim_context.py` documents exactly this failure class in
the sim-reset context — *"teleop ownership persists after deactivation and `_arm_preempted()` blocks
subsequent …"* — so the mechanism is known, just not wired to the abort path.

Present upstream too, so it is not a fork artifact. Checked at the fork's `de0ae13` and upstream's
`ab2d408`; the fork is **ahead of** upstream's default branch, not behind.

### Where the additions live

**Not in `ownership.py`.** It is an upstream safety primitive, and a fork of one is exactly the
thing that rots. Wrap it instead: expiry and the authority precondition go in a thin arm-authority
node of **ours**, which is the only caller of `acquire`/`preempt`/`release`. Upstream stays pristine
and swappable — the same boundary goal 4 needs — and the safety-critical additions sit in code we
control and can test without arms attached. If upstream gains expiry later, ours drops out.

The abort-recovery gap is worth reporting upstream rather than patching around, since the fix there
is a one-line change of `abort_all()` to `force_release_all()` in a path the upstream library's maintainers own.

## The bring-down — done, and the durability question answered

`openarm-up down` used to set the hardware `inactive` with no warning, which removes torque and
drops the arm from wherever it is. It now **refuses while either arm is active**, prints that arm's
motor positions, and points at `openarm-up park`.

Parking is a separate explicit step, not folded into `down`, because moving an arm on the way down is
exactly as hazardous as moving it on the way up — the same reason activation was already explicit.
The stow pose is configured in `/etc/opendubs/openarm-stow.conf`, **outside `/opt/opendubs`**,
because the deploy rsyncs `--delete` over `openarm/` and would eat it. Same lesson as the drive
calibration. It ships unset and `park` refuses rather than guessing, since a wrong pose drives the
arm into the robot's own head.

**Both arms on opendubs2 stow at zero**, verified 2026-10-02 by driving there, de-energizing and
measuring that the arm stayed — the right arm moved at most 0.04°. Zero is a *choice*, not a
discovery (see the roll joints above): it is defined, repeatable, and the pitch joints hang there,
which is what makes de-energizing at this pose a non-event.

`down` does not refuse an arm that is **already at its stow pose** — it compares the live joint
angles against the configured pose within `OPENARM_STOW_TOL_DEG` (5°, against a ~1° steady-state
error) and says so. Without that, a successful `park` was still followed by a refusal, so the normal
flow would always have needed `--drop` and the flag would have become reflex, which is precisely how
a guard stops being one.

One quirk of `openarm-motors` worth knowing: a **single read can transiently report a motor
`missing`** — seen immediately after a stack shutdown, with three consecutive reads straight
afterwards all clean. Poll rather than sampling once; the 8/8 activation gate does.

### Measured 2026-10-02: the motors hold, with torque, forever

`HOLD` **is** durable. The DAMIAO motors keep their last commanded position with full torque when
commands stop. There is **no command-timeout watchdog in the motors.**

The left elbow (joint 4, carrying the forearm, wrist and gripper) was commanded to 25° and held at
24.38° — sagging 0.6° below target, which is how we know it was genuinely loaded against gravity
rather than resting. Then `ros2_control_node` was `SIGKILL`ed, so no DISABLE frames were sent:

| | enabled, no controller, 18 s | on explicit DISABLE |
|---|---|---|
| joint 4 | `24.38 → 24.38` — **0.00° drift** | `24.38 → 18.20` — **6.2°** |

The second column is the control that makes the first meaningful: disabling moved the arm, so the
hold was torque, not friction. An earlier attempt at the same test on the **wrist** (joint 6) was
worthless and should not be repeated that way — it drifted only 0.9° when fully disabled, because a
wrist pitch carries nothing but the gripper, so the gravity torque is small beside the friction. It
would have "passed" whatever the motors did.

### Which joints gravity actually loads

Worth knowing before designing anything that moves these arms, and not a detail to rediscover:

- **Pitch joints — 2, 4, 6 — carry gravity.** Joint 4 is the one with real load behind it, since it
  carries the forearm, wrist and gripper. Test holding torque here.
- **Joints 3, 5 and 7 are ROLL joints about the limb axis**, so gravity exerts no moment about them
  at all. They stay wherever they were last commanded. This is why the right arm sat at
  `j3 -36.62°, j5 +30.94°` indefinitely, and it is **not** friction — the earlier reading of it as
  friction was wrong.

Two consequences. **"Where the arm naturally rests" is undefined for the rolls** — they do not
settle, so there is nothing to discover and a stow pose has to be *chosen* rather than measured.
And **driving a roll joint on a hanging arm is safe**: the 37° the right arm travelled to reach zero
was a roll, not a swing, so the arm never moved out of its hanging envelope. A 37° error on a
*pitch* joint would be a different matter.

**This is the inverse of every other lease on this robot.** Elsewhere silence is the stop — the RC
failsafe, `kRosCmdTimeoutMs`, the 250 ms `MAST CMD` heartbeat, `[AUTH]`. Here silence is a freeze:

- Good: `HOLD` as the universal fallback is physically realizable, and a crashed controller does
  **not** drop the arm.
- Bad: **fail-safe-by-omission does not exist at the motor level.** A stack that dies mid-trajectory
  leaves the arm energized and frozen at an arbitrary pose, indefinitely, drawing current. Nothing
  releases it but an explicit DISABLE, deactivating the hardware, or cutting power — the contactor
  and the e-stop.
- So an arm holding deliberately and an arm holding because its software died are
  **indistinguishable to an operator looking at it.** That is an annunciation requirement, not a
  nicety, and it belongs with the liveness field in
  [arm telemetry](arm-telemetry-and-viz.md).
- And recovery is its own hazard: **activation drives the arm to zero**, so restarting after a
  freeze sweeps from wherever it froze to zero. `openarm-up` now says so when it refuses.

Three `openarm-up` defects fell out of running this on hardware, all fixed:

- **`down` could leave the arms energized.** Against a dead controller_manager every
  `ros2 control` call waited out its full 30 s timeout, per arm, so `down` was killed before
  reaching the direct-DISABLE fallback that actually works. It now probes the controller_manager
  once and skips straight to the CAN path — 10 s instead of over 70.
- **The refusal guard was blind in exactly the dangerous case.** It read hardware state over ROS,
  which returns nothing when the stack is dead. It now also guards on motor status over CAN, which
  does not depend on the stack, and refuses to drop an arm that is energized and frozen.
- **A half-dead stack looked healthy.** `running()` tests for the *launch* process, which outlives
  a `SIGKILL`ed control node, so `start` refused as "already running" while `activate` failed with
  `rcl node's context is invalid`. That state is now named, with the fix (`down` first).

## Open

- **Report the abort-recovery gap upstream.** The mechanism and the evidence are in *A verified gap
  in abort recovery* above. `request_abort()` → `abort_all()` retains ownership, so re-engaging the
  same teleop afterwards cannot `acquire` (not `IDLE`) and cannot `preempt` (equal priority raises).
  The fix is in a path the upstream library's maintainers own — `abort_all()` → `force_release_all()` in `robot.py` — so it wants
  reporting to `personalrobotics/mj_manipulator` rather than patching in a fork. Verified at the fork's
  `de0ae13` and upstream's `ab2d408`. **Our wrapper must not depend on recovery working until this
  is resolved**: it should release leases itself on abort rather than trusting the registry to.
