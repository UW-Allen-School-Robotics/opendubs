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
second ties the arm to ROS 2 and needs a C++ plugin. The third was chosen for loop quality and no ROS. It excludes the ROS stack from
the bus, since only one process can own the CAN interface, so the two cannot run together.

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
position mode would sag the same way (the driver sends no feedforward torque in position mode). Two ways round it, neither done: gravity
feedforward in the driver's position and hold frames, from the bound model (which also removes the hold's sag on the real arm), or engaging
by `track` with the ready pose as its reference and posture target. A joint-space torque controller with gravity was tried and moved the arm
at about 0.02 rad/s; it is not in the config, and why was not found.

## Next

1. **The arm service**: owns the `RobotInterface`, reads CH5 and `[AUTH]`, runs leases and presence, grants and renews the permit, runs
   the keeper (`auto_reengage` is DEC-28's policy, off until decided), and serves state to the tablet over a websocket or dora instead
   of `/joint_states` through rosbridge.
2. **The first supported run** and the measurements above.
3. **The teleop**: the WebXR bridge is ROS-based upstream and would need porting to produce references. ssik still does the IK.
