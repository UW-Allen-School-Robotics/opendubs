# The arms' three kinematic models — do they agree?

Desk check, 2026-10-10, approved by the user after #154. Three models of the same V1 arms are in use:

| Model | From | What uses it |
|---|---|---|
| **ROS description** | enactic `openarm_description` `14ff67b`, `openarm_v1.0/urdf/openarm_v10.urdf.xacro`, expanded with the arm launch's exact mappings (`arm_type: openarm_v1.0`, bimanual, CAN interface names) | `ros2_control` and the impedance controller: its gravity model and its Cartesian goal limits (pinocchio from `robot_description`) |
| **V1 URDF** | `openarm_assets` `31eebb9`, `models/openarm/urdf/example/v1.urdf` | building the SSIK solver modules (`ssik build … --base openarm_<side>_link0 --ee openarm_<side>_hand_tcp`) |
| **MuJoCo model** | `openarm_assets` `31eebb9`, the decorated bimanual MJCF | the teleop: the arm model, collision checks, and the end-effector site that targets are given for |

**The ROS side uses the V1 description, not v2.0.** An earlier note (#154, Q11) said it uses v2.0. That
was inferred from the student fork's one commit, which touches only the v2.0 xacro. The launch file
says otherwise. Two consequences:
- **That commit is not used by anything the robot runs.** The V1 xacro already takes
  `left_can_interface` / `right_can_interface` at `14ff67b`.
- **So the Q11 overlay changes nothing at runtime.** Carrying it is harmless, and dropping it would be
  just as harmless.

## Method

- **Joint by joint**, between the two URDFs: parent and child links, origin xyz/rpy, axis, and limits
  (lower, upper, velocity, effort). Pure XML, compared to 1e-6.
- **Forward kinematics** from `link0` to `hand_tcp`, both arms, with pinocchio. 1000 random
  configurations inside the tighter of the two models' limits.
- **The MuJoCo model against the V1 URDF**: over 500 random configurations, the end-effector site's
  pose expressed in the V1 URDF's `hand_tcp` frame. If the two arm chains agree, that pose is
  constant.

The scripts are in the session's scratch area. They need `xacro`, `pin` and `mujoco` from PyPI, and no
ROS.

## Results

**The arm chains agree exactly**, in all three models:
- **The two URDFs:** all 7 joints per arm have identical origins, axes and limits, with the same
  parent and child links. FK orientation from `link0` to `hand_tcp` agrees to 0.0000° everywhere.
- **The MuJoCo model:** the end-effector site's pose in the V1 URDF's flange frame varies by
  0.0000 mm and 0.0000° over 500 configurations, on both arms. The chains match link for link.
- **The arm bases** (`link0` in the world) are identical in both URDFs.

**The tool frames do not agree.** Each model puts its end effector somewhere else:

| Model | Tool frame, relative to the `link7` flange |
|---|---|
| V1 URDF, `hand_tcp` | **at the flange** (zero offset). The fingers hang off `link7` at 102.5 mm |
| MuJoCo, `openarm_<side>_ee_site` | **(0, −4.5, 180.1) mm** — between the fingertips |
| ROS description, `hand_tcp` | **(0, 0, 186.0) mm** — `link7 → hand` 102.5 mm, `hand → hand_tcp` 83.5 mm |

There is one joint-limit difference, the same on both arms: **J4's lower limit is −2.9° in the MuJoCo
model and 0° in both URDFs**. `openarm_assets` widens it on purpose (`widen_joint4_lower_limit`), so
a hanging elbow that reads slightly negative isn't out of range in the model.

There are also small differences outside the arm chain:
- The finger joints sit 5 mm off-centre in the ROS description and on the axis in the V1 URDF.
- The ROS description has an extra `hand` link between `link7` and the fingers.

## Do they matter?

**For IK in the teleop: no.** `mj_manipulator`'s SSIK adapter computes one fixed transform between
the solver's end-effector frame (the V1 URDF's flange) and the MuJoCo end-effector site. It computes
it at start-up, and applies it to every target. That is exact only when the two chains are rigidly
related, and the check shows they are. So a target given for the MuJoCo site reaches the solver
correctly, whatever the URDF calls `hand_tcp`.

**For the impedance controller: at most a few millimetres.** Its Cartesian goal limits check the ROS
`hand_tcp`, which sits 7.4 mm from the teleop's site (5.9 mm along the tool axis, 4.5 mm across it).
Against a workspace box of about 1.2 m, that's not material. Its gravity model uses the ROS
description's masses and inertias. Those weren't compared here, and they don't affect kinematics.

**J4's −2.9°:** the solver never returns J4 below 0°; the model allows −2.9°. A real elbow hanging at
−1° is legal in the model but outside what IK proposes. This is harmless, and consistent with the
controller's 0° limit.

**For anything new, it matters a lot.** The name `hand_tcp` means **the flange** in one model and
**186 mm past it** in another. Code that gives the solver a pose "for the TCP" without going through
the adapter would be off by about 18 cm. That includes a show's "point at a named frame" move, a new
planner, or a test. **Name the tool frame once:**
- Use the MuJoCo `openarm_<side>_ee_site`, the one the teleop already uses, and always reach the
  solver through the adapter.
- Treat the URDFs' `hand_tcp` as model-specific, never as a shared name.

## Not checked

- Masses and inertias: the impedance controller's gravity compensation.
- Collision geometry.
- The v2.0 description. Nothing the robot runs uses it.
