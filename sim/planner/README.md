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

## 4. Web Motion Planner Studio & Halloween Show Designer

A lightweight, zero-dependency Web UI is available to jog arm poses, inspect 3D collision clearances in real time, plan collision-free motions with CBiRRT, preview trajectories with an interactive scrubber, and choreograph/export show routines for the Halloween demo:

```bash
# Launch the Motion Planner Studio
./sim/planner/ui/server.py --port 8092
```

Then open in your browser:
**`http://127.0.0.1:8092`**

### Features:
- **Interactive 3D Arm Visualizer:** Real OpenArm V1 mesh geometry in WebGL with orbit camera (ISO, Front, Side, Top, free orbit).
- **Dual-Pose Overlay:** Displays the solid starting pose and translucent ghost target pose simultaneously.
- **Real-Time Clearance & Collision Guard:** Live MuJoCo clearance calculation down to the millimeter with red collision alerts.
- **Cartesian Jogging & SSIK:** Enter or jog end-effector $(X, Y, Z)$ coordinates and solve analytical IK in $\sim 0.2\text{ ms}$.
- **Presets:** Quick selection of standard poses (`stow`, `zero`, `reach_forward`) and Halloween poses (`zombie_reach`, `spooky_arms_up`, `pumpkin_offer`, `spooky_wave`, `cauldron_stir`).
- **Interactive Trajectory Player:** Scrub through time, play back at $0.5\times / 1.0\times / 2.0\times$ speed, and view the 3D end-effector ribbon.
- **Halloween Show Choreographer:** Build an ordered sequence of keyframes, click **Plan Full Show Sequence** to generate collision-free transitions between all poses, and export directly to `software/choreo/shows/halloween.yaml` format!

