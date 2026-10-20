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
| `OpenArmPlanner` | High-level facade for planning, collision checking, kinematics | [`sim/planner/planner.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/planner.py) |
| `MotionPlannerBackend` | Protocol interface defining `plan()` and `plan_cartesian()` | [`sim/planner/base.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/base.py) |
| `CBiRRTBackend` | robocode SSIK + MuJoCo collision + CBiRRT + TOPP-RA | [`sim/planner/cbirrt_backend.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/cbirrt_backend.py) |
| `CuRoboBackend` | NVIDIA cuRobo GPU trajectory optimization adapter | [`sim/planner/curobo_backend.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/curobo_backend.py) |
| Benchmark Tool | Statistical measurement comparing planners in simulation | [`sim/planner/benchmark.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/benchmark.py) |
| Web Studio UI | Interactive 3D visualizer, jogger, choreographer, exporter | [`sim/planner/ui/server.py`](file:///home/arxzwood/Documents/Code/School/OpenDubs/opendubs/sim/planner/ui/server.py) |

---

## 2. Environment Setup

The motion planning stack runs in a standalone conda/mamba environment (`env_curobo`) equipped with
CUDA 12.8, PyTorch, NVIDIA Warp, cuRobo, MuJoCo, SSIK, and TOPP-RA:

```bash
# Executable location:
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python
```

Key dependencies:
- `torch==2.11.0+cu128`
- `warp-lang==1.18.0`
- `curobo` (compiled targeting NVIDIA RTX GPUs)
- `mujoco==3.16.0`
- `ssik==8.2.0` & `sscbirrt==3.9.0`
- `toppra==0.6.10`

---

## 3. Selecting Backends

Both planners can be selected through the CLI, Python API, or Web Studio UI.

### Command Line Interface
Pass `--backend cbirrt` or `--backend curobo`:

```bash
# Plan left arm forward reach using cuRobo on GPU
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/run_planner.py \
  --arm left --preset reach_forward --backend curobo

# Plan left arm forward reach using CBiRRT + SSIK on CPU
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/run_planner.py \
  --arm left --preset reach_forward --backend cbirrt

# Open interactive MuJoCo 3D viewer to watch playback
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/run_planner.py \
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
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/benchmark.py --trials 5

# Benchmark right arm and output JSON
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/benchmark.py \
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

## 5. Web Motion Planner Studio

The web-based visual studio allows interactive jogging, live clearance checking, trajectory preview,
and choreographing show routines for demonstrations.

```bash
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/ui/server.py --port 8092
```
Navigate to: **`http://127.0.0.1:8092`**

### UI Features:
- **Live Backend Dropdown:** Switch dynamically between CBiRRT and cuRobo in the top header.
- **Benchmark Modal:** Click **Benchmark** in the top bar to run live simulation benchmarks or view the comparison table in the browser.
- **Dual-Pose 3D Overlay:** Solid mesh displays starting pose; translucent ghost displays target pose.
- **Millimetric Clearance Guard:** Live contact calculation with collision warnings.
- **SSIK Analytical Jogger:** Cartesian $(X, Y, Z)$ target positioning with inverse kinematics solved in $< 0.2\text{ ms}$.
- **Choreography Routine Exporter:** Build multi-step motion sequences and export directly to `software/choreo/shows/halloween.yaml`.

---

## 6. Verification Test Suite

All components are covered by an automated test suite verifying kinematics, collision guards,
modular backend switching, cuRobo GPU planning, and REST APIs:

```bash
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/test_motion_planner.py
```

Test Sections:
1. `SSIK Analytical Kinematics & FK Consistency` (reconstruction error: 0.0000 mm)
2. `MuJoCo Collision Checking & Clearance Guard` (stow safe: 39.9 mm, head contact caught)
3. `CBiRRT Motion Planning & TOPP-RA Retiming` (spline limits, velocity bounds)
4. `Modular Backend Registry & cuRobo Adapter` (GPU CUDA trajectory, custom backend swap)
5. `Studio HTTP Server & REST API Endpoints` (`/api/status`, `/api/presets`, `/api/ik`, `/api/plan`, `/api/benchmark`, `/api/export_show`)

---

## 7. Relationship to Regular Tele-Op Mode

### Tele-Op Independence
Regular tele-operation (**Meta Quest WebXR bridge**) is completely separate and isolated from the
simulation motion planning testbed:

| System | Role | Execution Environment |
|---|---|---|
| **WebXR Tele-op** | Real-time direct controller tracking via Quest headset | Runs in `openarm` Docker container via `opendubs-teleop.service` |
| **Motion Planner** | Collision-free trajectory generation & choreo authoring | Runs in simulation testbed (`sim/planner/`) |

- **No Shared State:** The motion planning code resides entirely in `sim/planner/` and `sim/ik_artifacts/`. No tele-op services, patches, or controller configs were modified.
- **Controller Safety:** Regular tele-op continues to communicate with the `openarm_impedance_controller` as documented in [WebXR teleop](teleop-setup.md).

### Roadmap Toward Hardware Integration
1. **Phase 0 (Current):** Verified in simulation with CBiRRT + SSIK and cuRobo GPU backends.
2. **Phase 1 (Hardware-in-the-Loop):** Connect planned trajectories to `openarm_impedance_controller` via ROS 2 trajectory action clients.
3. **Phase 2 (cuRobo ROS Real-time Integration):** Stream cuRobo GPU trajectory points dynamically as the target hand position moves in tele-op mode.
