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

## Simulation, 2026-10-21

The two controllers run closed loop in RI's MuJoCo backend with the public gafro (the right arm, from where it stands; the reference is a
pose). Starting values, one run each, simulation only:

| controller | hold, 2 s | +80 mm in z over 1.5 s |
|---|---|---|
| `free` (operational space, gravity through the model) | hand 8 mm, but a joint moved up to 0.41 rad in the nullspace | 80.0 mm, 0.5 mm from the goal |
| `impedance` (J^T w) | hand 1 mm | **18 mm of 80 in 3.5 s: too soft or too slow as configured** |

`free` tracks; its posture stage lets the redundant joints drift while the hand holds. The `impedance` gains (400 N/m, 40 N s/m) do not
move the arm as asked, and why was not investigated. Both need tuning on an arm; these runs say the config works, not that it is right.

## Next

1. **The arm service**: owns the `RobotInterface`, reads CH5 and `[AUTH]`, runs leases and presence, grants and renews the permit, runs
   the keeper (`auto_reengage` is DEC-28's policy, off until decided), and serves state to the tablet over a websocket or dora instead
   of `/joint_states` through rosbridge.
2. **The first supported run** and the measurements above.
3. **The teleop**: the WebXR bridge is ROS-based upstream and would need porting to produce references. ssik still does the IK.
