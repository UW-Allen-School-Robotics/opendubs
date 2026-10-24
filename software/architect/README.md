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
| `opendubs_architect/demo_runner.py` | The Halloween runner: instruction, spoken yes, frozen program, "trick or treat" runs; a browser page shows the code |
| `opendubs_architect/ssa_agent.py` | ARCHITECT (ssa's session) behind the runner, with library helpers copied into each program |
| `opendubs_architect/show_backend.py` | The fallback backend: a program's arm calls recorded as a show |
| `ros/robot_api_interfaces/` | The ROS 2 service types (`RobotQuery`, `RobotCommand`, `ResultCode` with `REFUSED`) |
| `skills/*.md` | What the model is told about this robot: motion, grasping, verification, camera questions, handoff, navigation |
| `image/`, `tools/dev.sh` | The `archit-architect` image (the perception image's base and layout) and how it runs on opendubs2 |
| `examples/arms_wave.py` | An arms-only program for the first runs: no model needed |
| `tests/test_api.py` | The spec, the client, the dry-run robot and the show backend offer the same functions with the same signatures |
| `tests/test_demo_runner.py` | The runner: nothing runs without a yes and a held deadman, nothing said while armed reaches the model, the approved text is what runs |
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
python3 <this repo>/software/architect/tests/test_demo_runner.py                  # stdlib only
python3 <this repo>/software/architect/tests/test_live_arms.py                    # + numpy, PyYAML; mujoco for the twin
```

## The demo runner

`demo_runner.py` holds the interaction: the supervisor's instruction becomes a program, the robot
reads its `# Summary:` line aloud, and a spoken "yes" freezes it by hash. Each "trick or treat"
with the deadman held runs that text again. While a program is armed, nothing anyone says reaches
the model: "trick or treat" runs it, "stop" disarms it, everything else is ignored. A program must
pass a desk check before it is offered: it compiles, names only primitives and what it defines,
imports only `math` and `time`, never catches a refusal, and runs once on the dry-run robot.

The voice service posts what it heard to `POST /api/heard` with `{"kind": "instruction" | "speech"
| "trigger", "text": ...}`. `GET /` is the page that shows the code, for a browser and never the
tablet; `POST /api/trigger` and `POST /api/stop` back its two buttons.

```bash
# a desk: a fixed program, the dry-run robot, the deadman held
PYTHONPATH=.:<ssa checkout> python3 -m opendubs_architect.demo_runner --agent program --program handout.py --dry-run
curl -X POST localhost:8098/api/heard -d '{"kind": "instruction", "text": "hand out candy"}'
curl -X POST localhost:8098/api/heard -d '{"kind": "speech", "text": "yes"}'
curl -X POST localhost:8098/api/heard -d '{"kind": "trigger", "text": "trick or treat"}'
```

`--agent ssa` (the default) has ARCHITECT write the program. Its tools may read the robot and ask
camera questions while writing, but every motion refuses until a program is approved.

## First runs on the robot: ARCHITECT writes a show

The first profile for the physical robot has no perception and moves nothing itself. ARCHITECT
writes an arms-only program (`OPENDUBS_ARCHITECT_PROFILE=arms`: named poses, workspace points,
grippers, stow). A "yes" records it as a show, stamps it with the clearance sweep and validates
it. From there the robot's existing path takes over: `show_sync.py` puts the show in the robot's
own folder, and the dashboard's Shows page runs it through the show runner (see
`sim/planner/INSTRUCTIONS.md` on `feat/arm-motion-planner-ui`, steps 2 and 3). It runs in a
container, `archit-architect`, built and run like `archit-perception`: on opendubs2 as `archit`,
the repository and ssa mounted read-only, data in `~/architect-data`, nothing on the host changed.

```bash
# on opendubs2, as archit, in ~/opendubs-architect (a clone of this branch); ssa in ~/ssa
software/architect/tools/dev.sh build
LIMITS=<the arm stack's joint_limits.yaml> software/architect/tools/dev.sh dry   # examples/arms_wave.py
#   page: ssh -L 8098:127.0.0.1:8098 archit@opendubs2, then http://localhost:8098/; type an instruction, Yes
software/architect/tools/dev.sh shows
software/architect/tools/dev.sh down

# on the laptop: the robot's own pre-run check first, then the push
python3 software/choreo/show_sync.py push opendubs2 architect-<sha>.yaml --dry-run --limits-from-robot
python3 software/choreo/show_sync.py push opendubs2 architect-<sha>.yaml
```

Then, on the dashboard: TELEOP, ACTIVATE L and R; Shows, the `ARCHITECT <sha>` card, ARM RUN with
LIVE on; CH5 in AUTO and the deadman held for the whole run.

`dev.sh up` has ARCHITECT write the program instead of the fixed example. It needs
`~/architect-data/architect.env` (mode 600) with ssa's API settings.

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
