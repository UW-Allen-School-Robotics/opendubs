# The arms on gafro — design

Status: **proposed, 2026-10-21; nothing here has run on a robot.** Written by the session that built the driver; the Arms lane owns it
from here. It does not amend [`arm-authority.md`](arm-authority.md), which stays the specification of who may move an arm; it is a
different way to reach the motors, and where the two meet it says so.

## What this is

The arms driven by gafro's controllers through [gafro_robot_interface](https://github.com/personalrobotics/gafro-robot-interface)
(RI), over a driver that owns the CAN bus ([openarm-driver](https://github.com/personalrobotics/openarm-driver), on enactic's
`openarm_can`), with the model in [openarm_assets](https://github.com/personalrobotics/openarm_assets). **No ROS and no ros2_control
in the arm path**: the loop is in one process, the controller computes the torque and the driver sends it as a MIT frame. That is the
point: loop quality, and torque commands from the controller without a DDS hop or the upstream controller's gains in the way.

It replaces the arm path of [`workplan-arms.md`](../workplan-arms.md) (the ROS container, the impedance controller, `openarm-up`'s
activation), not its safety model.

## The layers, and where each lives

The old controller was one safety layer doing several jobs. Driving torque directly removes it, so its jobs are split by whose fact each is.

| Layer | What | Where | Built |
|---|---|---|---|
| Hardware | e-stop and contactor | the robot | yes, unchanged |
| **Driver** | torque, velocity, position and slew clamps; trips on speed, torque, tracking and temperature; hold on a stale command, a fault, silence or a disabled motor; motion-free engage; the motors checked before they are enabled; a **permit** with an expiry | `openarm-driver` | yes; simulated bus only |
| **Model** | joint ranges, rated torque and velocity, the gains | `openarm_assets` | yes |
| **Controller** | model limits (`joint_torque: model`), tool speed, gravity, the stages | `controllers.yaml` | builds; the model-limit bound waits on gafro#51 |
| **This robot** | the head cap on J2 | the model, `description/openarm/gafro/` (`tools/arm_gafro_model.py`) | yes |
| | CAN names, per-robot direction and zero | `hardware.example.yaml` | template |
| **Authority** | CH5, `[AUTH]`, leases, presence, the keeper, the tablet | an arm service | **not built** |

The driver's permit is the hook for the authority layer: with `require_permit` the arm holds unless the service has granted a short
permit and keeps renewing it, so if the service stops, for any reason, the arm holds by itself. This is the lease of `arm-authority.md`
at the lowest software layer. The driver does not know who grants it or why.

## What the first runs refuse

Until the arm service exists nothing checks presence, CH5 or the deadman, so the scripts do, on a real bus (a `bus: can` group in the hardware config) and before
anything is enabled, since `RobotInterface.initialize()` already commands the motors. `software/openarm/gafro/guards.py`, tested by
`software/openarm/tests/test_gafro_guards.py` (the CAN listener against real frames on a socket pair, the dashboard on a local server, each guard
mutated to see the tests fail):

- **Another controller on the bus** (every stage): the stack's `ros2_control_node` running (the arms' container is visible to the host's `pgrep`), or a passive
  listen on each arm's interface, for half a second, for what only a controller or a tool that changes the motors sends: command frames to ids 0x001 to
  0x008 (MIT, enable, disable) and register writes (0x7FF with function 0x55 or 0xAA). A read-only status refresh (0x7FF, function 0xCC) and the motors'
  replies are not counted: something on opendubs2 polls both arms with refreshes every 2.2 s, whatever the arm stack is doing, and a poll asks the
  motors for their state and commands nothing. An interface that cannot be listened to is a refusal.
- **Motion** (`engage`, `--park`, `bringup.py lapse` and `move`): CH5 MANUAL or AUTO (DEC-21: STANDBY is hold and status only) and the e-stop clear, read from
  the dashboard's `GET /api/choreo/robot`, the read-only view the runner uses, and `--supervised`, which says a person is at the e-stop. A dashboard that does not
  answer, or an unknown CH5 or e-stop, is a refusal.
- **Where a move starts** (`engage`, `--park`): within 5 degrees per joint of stow (the stow file's pose) or ready. The straight-line check against the collision geometry
  uses a model with no head, neck or lift, and the head moves, so from anywhere else the line could pass through it unseen. Until the head is in the model,
  that is the rule; the maintainers' decision on the cap and the ready pose makes it a condition.

A `fake` bus and the simulator are not gated. The arm service replaces the second and third: it owns presence, the leases and the deadman.

**Ready is a working pose, not a resting one.** An e-stop or a power drop with an arm at ready lets the forearm fall, where stow moves nothing. Park (`engage.py --park`,
or `openarm-up park`) before leaving the robot. The maintainers approved the J2 hard cap of 157 degrees (182 measured contact less the 25 of a show) as a backstop that
matches what shows already obey, and the ready pose's 78 mm to the head as a working pose, on two conditions: the head, neck and lift go into the model before either figure
is relied on near a moving head, and ready is never where the arms are left. `tools/arm_gafro_model.py` derives the cap from `software/choreo/cue.py`'s `SHOULDER_*`, so
changing those moves the arm's hard cap.

**Pins.** `env.sh` builds the driver at f881bf4 and refuses another checkout, and uses openarm_can 1.4.1, the version the driver's CAN backend was built and run against. The
existing stack's image carries 1.4.0 (f340d4b), which the driver has not been tried against.

## What is not covered, stated

- **A dead driver process.** The motors never time out and keep their last frame, so a process killed in torque mode leaves that
  torque applied. The permit and the stale-command hold cover a live driver and a dead caller, not this. Measuring what the Damiao
  `TIMEOUT` register does, on a supported arm, comes first; a separate daemon that owns the bus is the alternative, at the cost of IPC in
  the torque loop. Until one is chosen the e-stop is the answer.
- **The first run on an arm.** Activation is built to be motion-free (it reads the pose, enables, and ramps the stiffness up from a
  fraction), but how the motors behave when enabled is the A1 check again, on this driver. Do it supported and with a person present.
- **The cap is in the model, not only the driver.** The driver clamps position targets, and does not limit torque by position, so on the
  torque path the cap has to be in what the controllers read: gafro's joint-limit guard takes each joint's own limits from the model.
  The limits cannot be set on a loaded model from Python (`System.set_joint_limits` changes the system's arrays, which the driver reads,
  but not the joints', which the guard reads), so the model is generated with joint 2 narrowed. That the guard then reacts is checked
  at the controller (`check.py`: 0.1 rad from the cap the joint 2 torque differs by -7 N m from the arm's own model, 0 far from it); a
  closed-loop run near the cap has not been done.
- **No workspace box, no head in a collision model.** gafro's spec has neither; see the README beside the config.
- **Every default** (gains, trips, slews, the permit's use) is a starting value.

## Why a driver and not the ROS stack

Three ways to send torque were weighed: gafro as a ROS 2 client of the existing stack (forward the efforts through
`forward_command_controller`), gafro's controller inside the ROS controller manager, and gafro owning the bus. The first adds a DDS hop
inside the torque loop, on a controller manager that already shows missed cycles at 400 Hz without realtime scheduling (item 2.5). The
second ties the arm to ROS 2 and needs a C++ plugin. The third was chosen for loop quality and no ROS. It must not run beside the ROS stack,
but nothing in the kernel makes sure of that: SocketCAN lets any number of processes send, so two controllers can command the same motors. The
first-run scripts refuse to start while the stack's `ros2_control_node` is running or another sender's command frames are on the bus (see
*What the first runs refuse*); the driver has no such check yet.

## Stow and ready

**Stow is not where a controller starts.** Stow is the zero pose, with the elbow straight: exactly singular (smallest Jacobian singular
value 0.000, against the guard's floor of 0.06). The model's own `ready` keyframe (J4 = 0.3, chosen only to stay off the joint's limit) is
barely better (0.027). A controller engaged from either has the guard swing the arm: J3 and J5 counter-rotate by about 0.75 rad while the hand
barely moves (9 mm), which is the guard doing its job, not a gain problem.

So the arm has a **ready pose**, separate from stow, which is where it is engaged and homed to. Right arm, model joint order (the left is the
mirror, joints 1 to 3 negated, per `sim/scene.py`): `[0.00, 0.70, -0.50, 1.80, -1.00, -0.10, -0.55]`. Elbow bent 103 deg, the hand at
(0.37, -0.19, 0.50) in front of the chest, the tool pointing forward.

| measure | value | against |
|---|---|---|
| smallest singular value | 0.128 | the guard's floor, 0.06 (2.1x) |
| smallest joint margin | 0.57 rad | the guard's margin, 0.35 |
| J2 margin to the head cap | 117 deg | |
| clearance to the head, at lift 0, 137 and 275 mm | 78 mm | a show leaves 30 mm; the twin is 6 deg optimistic (DEC-12) |
| between the arms | 188 mm | |

It was **picked** by sampling, with ssik through `self_motion_charts.bridge` (the OpenArm's ssik twin agrees with gafro's FK to 3e-15), the
self-motion manifold of 108 candidate hand poses in front of the chest and scoring every posture on it, then checking the best against the head
and each other in this robot's scene. `tools/arm_ready_pose.py pick` repeats that and `verify` checks the chosen pose (it rejects the arm's own
`ready`); `tools/arm_gafro_model.py` writes it into the model as the `ready` keyframe, and `check.py` checks the keyframe is that pose.

RI's MuJoCo backend starts in the model's first keyframe, so the simulated arm now begins in the ready pose.

## Simulation, 2026-10-21

The controllers run closed loop in RI's MuJoCo backend with the public gafro (the right arm, from the ready pose, the reference a pose).
Starting values, one run each, simulation only:

| controller | hold, 4 s | +80 mm in z over 1.5 s |
|---|---|---|
| `track` (operational space, gravity through the model) | hand 0.1 mm, joints at most 0.001 rad | 80.9 mm, 0.9 mm from the goal |
| `freedrive` (no stiffness, gravity, joint damping) | the arm floats: hand 5.6 mm, joints 0.011 rad of creep; with gravity 0 it falls 554 mm | (no reference to move) |

**The first `impedance` controller was removed.** It was a task-space spring-damper with J^T w output, and its `gravity: 9.81` did nothing: from the
ready pose the hold sagged 43.9 mm at gravity 0, 9.81 and 19.62 alike, and with four times the stiffness the hand sagged 12.5 mm but the
joints still wandered 0.64 rad, because an impedance controller has no null-space stage. It claimed a gravity compensation it did not have. A
compliant tracking controller would need that designed (gravity through an acceleration stage, a posture stage); `track` is operational-space,
as decided, and `freedrive` covers the zero-stiffness case.

An earlier `track` run from the model's default pose looked like drift (9 mm, 0.78 rad); that was the guard leaving a near-singular start, above.

**`go_to_keyframe` does not return.** From a displaced pose it brings the arm to within 0.057 rad of the ready keyframe and never finishes: RI's
position servo has no gravity feedforward, so it settles a few hundredths of a radian short, outside its stopping tolerance. The real arm's
position mode would sag the same way unless the driver feeds gravity forward, which it now does (hold, position and velocity frames, from the bound
model; `openarm-driver` f881bf4, tested on a fake bus whose load is the model's own torque: the hold sags 0.006 rad
against 0.1 to 0.4 rad without). In the simulation the position servo is MuJoCo's own actuator, not the driver, so `go_to_keyframe` there needs the model's gravity compensation
(`openarm_assets.assembly --gravcomp`): with it the move back to ready returns in 0.8 s within 0.01 rad, without it it never returns (0.13 to
0.27 rad short). That model cannot be the default: the torque controllers cancel gravity themselves, so on a weightless model `track` holds
nothing (0.75 rad of joint wander, 9.5 mm of hand). The committed model stays without gravity compensation, which is what the real arm is; use
the compensated one only for position-mode moves in the simulation. A joint-space torque controller with gravity was tried and moved the arm
at about 0.02 rad/s; it is not in the config, and why was not found.

**Impedance cannot hold station.** A wrench-output stage ignores gravity (the same drift with `gravity: 0`), and an acceleration posture stage
below it only acts in its null space: 0.33 rad of joint wander and 44 mm of hand sag in 4 s. With the posture stage above it the arm holds
(0.004 rad) but cannot move, because a full-joint posture stage takes every degree of freedom. So `track` (all acceleration stages) is the
controller; impedance needs a gravity term on wrench-output stages in gafro's control spec.

## Engage: stow to ready to `track`

`software/openarm/gafro/engage.py`, one arm per call. It reads the measured pose, refuses a target outside the joint limits, checks the straight
joint-space line to the model's `ready` keyframe against the collision geometry, runs the `engage` controller (a posture stage in joint space,
gravity from the model, the same torque path as `track`) along a minimum-jerk reference at 0.3 rad/s of the largest joint (11 s from stow), then starts
`track` anchored where the arm is, so the handover commands nothing new. It asks no permit of anything: the arm service calls it once an arm is
allowed to move.

In simulation, from stow, both arms: at `ready` within 0.003 and 0.005 rad, then 0.0 mm and 0.0001 rad of drift under `track` for 3 s. The line
stow to ready keeps every pair of parts as far apart as it is at its closer end (the closest, finger to wrist at 6 mm and link 1 to link 3 at 17 mm,
are the same at both ends). The refusal fires: 4 of 300 random single-arm targets were refused, for example the hand 17 mm from the torso. The rule is
relative, so a start or target already in contact is not flagged; the target here is `ready`, which is checked.

What it took to get there, since each cost a run:

- **The stiffness.** A posture PD at 6 rad/s stalls the wrist joints 0.75 rad short and at 20 rad/s 0.07 rad short: the model's joint friction
  (0.1 N m) is more than the torque a PD asks of a joint with an inertia of a few thousandths. 40 rad/s reaches `ready`. That is the model's friction;
  the real arm's is not measured, and 40 on a wrist with the real loop's latency is the first thing to watch. An integral term is the other way.
- **`JointReferenceChunk(start, period, knots)`: the second argument is the spacing of the knots, not the length of the chunk.** With the duration
  there, 41 knots take 41 times as long, and the arm crawled; this is also why the earlier joint-space torque controller "moved at about 0.02 rad/s".
- **A simulator started at stow does not move.** A model whose first keyframe is the zero pose holds the right arm rigid under both `engage` and
  `track` (plain MuJoCo with the same torque moves it, and the same model moves when the arm is first brought to stow by a position move). Not
  understood; the sequence is tested by placing the arm at stow with `go_to_joint_position`, which is also how a real arm gets there.
- **Both arms at once** (`--side both`): a `RobotInterface` runs one controller, and a second `control()` replaces the first (the right arm was
  dropped when the left was started), so both arms need one controller over both. A stage binds to one task space, so `openarm_assets` now
  declares a `bimanual` task space (both chains, both groups, 14 joints) and `engage` runs over it. In simulation, ready to stow and back with
  the same call: within 0.001 rad of ready, held under `engage` (0.0045 rad of settling in 3 s). It ends under `engage` and not `track`, because
  there is no `track` over both arms: the pose stage would have to bind to each arm's task space, which the spec cannot say. That is the next
  gafro piece for a bimanual controller; until then one arm is under `track` and the other is held by whatever holds an uncontrolled arm
  (on hardware the driver's hold with gravity feedforward; the simulator lets it fall).
- **Park** (`--park`) is the same move to the stow file's pose (`/etc/opendubs/openarm-stow.conf`, the file `openarm-up park` reads), refused when unset,
  ending under `engage` because `track` cannot start at a singular stow. Tested in simulation for the right arm: at stow within 0.009 rad. The
  file's degrees are read as the model's joint angles, which holds where the driver's direction and zero are the identity.
- **The gripper cannot be run in the simulator on this model.** RI's MuJoCo backend takes a non-joint-transmission actuator (a tendon over the
  fingers) as a gripper; the OpenArm model has two independent finger-joint actuators, so `get_gripper_names()` is empty. The driver has the
  gripper (ids 0x08 / 0x18, closure 0 to 1) and its per-robot calibration (`gripper_open_position`, `gripper_closed_position`, no default), now
  in `hardware.example.yaml` with `use_gripper: false`; the calibration is found on the robot. A coupled finger tendon in `openarm_assets` is
  what the simulator needs.
- **CI** (`.github/workflows/arms-gafro.yml`) runs `tools/arm_gafro_model.py --check` against `openarm_assets` pinned to a commit; `check.py` needs the
  published gafro, a C++ build, and is run by hand. Installing `openarm_assets` from git exposed a packaging bug (the vendored URDF, which
  `build_openarm` reads for its rated limits, was not in the package data); fixed in `openarm_assets`' `pyproject.toml`, and the pin has to be
  a commit that has it.
- The settle check is 0.05 rad: friction stalls a wrist 0.02 rad short in one direction, and `track` anchors where the arm is.

## Next

1. **The arm service**: owns the `RobotInterface`, reads CH5 and `[AUTH]`, runs leases and presence, grants and renews the permit, runs
   the keeper (`auto_reengage` is DEC-28's policy, off until decided), and serves state to the tablet over a websocket or dora instead
   of `/joint_states` through rosbridge.
2. **The first supported run** and the measurements above.
3. **The teleop**: the WebXR bridge is ROS-based upstream and would need porting to produce references. ssik still does the IK.
