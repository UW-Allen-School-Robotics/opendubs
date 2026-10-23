# Arm motion planning (simulation)

How motion planning works for OpenDubs' two 7-DOF OpenArm arms in simulation. This page documents
the planner architecture (CBiRRT + SSIK and optional cuRobo GPU), the benchmarking suite,
choreography composer integration, and simulation verification.

Planning is simulation-only and has not yet run on the physical arms.

## Architecture

OpenDubs supports modular motion planning for both arms in simulation. The stack is structured
around an abstraction (`MotionPlannerBackend`), allowing runtime switching between planners
without modifying caller code.

```
                  ┌──────────────────────────────────────────────┐
                  │          OpenArmPlanner Frontend             │
                  │   (Joint Limits, Clearance, Kinematics)      │
                  └──────────────────────┬───────────────────────┘
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   ▼                                           ▼
      ┌─────────────────────────┐                 ┌─────────────────────────┐
      │     CBiRRT Backend      │                 │      cuRobo Backend     │
      │   (SSIK + Clearance)    │                 │   (GPU CUDA Batched)    │
      ├─────────────────────────┤                 ├─────────────────────────┤
      │ • SSIK Analytical IK    │                 │ • PyTorch + Warp C/CUDA │
      │ • MuJoCo Clearance      │                 │ • Batched Optimization  │
      │ • Sampling-based CBiRRT │                 │ • Joint & Cartesian     │
      │ • TOPP-RA Retiming      │                 │ • Pre-timed Trajectory  │
      └─────────────────────────┘                 └─────────────────────────┘
```

| Component | Role | Location |
|---|---|---|
| `OpenArmPlanner` | Facade for planning, collision checking, kinematics | `sim/planner/planner.py` |
| `MotionPlannerBackend` | Protocol interface defining `plan()` and `plan_cartesian()` | `sim/planner/base.py` |
| `CBiRRTBackend` | SSIK + MuJoCo collision + CBiRRT + TOPP-RA | `sim/planner/cbirrt_backend.py` |
| `CuRoboBackend` | NVIDIA cuRobo GPU trajectory optimization adapter | `sim/planner/curobo_backend.py` |
| Benchmark Tool | Statistical measurement comparing planners in simulation | `sim/planner/benchmark.py` |
| Choreo Composer | 3D visualizer, jogger, and cue path planner | `software/choreo/composer/` |

## Environment setup

### Needed dependencies

The core simulation environment requires:
- `numpy`: numerical operations and joint array handling (`pip install numpy`)
- `mujoco`: simulation scene, forward kinematics, and clearance sweeps (`pip install mujoco`)
- Generated SSIK artifacts: analytical IK solvers pre-built in `sim/ik_artifacts/` (`openarm_left_ik.py`, `openarm_right_ik.py`)

With only the needed dependencies installed, spline interpolation and analytical IK solving work.

### Optional dependencies

Additional planners and retiming tools can be installed as needed:
- `sscbirrt`: Sampling-based constrained bidirectional RRT planner (`pip install sscbirrt` or build from git source). Adds the `cbirrt` backend. Without it, spline planning remains functional.
- `toppra`: Trajectory Optimization for Paths Subject to Acceleration and Velocity Constraints (`pip install toppra`). Provides time-optimal trajectory retiming. Without it, the planner falls back to velocity-bounded linear interpolation.
- `curobo` + `torch` + `warp-lang`: NVIDIA cuRobo GPU trajectory generator (CUDA only; install per [cuRobo installation](https://curobo.org)). Adds the `curobo` backend. Without it, CPU backends continue to work normally.

## Selecting backends

Planners can be selected through the CLI, Python API, or Composer UI.

### Command-line interface
Pass `--backend cbirrt` or `--backend curobo`:

```bash
# Plan left arm forward reach using cuRobo on GPU
python sim/planner/run_planner.py \
  --arm left --preset reach_forward --backend curobo

# Plan left arm forward reach using CBiRRT + SSIK on CPU
python sim/planner/run_planner.py \
  --arm left --preset reach_forward --backend cbirrt

# Open interactive MuJoCo 3D viewer to watch playback
python sim/planner/run_planner.py \
  --arm left --preset reach_forward --backend curobo --view
```

### Python API
```python
import numpy as np
from sim.planner import OpenArmPlanner, get_available_backends

# Check installed backends
print(get_available_backends())
# {'cbirrt': {'name': 'cbirrt', 'available': True}, 'curobo': {'name': 'curobo', 'available': True}}

# Initialize with desired backend:
planner = OpenArmPlanner(arm="left", backend="curobo")

# Plan collision-free trajectory:
q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])
traj = planner.plan_to_joint_goal(q_start, q_goal)

# Switch backend at runtime:
planner.set_backend("cbirrt")
```

## Performance benchmarking

The benchmark suite (`sim/planner/benchmark.py`) measures planning latency, trajectory duration,
waypoint density, path clearance, and success rates in MuJoCo simulation.

### Running benchmarks
```bash
# Run benchmark across canonical motions (5 trials per motion)
python sim/planner/benchmark.py --trials 5

# Benchmark right arm and output JSON
python sim/planner/benchmark.py \
  --arm right --trials 3 --json-out benchmark_results.json
```

### Empirical results (simulation)

Tested on an x86_64 host (AMD Ryzen 9 7950X) with an NVIDIA RTX 4090 GPU in MuJoCo simulation,
evaluating OpenArm V1 left arm transitions across canonical workspace poses (`stow` → `reach_forward` → `reach_high` → `reach_side` → `ready` → `stow`):

```
==============================================================================
 SUMMARY: OpenArm Planner Performance Comparison (LEFT ARM)
==============================================================================
Metric                       | CBIRRT               | CUROBO              
------------------------------------------------------------------------------
Success Rate                 | 100.0% (10/10)       | 100.0% (10/10)      
Mean Latency                 | 112.3 ms             | 48.5 ms             
Median Latency               | 116.1 ms             | 47.7 ms             
P95 Latency                  | 137.6 ms             | 52.7 ms             
Min / Max Latency            | 85.6 / 145.8 ms      | 44.1 / 53.5 ms      
Mean Traj Duration           | 0.85 s               | 0.63 s              
Mean Waypoints               | 17                   | 32                  
Min Path Clearance           | 39.9 mm              | 39.9 mm             
==============================================================================
 >> SPEEDUP: cuRobo is 2.3x faster on average than CBiRRT (48.5 ms vs 112.3 ms)
==============================================================================
```

Key observations:
1. **Speedup:** cuRobo operates **2.3× faster** on average than CBiRRT on this test host by parallelizing optimization on CUDA cores.
2. **Clearance:** Both planners achieved measured clearances of 39.9 mm along their generated waypoints in the simulation scene. The authoritative collision gate remains the save-time sweep (`sim/clearance.py`), which checks the interpolated spline against the 30.0 mm threshold (`CLEAR_MIN_MM`). Note that the twin simulation reads about 6° optimistic and the head moves during live operation.

## Choreo Composer integration

Motion planning is integrated into the Choreo Composer (`software/choreo/composer/`). To start the
composer, see the [Composer quickstart](composer/quickstart.md) or run:

```bash
software/choreo/studio-up.sh
```

### Capabilities
- **3D Robot Arm Preview:** Dual-arm WebGL visualizer rendering real OpenArm mesh geometry with orbit camera.
- **Dynamic Joint Sliders:** Interactive joint angle sliders with live 3D visual preview.
- **SSIK Analytical Jogger & Reach Solver:** Enter Cartesian (X, Y, Z) targets and click **Solve Reach** to compute analytical IK in under 0.2 ms.
- **Automatic Path Planning:** Click **Plan Path** on any arm cue to synthesize collision-free paths routing around obstacles and the robot chassis using CBiRRT or cuRobo.
- **Save-Time Clearance Verification:** Clearance reported by the planner applies to the planner's own waypoints. The save-time sweep (`sim/clearance.py`) provides the check against the 30.0 mm threshold (`CLEAR_MIN_MM`).
- **Show Runner Safety Gating:** Physical robot motion is executed through `software/choreo/runner.py`, which acquires the exclusive motion lease, validates preflight safety, and enforces the RC deadman switch.

## Verification test suite

The simulation planning components are covered by an automated test suite verifying kinematics,
collision guards, backend switching, cuRobo integration, and benchmarking:

```bash
python3 sim/planner/test_motion_planner.py
```

### Covered test sections
1. `SSIK Analytical Kinematics & FK Consistency` (reconstruction error: 0.0000 mm)
2. `MuJoCo Collision Checking & Clearance Guard` (stow safe: 39.9 mm, head contact caught)
3. `CBiRRT Motion Planning & TOPP-RA Retiming` (spline limits, velocity bounds)
4. `Modular Backend Registry & cuRobo Adapter` (GPU CUDA trajectory, custom backend swap)
5. `Performance Benchmarking` (CBiRRT & cuRobo success rates and latency profiling)

## Tele-op independence

Regular tele-operation ([WebXR teleop](teleop-setup.md)) is separate and isolated from the
simulation motion planning testbed:

| System | Role | Execution Environment |
|---|---|---|
| **WebXR Tele-op** | Real-time direct controller tracking via Quest headset | Runs in `openarm` Docker container via `opendubs-teleop.service` |
| **Motion Planner** | Collision-free trajectory generation & choreo authoring | Runs in simulation testbed (`sim/planner/`) and Choreo Composer (`software/choreo/composer/`) |

Tele-op communicates with the `openarm_impedance_controller` as documented in [WebXR teleop](teleop-setup.md).
The motion planning code resides in `sim/planner/` and `sim/ik_artifacts/`. Planning is simulation only and
has not yet run on the physical arms.

## Licenses and provenance

- **cuRobo**: Copyright &copy; NVIDIA Corporation. The backend targets the cuRobo v2 API, licensed under **Apache-2.0**. (Earlier v0.7.x releases were under the NVIDIA non-commercial license). cuRobo is an external library and is **not vendored** into this repository. It is an optional dependency installed separately by users in environments equipped with NVIDIA CUDA GPUs. The composer, choreography tools, and test suites function without cuRobo.
- **SSIK (Simple Symbolic Inverse Kinematics)**: The analytical IK artifacts in `sim/ik_artifacts/openarm_left_ik.py` and `sim/ik_artifacts/openarm_right_ik.py` were generated using SSIK, developed by the Personal Robotics Lab and licensed under the **BSD-3-Clause** license. See `sim/ik_artifacts/LICENSE-ssik`.
- **sscbirrt**: The constrained bidirectional RRT planner (`sscbirrt`) is developed by the Personal Robotics Lab and licensed under the **MIT** license. It is an optional dependency; the test suite skips cleanly when it is not installed.
- **TOPP-RA**: Trajectory Optimization for Paths Subject to Acceleration and Velocity Constraints (`toppra`) is licensed under the **MIT** license and is an optional dependency for trajectory retiming. Fallback velocity-bounded interpolation is provided when TOPP-RA is not installed.
