# OpenArm Simulation Motion Planner

Simulation motion planning testbed for the OpenArm V1 arms on OpenDubs.
Integrates **SSIK** analytical IK, **MuJoCo** clearance/collision checking, **CBiRRT** path planning, and **TOPP-RA** time-optimal retiming.

---

## 1. Quick Start

Activate the simulation virtual environment:

```bash
source .venv/bin/activate
```

### Run a plan using presets:
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

### Plan to a static Cartesian target (v0):
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

# Initialize planner for left arm
planner = OpenArmPlanner(arm="left", min_clearance_mm=15.0)

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
# {'cbirrt': {'name': 'cbirrt', 'available': True}, 'curobo': {'name': 'curobo', 'available': False}}

# Switch via backend name or instance:
planner = OpenArmPlanner(arm="left", backend="cbirrt")

# When cuRobo is installed with CUDA:
# planner.set_backend("curobo")
```

Or pass `--backend` in the CLI and Web Studio:
```bash
# Run CLI with a specific backend:
./sim/planner/run_planner.py --arm left --preset reach_forward --backend cbirrt

# Launch Web UI with cuRobo (when installed):
./sim/planner/ui/server.py --backend curobo
```

### How cuRobo Integrates:
1. `sim/planner/curobo_backend.py` implements the `MotionPlannerBackend` protocol.
2. It auto-detects PyTorch and CUDA availability via `CuRoboBackend.is_available()`.
3. When cuRobo is installed, it computes collision-free trajectories directly on GPU tensors and returns pre-timed `PlannedTrajectory` objects without needing CPU retiming.
4. If native Cartesian reach is called, `plan_cartesian` optimizes trajectory and end-effector pose jointly on GPU.
5. In the Web UI, the **BACKEND** dropdown in the top header dynamically reflects all registered backends and allows switching between them live!

See [`example_custom_planner.py`](example_custom_planner.py) for a runnable demonstration of writing your own planner backend.

---

## 4. Web Motion Planner Studio & Sequence Choreographer

A lightweight, zero-dependency Web UI is available to jog arm poses, inspect 3D collision clearances in real time, plan collision-free motions with CBiRRT or cuRobo, preview trajectories with an interactive scrubber, and choreograph/export show routines:

```bash
# Launch the Motion Planner Studio (Local Workstation / Laptop)
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/ui/server.py --port 8092
```

Then open in your browser:
**`http://localhost:8092`**

### Hosting on the Robot (Thor / `10.42.2.10`):
On the physical robot, **port 8092 is occupied** by `opendubs-runner` (the kiosk choreography cue runner).
The Motion Planner Studio is installed as a shared system service at `/opt/opendubs-planner` and runs on port **`8095`**:
```bash
# Service status:
sudo systemctl status opendubs-planner.service

# Restart if needed:
sudo systemctl restart opendubs-planner.service
```
Access it directly from any browser on the robot's Wi-Fi at:
**`http://10.42.2.10:8095`** (or `http://opendubs2.local:8095`).

> [!NOTE]
> `/opt/opendubs-planner/` is owned by group `opendubs-admin` (`2775`), giving all team members (`andrew`, `archit`, `shamika`, `daphne`, `eric`, `tobi`, `raj`) full collaborative access without touching `/opt/opendubs/`.

### Web Studio Features:
- **Interactive 3D Arm Visualizer:** Real OpenArm V1 mesh geometry in WebGL with orbit camera (ISO, Front, Side, Top, free orbit).
- **Dual-Pose Overlay:** Displays the solid starting pose and translucent ghost target pose simultaneously.
- **Real-Time Clearance & Collision Guard:** Live MuJoCo clearance calculation down to the millimeter with red collision alerts.
- **Cartesian Jogging & SSIK:** Enter or jog end-effector $(X, Y, Z)$ coordinates and solve analytical IK in $\sim 0.2\text{ ms}$.
- **Presets:** Quick selection of standard poses (`stow`, `zero`, `reach_forward`) and Halloween poses (`zombie_reach`, `spooky_arms_up`, `pumpkin_offer`, `spooky_wave`, `cauldron_stir`).
- **Interactive Trajectory Player:** Scrub through time, play back at $0.5\times / 1.0\times / 2.0\times$ speed, and view the 3D end-effector ribbon.
- **Sequence Choreographer with Gripper Actions:**
  - Build an ordered sequence of keyframe poses.
  - Set individual keyframe gripper states (**Open** / **Closed**).
  - Click **Plan Full Sequence** to synthesize collision-free paths with automatic gripper action transitions.
  - Export sequences directly to `software/choreo/shows/` YAML format.
- **Live Hardware Controls:**
  - **🛡️ Preflight Check**: Validates clearance, velocity limits, and TCP bounding box before any physical motion.
  - **🔄 HITL Dry Run**: Runs closed-loop 50 Hz simulated execution with live progress bar and 3D visualizer feedback.
  - **🚀 Execute on Robot**: Sends validated trajectories to the physical OpenArm controller.
  - **🛑 EMERGENCY HOLD**: Immediately cancels active trajectory and commands the controller to hold in place.

---

## 5. Performance Benchmarking Tool (`benchmark.py`)

To measure and compare planning latency, duration, waypoint count, clearance, and success rates between CBiRRT and cuRobo in simulation:

```bash
# Run benchmark comparing CBiRRT vs cuRobo (default 5 trials per motion scenario):
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/benchmark.py --trials 5

# Benchmark right arm:
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/benchmark.py --arm right --trials 3

# Export results to JSON:
/home/arxzwood/.local/share/mamba/envs/env_curobo/bin/python sim/planner/benchmark.py --json-out benchmark_results.json
```

In the Web Studio UI, click the **Benchmark** button in the header toolbar to trigger or view the benchmark comparison modal directly in the browser!

---

## 6. Physical Robot Hardware Connection & HITL Testing

The motion planner includes a dedicated **Hardware Bridge & Safety Preflight Layer** (`sim/planner/hardware_bridge.py`) that connects the simulation and studio planner to the physical OpenArm robot on the OpenDubs platform.

### Hardware Safety Protocol:
Every trajectory is subjected to strict preflight validation and live runtime gating before and during physical execution:
1. **RC Deadman Switch (CH6) & Mode Authority Enforcement:**
   - Physical arm motion strictly follows the OpenDubs platform safety protocol.
   - Telemetry is queried in real-time from `opendubs-dash.service` (`GET http://127.0.0.1:8080/api/choreo/robot`).
   - **Pre-execution Gate:** Refuses physical trajectory dispatch unless:
     - `deadman == True` (RC controller switch CH6 is actively held down)
     - `estop == False` (emergency stop button is released)
     - `motion_power == True` (safety contactors are closed and energized)
     - `ch5 in ("AUTO", "MANUAL")` (not in STANDBY or OFF)
   - **Mid-Trajectory Safety Watchdog (80 ms cycle):** If the RC Deadman (CH6) is released or E-Stop is triggered mid-motion, the watchdog immediately cancels the ROS 2 goal handle and executes an emergency hold (`/usr/local/bin/openarm-up hold <arm>`) to freeze the arm in place safely.
   - *Note: HITL Simulation mode (`--hitl`) bypasses the RC deadman requirement, allowing offline testing and dry-runs without a physical controller.*
2. **Fingertip Workspace Guard (`HAND_BOX`):**
   Computes forward kinematics of the controller's exact Tool Center Point ($\text{link7} + 186\text{ mm}$ grasp center) across all trajectory waypoints. Rejects any motion that leaves the controller's bounding box ($x, y \in [-0.8, 0.8]\text{ m}, z \in [0.0, 1.3]\text{ m}$ less $20\text{ mm}$ margin), matching `software/choreo/tcp.py`.
3. **Velocity Trip Protection (`TRIP_DEG_S`):**
   Verifies joint velocities against motor trip limits (`J1,J2: 960°/s`, `J3,J4: 312°/s`, `J5-J7: 1200°/s`) to guarantee the physical motor drivers will never trip or freeze mid-motion.
4. **Collision & Clearance Guard:**
   Ensures continuous MuJoCo collision clearance $\ge 15.0\text{ mm}$ across the entire path.

### Execution Transports & Architecture:
- **`hitl` (Hardware-in-the-Loop Simulation):** Runs a 50 Hz closed-loop simulated execution with real-time progress and visualizes the moving arm live in the 3D studio.
- **`remote_ssh` (Remote Thor Transport):** When running off-board on a development laptop, automatically detects the robot at `andrew@10.42.2.10` and dispatches trajectory action goals directly into the `openarm` ROS 2 container over passwordless SSH.
- **`ros2` (Direct Action Client):** Directly commands the active ROS 2 action servers (`/{arm}_controller/follow_joint_trajectory` and `/{arm}_gripper_controller/gripper_cmd`).
- **`docker` (Container Exec Bridge):** Dispatches trajectory goals directly into the `openarm` container via `openarm-up` / ROS 2 CLI.
- **`Emergency Hold (E-Stop)`:** Sends immediate goal cancellation and issues `openarm-up hold <arm>` to freeze the arm in place safely without dropping.

### Running from the CLI:
```bash
# Validate and test trajectory in HITL loopback:
python3 -m sim.planner.execute_hardware --arm left --start stow --goal reach_forward --hitl

# Execute on physical robot hardware (requires RC Deadman CH6 held):
python3 -m sim.planner.execute_hardware --arm left --start stow --goal reach_forward --live
```

---

## 7. Hosting on Robot Hardware (Thor)

The OpenArm Planner Studio is hosted directly on Thor as a systemd service, making it accessible to anyone on the robot's local Wi-Fi network:

- **Web Studio URL:** `http://10.42.2.10:8095`
- **Shared Repository Path:** `/opt/opendubs-planner` (owned by group `opendubs-users`)
- **Systemd Service:** `opendubs-planner.service`

### Updating Code on Thor:
```bash
# Push latest commits directly from your development machine to Thor:
git push andrew@10.42.2.10:/opt/opendubs-planner feat/arm-motion-planner-ui

# Restart the planner studio service:
ssh andrew@10.42.2.10 "sudo systemctl restart opendubs-planner.service"

# View live service logs:
ssh andrew@10.42.2.10 "journalctl -u opendubs-planner.service -f"
```

---

## 8. Complete Verification Test Suite

Run the complete end-to-end verification suite across all 8 test sections:
```bash
python3 sim/planner/test_motion_planner.py
```

### Covered Test Sections:
1. `SSIK Analytical Kinematics & FK Consistency` (reconstruction error: 0.0000 mm)
2. `MuJoCo Collision Checking & Clearance Guard` (stow safe: 39.9 mm, head contact caught)
3. `CBiRRT Motion Planning & TOPP-RA Retiming` (spline limits, velocity bounds)
4. `Modular Backend Registry & cuRobo Adapter` (GPU CUDA trajectory, custom backend swap)
5. `Studio HTTP Server & REST API Endpoints` (`/api/status`, `/api/presets`, `/api/ik`, `/api/plan`, `/api/benchmark`, `/api/export_show`, `/api/hardware/execute`)
6. `Hardware Bridge Safety Preflight & HITL Loopback` (bounds, velocities, 50 Hz progress)
7. `Choreography Sequence Planning & Gripper Lifecycle` (multi-keyframe transitions + gripper open/close)
8. `Remote Hardware Bridge Transport & Preflight Validation` (SSH transport detection, safety validation)

