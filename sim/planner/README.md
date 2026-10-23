# OpenArm Simulation Motion Planner

Simulation motion planning engine for the OpenArm V1 arms on OpenDubs.
Integrates **SSIK** analytical IK, **MuJoCo** clearance/collision checking, **CBiRRT** path planning, **TOPP-RA** time-optimal retiming, and **cuRobo** GPU trajectory optimization.

Integrated with the **Choreo Composer** (`software/choreo/composer/`) for interactive 3D authoring,
joint angle sliders, Cartesian reach solving, and collision-free path synthesis.

> For full step-by-step instructions on using the tool, enabling the arms via the dashboard,
> deploying to Thor, and developer Python API references, see [**INSTRUCTIONS.md**](INSTRUCTIONS.md).

---

## 1. Quick Start

Activate the simulation virtual environment:

```bash
source .venv/bin/activate
```

### Run a plan using presets from CLI:
```bash
# Plan left arm forward reach
./sim/planner/run_planner.py --arm left --preset reach_forward

# Plan right arm to stow pose
./sim/planner/run_planner.py --arm right --preset stow

# Plan from stow pose to wave_ready
./sim/planner/run_planner.py --arm left --start stow --preset wave_ready
```

### Plan to specific joint angles (in degrees):
```bash
./sim/planner/run_planner.py --arm left --joints "0,-30,0,60,0,15,0"
```

### Plan to a static Cartesian target:
Pass `x,y,z` in meters relative to the arm's base link (`openarm_<side>_link0`):
```bash
./sim/planner/run_planner.py --arm left --cartesian "0.187,0.284,0.286"
```

### Watch playback in the interactive MuJoCo 3D viewer:
Add the `--view` flag:
```bash
./sim/planner/run_planner.py --arm left --preset reach_forward --view
```
*(Press **ESC** to exit the 3D viewer).*

---

## 2. Python API

```python
import numpy as np
from sim.planner import OpenArmPlanner

# Initialize planner for left arm with 30mm minimum clearance threshold
planner = OpenArmPlanner(arm="left", min_clearance_mm=30.0)

# Plan to joint goal:
q_start = np.zeros(7)
q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])
traj = planner.plan_to_joint_goal(q_start, q_goal)

if traj:
    print(f"Trajectory duration: {traj.duration:.2f} s")
    print(f"Minimum clearance:   {traj.min_clearance_mm:.1f} mm")
    
    # Sample at time t:
    pos, vel, acc = traj.sample(t=0.5)

    # Interactive 3D preview:
    planner.visualize_trajectory(traj)
```

---

## 3. Modular Backend Architecture & cuRobo Support

The motion planning stack is built on a modular `MotionPlannerBackend` protocol with a global backend registry (`sim.planner.register_backend`).

### Switching Backends at Runtime:

```python
from sim.planner import OpenArmPlanner, get_available_backends

print(get_available_backends())
# {'cbirrt': {'name': 'cbirrt', 'available': True}, 'curobo': {'name': 'curobo', 'available': True}}

# Switch via backend name or instance:
planner = OpenArmPlanner(arm="left", backend="cbirrt")

# When cuRobo is installed with CUDA:
planner.set_backend("curobo")
```

Or pass `--backend` in the CLI:
```bash
./sim/planner/run_planner.py --arm left --preset reach_forward --backend cbirrt
./sim/planner/run_planner.py --arm left --preset reach_forward --backend curobo
```

### How cuRobo Integrates:
1. `sim/planner/curobo_backend.py` implements the `MotionPlannerBackend` protocol.
2. It auto-detects PyTorch and CUDA availability via `CuRoboBackend.is_available()`.
3. When cuRobo is installed, it computes collision-free trajectories directly on GPU tensors and returns pre-timed `PlannedTrajectory` objects without needing CPU retiming.
4. If native Cartesian reach is called, `plan_cartesian` optimizes trajectory and end-effector pose jointly on GPU.

See [`example_custom_planner.py`](example_custom_planner.py) for a runnable demonstration of writing your own planner backend.

---

## 4. Integration with Choreo Composer

All motion planning capabilities are directly embedded into the **Choreo Composer**:

```bash
# Launch the Choreo Composer
python3 software/choreo/composer/server.py --port 8091
```

Then open in your browser:
**`http://localhost:8091`**

### Composer Features Powered by `sim/planner`:
- **3D Robot Arm Preview:** Dual-arm WebGL visualizer rendering real OpenArm geometry.
- **Dynamic Joint Sliders:** Interactive joint angle sliders with live 3D preview.
- **Cartesian IK Reach Solving:** Enter $(X, Y, Z)$ targets and click **Solve Reach** to solve analytical kinematics in $< 0.2\text{ ms}$.
- **Automatic Path Planning:** Click **Plan Path** on any arm cue to synthesize collision-free paths routing around obstacles and the robot body using cuRobo or CBiRRT.
- **Safety Clearance Verification:** Checks trajectories against the 30.0 mm minimum threshold (`CLEAR_MIN_MM` in `software/choreo/ik.py`).
- **Show Runner Safety:** Shows are executed on physical hardware through `software/choreo/runner.py`, which enforces the motion lease, E-Stop, and RC deadman switch.

---

## 5. Performance Benchmarking Tool (`benchmark.py`)

To measure and compare planning latency, duration, waypoint count, clearance, and success rates between CBiRRT and cuRobo in simulation:

```bash
# Run benchmark comparing CBiRRT vs cuRobo (default 5 trials per motion scenario):
python3 sim/planner/benchmark.py --trials 5

# Benchmark right arm:
python3 sim/planner/benchmark.py --arm right --trials 3

# Export results to JSON:
python3 sim/planner/benchmark.py --json-out benchmark_results.json
```

---

## 6. Verification Test Suite

Run the automated test suite verifying kinematics, collision guards, CBiRRT, cuRobo, and benchmarking:
```bash
python3 sim/planner/test_motion_planner.py
```

### Covered Test Sections:
1. `SSIK Analytical Kinematics & FK Consistency` (reconstruction error: 0.0000 mm)
2. `MuJoCo Collision Checking & Clearance Guard` (stow safe: 39.9 mm, head contact caught)
3. `CBiRRT Motion Planning & TOPP-RA Retiming` (spline limits, velocity bounds)
4. `Modular Backend Registry & cuRobo Adapter` (GPU CUDA trajectory, custom backend swap)
5. `Performance Benchmarking` (CBiRRT & cuRobo success rates and latency profiling)
