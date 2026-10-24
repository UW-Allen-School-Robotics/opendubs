# ARCHITECT on OpenDubs

The OpenDubs backend for **ARCHITECT** ([project page](https://robo-architect.github.io/),
[arXiv:2607.23784](https://arxiv.org/abs/2607.23784)): a coding agent that turns a spoken
instruction into a Python program over the robot's perception and control primitives, runs it,
checks the outcome with camera questions, and takes corrections in plain language. The agent
itself is the `ssa` research code (the development version of
[architect-franka](https://github.com/robo-architect/architect-franka)); this directory holds only
what is specific to OpenDubs.

**Status: at the desk only.** The API, its dry-run robot, the ROS 2 client and the robot-side arm
service (`live_arms.py`, `robot_node.py`) are written and tested against fake arms and the twin.
The arm service has not moved a real arm, and must not until the items under
[Before the real arms](#before-the-real-arms) are done. First target: the Halloween demo, a candy
handout triggered by "trick or treat".

## Layout

| Path | What |
|---|---|
| `opendubs_architect/api.py` | The API the model reads (`API_SPEC`), the list of primitives and their services (`PRIMITIVES`), argument checks |
| `opendubs_architect/client.py` | The live ROS 2 client: one service call per primitive, a timeout on every call, `MotionRefused` when a safety gate says no |
| `opendubs_architect/dryrun.py` | The dry-run robot: the client's signatures and argument checks, plausible replies |
| `opendubs_architect/config.py` | `OpenDubsConfig`, the `RobotConfig` ssa loads, and the backend switch |
| `opendubs_architect/live_arms.py` | The arm service: each move planned on the twin, checked, and sent behind the runner's gates; `ShowRecorder` for the fallback |
| `opendubs_architect/robot_node.py` | The ROS 2 node serving the arm primitives under `/robot/` over `live_arms` |
| `opendubs_architect/show_backend.py` | The fallback backend: a program's arm calls recorded as a show |
| `ros/robot_api_interfaces/` | The ROS 2 service types (`RobotQuery`, `RobotCommand`, `ResultCode` with `REFUSED`) |
| `skills/*.md` | What the model is told about this robot: motion, grasping, verification, camera questions, handoff, navigation |
| `tests/test_api.py` | The spec, the client, the dry-run robot and the show backend offer the same functions with the same signatures |
| `tests/test_live_arms.py` | The arm service's gates: the deadman, unknown state, the workspace, the twin's clearance check; a recorded program is a valid show |

## Run it at a desk

ssa needs its `opendubs` branch, which loads a robot backend from outside its tree
(`SSA_ROBOT_PLUGINS`), lets a robot switch off agent tools (`run_ros2_command` is off here) and
skips ssa's client-side CuRobo stub when the robot checks its own motions.

```bash
export SSA_ROBOT_PLUGINS=opendubs=opendubs_architect.config:OpenDubsConfig
export PYTHONPATH=<this repo>/software/architect:<ssa checkout>
cd <ssa checkout>
python3 scripts/ssa_cli.py --robot opendubs --dry-run --program my_program.py     # no model, no robot
python3 scripts/ssa_cli.py --robot opendubs --dry-run "Hand a candy to the person" # needs ANTHROPIC_API_KEY
python3 <this repo>/software/architect/tests/test_api.py                          # stdlib only
python3 <this repo>/software/architect/tests/test_live_arms.py                    # + numpy, PyYAML; mujoco for the twin
```

## Two backends for the arms

`OPENDUBS_ARCHITECT_BACKEND` says what carries out a program's arm and gripper calls. Both plan
every move the same way, on the twin (`live_arms.Planner`): the twin's IK (`software/choreo/ik.py`)
for a target, the shows' joint limits and speed caps, and the 30 mm clearance check from where the
arm is to where it is going.

- **`live`** (the default): one checked move per call, on the robot, as the program runs
  (`robot_node.py`). The gates are the show runner's (`runner.preflight`, `runner.monitor`): AUTO,
  the deadman held at the request and for the whole move, no e-stop, no fault, nothing unknown.
- **`show`** (the fallback): the approved program is run once and its arm calls are written as a
  show (`OPENDUBS_ARCHITECT_SHOW`, default `~/.cache/opendubs/architect.yaml`). It is compiled and
  stamped like any show (`ik.py check`, `sweep.py stamp`), and played by the runner on each
  trigger. Targets are fixed when it is recorded; `place_in_hand` offers at the named pose `offer`.

The robot's own limits come from its arm config (`/etc/opendubs/architect-arms.yaml`, or
`OPENDUBS_ARCHITECT_ARMS`; the template is `live_arms.CONFIG_TEMPLATE`): each arm's workspace box
and its named poses. With no file, the arms take `stow` only.

```bash
# the robot side, moving nothing (fake arms, a fake deadman held in AUTO):
python3 -m opendubs_architect.robot_node
# record a program as a show at the desk:
OPENDUBS_ARCHITECT_BACKEND=show python3 scripts/ssa_cli.py --robot opendubs --dry-run --program my_program.py
```

## Before the real arms

- **One motion source at a time.** The arm lease is designed, not built, so nothing stops the runner
  and this service from both sending goals. The Arms and Choreography lanes sign off on a second
  motion source first.
- **The head and the lift.** Their positions are not in the robot state the dashboard serves, so the
  clearance check assumes the arm config's `head` poses and `lift_mm`.
- **Orientation.** The twin's IK places the tool point and does not hold an orientation.
- **The frame.** The twin's chassis frame is taken to be `base_link`; the Thor stack lane confirms.
- **The planner.** A move is a smoothed straight line in joint space, refused if it passes too close.
  CBiRRT from `sim/planner` (on `feat/arm-motion-planner-ui`) could route around instead.

## The robot side: one ROS 2 service per primitive

Each primitive is a service `/robot/<category>/<name>` (the table is `api.PRIMITIVES`) of the
JSON-over-ROS types from architect-franka's `robot_api_interfaces`, rebuilt for ROS 2 Jazzy:

- `RobotQuery`: no request fields; response `ResultCode result_code`, `string data` (JSON).
- `RobotCommand`: request `string req` (JSON); the same response.
- `ResultCode`: `SUCCESS=0, FAILURE=1, INVALID_INPUT=2, TIMEOUT=3, SERVICE_NOT_RUNNING=4`, plus
  OpenDubs' own **`REFUSED=5`**: a safety gate said no. The client raises `MotionRefused`, and the
  generated program is told never to catch it.

**The services, not the program, decide whether anything moves.** A motion service runs only
with CH5 in AUTO and the deadman held, through the same gate the show runner uses
(`software/choreo/arm_output.py`), and holds the arm where it is the moment either is lost.
Workspace bounds, named poses and grip effort live on the robot and are read by the program
(`get_workspace`, `get_named_poses`), never written into a prompt.
