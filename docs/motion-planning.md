# OpenArm Motion Planning & cuRobo Integration

How motion planning works for OpenDubs' two 7-DOF OpenArm arms. This page documents the dual-backend
architecture (**CBiRRT + SSIK** and **cuRobo GPU**), the benchmarking suite, the interactive Web
Motion Planner Studio, simulation verification, and the integration roadmap toward hardware tele-op
and impedance control.

---

## 1. Overview & Architecture

OpenDubs supports modular motion planning for both arms in simulation prior to deployment on hardware.
The stack is structured around a backend-agnostic abstraction (`MotionPlannerBackend`), allowing seamless
runtime switching between planners without modifying caller code.

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
      │   (robocode + SSIK)     │                 │   (GPU CUDA Batched)    │
      ├─────────────────────────┤                 ├─────────────────────────┤
      │ • SSIK Analytical IK    │                 │ • PyTorch + Warp C/CUDA │
      │ • MuJoCo Clearance      │                 │ • Batched Optimization  │
      │ • Sampling-based CBiRRT │                 │ • Joint & Cartesian     │
      │ • TOPP-RA Retiming      │                 │ • Pre-timed Trajectory  │
      └─────────────────────────┘                 └─────────────────────────┘
```

| Component | Role | Location |
|---|---|---|
| `OpenArmPlanner` | High-level facade for planning, collision checking, kinematics | `sim/planner/planner.py` |
| `MotionPlannerBackend` | Protocol interface defining `plan()` and `plan_cartesian()` | `sim/planner/base.py` |
| `CBiRRTBackend` | robocode SSIK + MuJoCo collision + CBiRRT + TOPP-RA | `sim/planner/cbirrt_backend.py` |
| `CuRoboBackend` | NVIDIA cuRobo GPU trajectory optimization adapter | `sim/planner/curobo_backend.py` |
| Benchmark Tool | Statistical measurement comparing planners in simulation | `sim/planner/benchmark.py` |
| Choreo Composer | Interactive 3D visualizer, jogger, and cue path planner | `software/choreo/composer/` |

---

## 2. Environment Setup

The motion planning stack can run in a dedicated Python environment equipped with
PyTorch, NVIDIA Warp, cuRobo, MuJoCo, SSIK, and TOPP-RA:

Key dependencies:
- `torch` (with CUDA support for cuRobo GPU acceleration)
- `warp-lang`
- `curobo` (optional; installed per https://curobo.org)
- `mujoco`
- `ssik` & `sscbirrt`
- `toppra`

---

## 3. Selecting Backends

Both planners can be selected through the CLI, Python API, or Web Studio UI.

### Command Line Interface
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

---

## 4. Performance Benchmarking

The benchmark suite (`sim/planner/benchmark.py`) measures and compares planning latency, trajectory
duration, waypoint density, path clearance, and success rates in MuJoCo simulation.

### Running Benchmarks
```bash
# Run benchmark across canonical motions (5 trials per motion)
python sim/planner/benchmark.py --trials 5

# Benchmark right arm and output JSON
python sim/planner/benchmark.py \
  --arm right --trials 3 --json-out benchmark_results.json
```

### Empirical Results (Simulation)

Tested on OpenArm V1 left arm across canonical workspace transitions (`stow` $\to$ `reach_forward`, `reach_forward` $\to$ `spooky_arms_up`, `spooky_arms_up` $\to$ `zombie_reach`, `zombie_reach` $\to$ `pumpkin_carry`, `pumpkin_carry` $\to$ `stow`):

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

Key takeaways:
1. **Speedup:** cuRobo operates **2.3× to 4.8× faster** than CBiRRT by computing trajectory optimization in parallel on GPU CUDA cores.
2. **Smoothness:** cuRobo outputs denser waypoint paths (32 waypoints vs 17) with 26% faster execution duration.
3. **Safety:** Both planners strictly obey the 38.9–39.9 mm clearance threshold against the robot chassis and head in MuJoCo.

---

## 5. Choreo Composer Integration

All motion planning capabilities are natively integrated into the **Choreo Composer** (`software/choreo/composer/`):

### Starting the Composer:
```bash
python3 software/choreo/composer/server.py --port 8091
```
Navigate to: **`http://localhost:8091`**

### Features:
- **3D Robot Arm Preview:** Dual-arm WebGL visualizer rendering real OpenArm mesh geometry with orbit camera.
- **Dynamic Joint Sliders:** Interactive joint angle sliders with live 3D visual preview.
- **SSIK Analytical Jogger & Reach Solver:** Enter Cartesian $(X, Y, Z)$ targets and click **Solve Reach** to compute analytical IK in $< 0.2\text{ ms}$.
- **Automatic Path Planning:** Click **Plan Path** on any arm cue to synthesize collision-free paths routing around obstacles and the robot chassis using CBiRRT or cuRobo.
- **Safety Clearance Verification:** Strictly verifies all generated trajectories against the 30.0 mm minimum clearance threshold (`CLEAR_MIN_MM` in `software/choreo/ik.py`).
- **Show Runner Safety Gating:** Physical robot motion is executed through `software/choreo/runner.py`, which acquires the exclusive motion lease, validates preflight safety, and enforces the RC deadman switch.

---

## 6. Verification Test Suite

The simulation planning components are covered by an automated test suite verifying kinematics, collision guards, modular backend switching, cuRobo GPU planning, and benchmarking:

```bash
python3 sim/planner/test_motion_planner.py
```

### Covered Test Sections:
1. `SSIK Analytical Kinematics & FK Consistency` (reconstruction error: 0.0000 mm)
2. `MuJoCo Collision Checking & Clearance Guard` (stow safe: 39.9 mm, head contact caught)
3. `CBiRRT Motion Planning & TOPP-RA Retiming` (spline limits, velocity bounds)
4. `Modular Backend Registry & cuRobo Adapter` (GPU CUDA trajectory, custom backend swap)
5. `Performance Benchmarking` (CBiRRT & cuRobo success rates and latency profiling)

---

## 7. Relationship to Regular Tele-Op Mode

### Tele-Op Independence
Regular tele-operation (**Meta Quest WebXR bridge**) is completely separate and isolated from the
simulation motion planning testbed:

| System | Role | Execution Environment |
|---|---|---|
| **WebXR Tele-op** | Real-time direct controller tracking via Quest headset | Runs in `openarm` Docker container via `opendubs-teleop.service` |
| **Motion Planner** | Collision-free trajectory generation & choreo authoring | Runs in simulation testbed (`sim/planner/`) and Choreo Composer (`software/choreo/composer/`) |

- **No Shared State:** The motion planning code resides entirely in `sim/planner/` and `sim/ik_artifacts/`. No tele-op services, patches, or controller configs were modified.
- **Controller Safety:** Regular tele-op continues to communicate with the `openarm_impedance_controller` as documented in [WebXR teleop](teleop-setup.md).

### Roadmap Toward Hardware Integration
1. **Phase 0 (Completed):** Verified in simulation with CBiRRT + SSIK and cuRobo GPU backends.
2. **Phase 1 (Hardware-in-the-Loop & Execution):** Hardware safety preflight layer, HITL 50 Hz loopback, remote SSH transport to Thor `openarm` container, and gripper action commands.
3. **Phase 2 (cuRobo ROS Real-time Integration):** Stream cuRobo GPU trajectory points dynamically as the target hand position moves in tele-op mode.

---

## 8. Licenses and Provenance

- **cuRobo**: Copyright &copy; NVIDIA Corporation. cuRobo is an external library developed by NVIDIA Research under its own terms (NVIDIA Source Code License / Apache-2.0). It is **not vendored** into this repository. It is an optional dependency that users can install separately in environments equipped with NVIDIA CUDA GPUs. The composer, choreography tools, and test suites function cleanly without cuRobo.
- **SSIK (Simple Symbolic Inverse Kinematics)**: The analytical IK artifacts in `sim/ik_artifacts/openarm_left_ik.py` and `sim/ik_artifacts/openarm_right_ik.py` were generated using SSIK, developed by the Personal Robotics Lab and licensed under the **BSD-3-Clause** license.
- **sscbirrt**: The constrained bidirectional RRT planner (`sscbirrt`) is developed by the Personal Robotics Lab and licensed under the **BSD-3-Clause** license. It is an optional dependency; the test suite skips cleanly when it is not installed.
- **TOPP-RA**: Trajectory Optimization for Paths Subject to Acceleration and Velocity Constraints (`toppra`) is licensed under the **LGPL-3.0 / BSD-3-Clause** license and is an optional dependency for trajectory retiming. Fallback velocity-bounded interpolation is provided when TOPP-RA is not installed.

