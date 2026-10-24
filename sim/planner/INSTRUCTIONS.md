# OpenArm Motion Planning & Choreography: Instructions & Developer Guide

This document provides complete instructions for using the OpenArm motion planning tools,
planning collision-free trajectories in the Choreo Composer, deploying shows to the physical robot
(Thor), enabling the arms via the dashboard, and programmatically calling the CBiRRT and cuRobo
planning functions in Python.

---

## Quickstart: Choreo Composer

The **Choreo Composer** is the visual authoring environment for creating robot routines and planning
collision-free trajectories.

### Starting the Composer

From the repository root, start the composer using the studio launcher:

```bash
software/choreo/studio-up.sh
```

Open your web browser and navigate to:
**`http://localhost:8091`**

To stop the background server when done, run `software/choreo/studio-down.sh`.

---

### Posing and Planning Arms in the UI

1. **Open the 3D Robot Visualizer**:
   - In the top-right toolbar, click the **3D View** button.
   - An interactive 3D WebGL preview appears, rendering the dual OpenArm meshes.
   - Use the camera preset buttons (**ISO**, **FRONT**, **SIDE**, **TOP**) or orbit/pan/zoom controls.
   - Click the window icon in the 3D header to detach the visualizer into a floating picture-in-picture window.

2. **Select or Create an Arm Cue**:
   - Click any keyframe on the **`ARM_L`** or **`ARM_R`** lane on the timeline.
   - The **Arm Inspector** panel opens on the right.

3. **Posing the Arm**:
   - **Joint Sliders (J1–J7)**: Drag sliders to adjust outward joint angles interactively. The 3D ghost arm updates in real time to show the target pose.
   - **SSIK Cartesian Reach Solver**: In the *Solve Target Reach* section, enter Cartesian coordinates (e.g. `[0.45, 0.10, 0.25]` in meters relative to arm base) or a named target (e.g. `head_screen`), then click **Solve Reach**. Analytical IK resolves the target into joint angles in under 0.2 ms.

4. **Select Motion Planner Backend**:
   - In the **Motion Planner** dropdown, choose your planning backend:
     - `Spline (Default)`: Direct Hermite cubic spline interpolation.
     - `CBiRRT (SSIK + Clearance)`: Constrained bidirectional RRT with analytical IK projection and MuJoCo collision checking (CPU).
     - `cuRobo (GPU Tensor)`: Parallelized CUDA trajectory optimization (NVIDIA GPU required).

5. **Generate and Preview Trajectory**:
   - Click **Plan Path**.
   - The composer computes the trajectory and opens the **Plan Preview Card**:
     - **Validation Verdict**: Displays `PASS` (green) or `FAIL` (red) based on validator speed caps (180 deg/s) and joint limits.
     - **Planner Clearance**: Reports the minimum clearance along the planner's own waypoints.
     - **Clamping Notification**: Warns if any target or waypoint joint angle was clamped to stay within the usable range (5 deg margin inside hardware limits).
   - Click **Apply Planned Path** (or **Apply Anyway**) to insert avoidance waypoints and the arrival cue into the show, or click **Discard** to cancel.

6. **Save the Show**:
   - Press **`Ctrl+S`** or click **Save**.
   - The save-time clearance sweep (`CLEAR_MIN_MM = 30.0 mm` in `software/choreo/ik.py`) runs across the entire interpolated show to verify clearance.

---

## Deploying and running on the physical robot

Physical robot execution and show deployment are documented in the composer guides:

- [docs/composer/quickstart.md](../../docs/composer/quickstart.md) ("Run it live"): Live execution with the show runner (`software/choreo/runner.py`), RC authority (`AUTO`), and deadman switch gating.
- [docs/composer/making-a-show.md](../../docs/composer/making-a-show.md) ("Sending a show to the robot"): Shows are transferred to the robot (`/var/lib/opendubs/shows`) using `python3 software/choreo/show_sync.py push <robot> <show>.yaml`.

Before running arm shows, the arms must be energized and active (via the dashboard's Teleop page or `openarm-up activate` on the robot).

---

## Python API reference: CBiRRT, cuRobo, and SSIK

Developers can use the motion planning engine directly in Python scripts, testbeds, and notebooks.

### Unified Interface: `OpenArmPlanner`

The `OpenArmPlanner` class (`sim.planner.OpenArmPlanner`) provides a backend-agnostic interface
supporting both joint-space and Cartesian planning.

```python
import numpy as np
from sim.planner import OpenArmPlanner, get_available_backends

# 1. Check available backends in your environment
print(get_available_backends())
# Output: {'cbirrt': {'available': True}, 'curobo': {'available': True}}

# 2. Instantiate planner for left or right arm
# backend options: 'cbirrt', 'curobo', or 'spline'
planner = OpenArmPlanner(arm="left", backend="cbirrt", min_clearance_mm=30.0)

# 3. Define start and goal joint angles (radians)
q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
q_goal  = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])

# 4. Plan collision-free trajectory
traj = planner.plan_to_joint_goal(q_start, q_goal, timeout=6.0)

if traj is not None:
    print(f"Plan succeeded!")
    print(f"Waypoints: {len(traj.positions)}")
    print(f"Duration:  {traj.duration:.2f} s")
    print(f"Clearance: {traj.min_clearance_mm:.1f} mm")

    # Sample trajectory at time t (seconds)
    q_t, v_t, a_t = traj.sample(t=0.5)

    # Launch interactive MuJoCo playback window
    planner.visualize_trajectory(traj)

# 5. Switch backends at runtime
if get_available_backends().get("curobo", {}).get("available"):
    planner.set_backend("curobo")
    gpu_traj = planner.plan_to_joint_goal(q_start, q_goal)
    print(f"cuRobo planned in {gpu_traj.metadata.get('plan_time_ms', 0):.1f} ms")
```

---

### Direct cuRobo Backend Functions

When NVIDIA cuRobo and CUDA are available, the cuRobo backend can be invoked directly:

```python
from sim.planner.curobo_backend import CuRoboBackend
import numpy as np

if CuRoboBackend.is_available():
    backend = CuRoboBackend(arm="left")
    
    q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
    q_goal  = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])
    
    # Generate time-optimal trajectory on GPU
    traj = backend.plan(q_start, q_goal)
    
    # Or plan directly to a 4x4 SE(3) Cartesian pose
    T_target = np.eye(4)
    T_target[:3, 3] = [0.45, 0.10, 0.25]
    cart_traj = backend.plan_cartesian(q_start, T_target)
```

---

### Direct CBiRRT Backend Functions

The CBiRRT backend combines SSIK analytical inverse kinematics with MuJoCo clearance checking
and TOPP-RA path retiming on CPU:

```python
from sim.planner.cbirrt_backend import CBiRRTBackend
from sim.clearance import ClearanceChecker
from sim.ik_artifacts import openarm_left_ik
import numpy as np

# Clearance checker evaluates distance to chassis and head in MuJoCo
checker = ClearanceChecker()

backend = CBiRRTBackend(
    arm="left",
    collision_checker=checker,
    ik_solver=openarm_left_ik.solve_ik,
)

q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
q_goal  = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])

traj = backend.plan(q_start, q_goal)
```

---

### Analytical Inverse Kinematics via SSIK

For fast analytical kinematics (< 0.2 ms per solve), use the generated SSIK modules directly:

```python
import numpy as np
from sim.ik_artifacts import openarm_left_ik, openarm_right_ik

# 4x4 SE(3) end-effector target pose in arm base link0 frame
T_target = np.eye(4)
T_target[:3, 3] = [0.45, 0.10, 0.25]  # X, Y, Z in meters

# Solve analytical IK
solutions = openarm_left_ik.solve_ik(T_target)
print(f"Found {len(solutions)} analytical IK solutions:")
for idx, sol in enumerate(solutions):
    print(f"  Sol {idx}: {np.degrees(sol).round(1)} deg")

# Compute forward kinematics
T_fk = openarm_left_ik.forward_kinematics(solutions[0])
print("Reconstructed position:", T_fk[:3, 3].round(4))
```

---

## Command-line tools and benchmarking

### Command-Line Planner Runner
Run test motions directly from the terminal without starting the web composer:

```bash
# Plan left arm forward reach
python3 sim/planner/run_planner.py --arm left --preset reach_forward

# Plan to specific joint angles in degrees
python3 sim/planner/run_planner.py --arm right --joints "0,-20,0,80,0,15,0"

# Plan with interactive 3D playback in MuJoCo viewer
python3 sim/planner/run_planner.py --arm left --preset reach_forward --view
```

### Benchmarking Suite
Compare planning latency, trajectory duration, waypoint density, and clearance between CBiRRT
and cuRobo:

```bash
# Run benchmark across canonical motions (5 trials per motion scenario)
python3 sim/planner/benchmark.py --trials 5

# Benchmark right arm and output JSON summary
python3 sim/planner/benchmark.py --arm right --trials 3 --json-out benchmark_results.json
```

### Verification Test Suite
Run the test suite verifying kinematics, clearance checking, CBiRRT, cuRobo, and benchmarking:

```bash
python3 sim/planner/test_motion_planner.py
```
*(Tests skip cleanly if optional dependencies like `mujoco` or `curobo` are not installed).*
