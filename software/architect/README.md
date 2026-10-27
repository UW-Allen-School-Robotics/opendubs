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
| `opendubs_architect/routing.py` | For a recorded show: a move the straight-line planner refuses as too close is routed around the body by the arm motion planner (CBiRRT, `sim/planner` on `feat/arm-motion-planner-ui`); every leg is checked as a straight move is |
| `opendubs_architect/robot_node.py` | The ROS 2 node serving the arm primitives under `/robot/` over `live_arms` |
| `opendubs_architect/demo_runner.py` | The Halloween runner: instruction, spoken yes, frozen program, "trick or treat" runs; a browser page shows the code |
| `opendubs_architect/listener.py` | The runner's ears and voice on the Jabra speakerphone: "OpenDubs", "trick or treat", yes and stop, each checked by Parakeet before it is posted |
| `opendubs_architect/chat.py` | What the robot says to visitors, from its own local model (the voice service's Qwen on vLLM): a varied greeting as each run starts, short answers while armed; words only, never posted to the runner |
| `opendubs_architect/camera_feed.py` | The Orbbec's colour image as a small JPEG on 127.0.0.1:8097 for the dashboard's ARCHITECT tab; subscribed only while asked (`dev.sh camera`) |
| `opendubs_architect/kws.py`, `alsa.py` | Keyword spotting on the Thor (sherpa-onnx, the face page's pinned model), and the speakerphone through libasound |
| `opendubs_architect/ssa_agent.py` | ARCHITECT (ssa's session) behind the runner, with library helpers copied into each program |
| `opendubs_architect/show_backend.py` | The fallback backend: a program's arm calls recorded as a show |
| `ros/robot_api_interfaces/` | The ROS 2 service types (`RobotQuery`, `RobotCommand`, `ResultCode`), identical to perception's |
| `opendubs_architect/perception.py`, `camera_frames.py` | The perception services as `detect_objects` and `locate_object`, their answers in the chassis frame |
| `skills/*.md` | What the model is told about this robot: motion, grasping, verification, camera questions, handoff, navigation |
| `image/`, `tools/dev.sh` | The `archit-architect` image (built on `archit-perception:dev`, with the listener's speech layer; `speech_no_gpl.py` checks that layer) and how it and the listener run on opendubs2 |
| `tools/workspace_box.py` | Proposes each arm's workspace box from the twin, and checks the moves inside it |
| `examples/arms_wave.py` | An arms-only program for the first runs: no model needed |
| `tests/test_api.py` | The spec, the client, the dry-run robot and the show backend offer the same functions with the same signatures |
| `tests/test_demo_runner.py` | The runner: nothing runs without a yes and a held deadman, nothing said while armed reaches the model, the approved text is what runs |
| `tests/test_listener.py` | The listener: a hit alone posts nothing, "trick or treat" only while armed and confirmed, a bystander's sentence is no approval, the robot never hears itself |
| `tests/test_perception.py` | Camera answers land in the chassis frame; the robot description's camera chain matches the twin's |
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

### The voice: a Jabra speakerphone on the Thor

`listener.py` hears through a USB Jabra SPEAK 510 plugged into the Thor and speaks through it, so
its echo canceller has the robot's voice to subtract. It spots "OpenDubs" (since 2026-10-26; "Hey
OpenDubs" before, and still heard) and "trick or treat"
on the Thor (`kws.py`), transcribes each candidate with Parakeet, and posts to `/api/heard` only
what passes; the rules are in its docstring. "Trick or treat" needs no wake phrase but counts only
while a program is armed, and the runner still needs the deadman. While the runner waits for a yes,
a reply of three words or fewer is heard without the wake phrase; while a program runs, so is
"stop". It reuses the voice service's modules (`wakeword.py`, `vad.py`, `speech.py`) without
changing them, and it is not part of the voice service, which never reaches motion.

It runs in the `archit-architect` image, which `dev.sh build` now builds FROM
`archit-perception:dev` (Kokoro uses perception's PyTorch; perception's PyTorch stack is held where
it is) with a speech layer installed the way the voice image installs it. The listener is its own
container, `archit-architect-listener`, with the Jabra (`/dev/snd`, the audio group), the voice
service's models read-only, and the spotter's model in `~/architect-data/models`:

```bash
software/architect/tools/dev.sh build             # needs archit-perception:dev on the robot
software/architect/tools/dev.sh kws               # once: the spotter's model, verified against its pins
software/architect/tools/dev.sh butler            # once: the butler's voice, verified against its pins
software/architect/tools/dev.sh listen            # Kokoro on the GPU (TTS_DEVICE=cpu: about 2 s a line)
LISTEN=1 software/architect/tools/dev.sh dry      # the runner, speaking through the listener
```

**The voice is a butler's** (`butler.py`): Kokoro's British man, `bm_george`, in British English,
a touch slower than Kokoro's usual pace. It uses the voice service's own Kokoro weights and its
no-GPL rule. Words outside misaki's British dictionary go to misaki's own British fallback model
(Apache-2.0), not espeak-ng, as the American voice's go to the American one. The voice and that
model are pinned by sha256 and fetched once by `dev.sh butler`. `VOICE=heart` gives the voice
service's American voice (`af_heart`) instead.

**The robot talks back** (`chat.py`), from the robot's own language model: Qwen3-30B-A3B on vLLM,
the voice service's local brain, called with the voice service's serving profile. As each run
starts, it says one greeting, different for each visitor ("Pumpkin power, hope this treat gives
you a sweet ride!"); "OpenDubs, are you a real robot?" while armed gets a one- or two-sentence
answer. The model gets no tools and its words go only to the speaker: a question never reaches the
runner, so it never becomes a program or a run, and each answer passes the voice service's own
check (`spoken.Gate`), so the robot never promises a move it will not make. Nothing leaves the robot
and nothing is kept between visitors. Each takes 0.2 to 0.5 s; if the model is slow or down, a
fixed line is said. `--no-chat` turns it off.

**What it hears, live:** the listener serves a page on its own port, 8099 (`GET /`; tunnel it with
`ssh -L 8099:127.0.0.1:8099`): each clip Parakeet transcribed and what the listener did with it, the
phrases the spotter caught, the robot's lines, the base-light flash, a microphone meter and whether it is
deaf while the robot talks. The last 200 events, in memory only: the log keeps no transcript a person
did not post. A menu item and page on the dashboard itself is the ARCHITECT page task below.

**On the night: a pre-selected program, no model calls.** `--agent program --program handout.py`
offers that file for any instruction, so ARCHITECT and its API are not called at all (the local
Qwen still greets each visitor; it is on the robot and costs no API calls). Once, at the
start: "OpenDubs, hand out candy", the robot reads the program's summary, "yes", and it is
armed. From then on each "trick or treat" runs the same frozen program again, back to ARMED after
each run; nothing is regenerated. "Stop" disarms it, and the same instruction and "yes" arm it
again. A "trick or treat" before it is armed is dropped. Run it with the live arm backend, not
`--publish`: on the show path the Shows page runs the show and "trick or treat" is ignored.

A spoken "trick or treat" starting an arm run was approved for the Halloween demo on 2026-10-24;
it is an exception made for this demo, with the deadman and the frozen program as its gates.

**The first Jabra take** (2026-10-24, one speaker, a quiet room, replayed through the whole
listener): at 1 m the spotter caught 3 of 3 "Hey OpenDubs" and "trick or treat" became a trigger 2
times of 3; at 3 m one "trick or treat" of one became a trigger; at 2 m nothing was caught. Speech
from 2 m arrived about 15 dB below speech at 1 m, so **the Jabra must sit within about a metre of
where the supervisor and the children stand.** Parakeet's spellings on the Jabra ("Hey, opened
up", "Hey overdubs", "Hey, open dogs", "check or cheat", "trigger treat") are accepted after a spotter hit (`listener.wake_rest`,
`listener.is_trick_or_treat`).

**The second take** (the same day, 1 m, then music and other people talking nearby): all 4 whole
instructions reached the runner; every "yes", "no" and "stop", alone or after the wake phrase, reached it
while it waited for a yes or ran a program, three of them over music; and no "trick or treat" was
triggered by the music or the talk. Over music the spotter missed most wake phrases, so **a new
instruction needs the music down**.

Not yet done: an hour of show music with nobody saying either phrase (one minute is not a false
trigger rate).

**[TODO] An ARCHITECT page on the dashboard**, with its own menu item: the program being run, so
the operator sees on the dashboard what "trick or treat" will run, without a second browser tab.
- **What it shows:** the runner's state (IDLE, AWAITING_YES, ARMED, RUNNING, CHECK_FAILED), the armed
  program's summary, its full code and its hash, the run count and the last run's outcome, and the
  recent events (who triggered, refused and why). All of it is already in the runner's
  `GET /api/state` (`demo_runner.DemoRunner.status`).
- **How:** the Shows page's pattern. A backend proxy (`backend/architect_proxy.py`, after
  `choreo_proxy.py`) forwards a small fixed set of the runner's calls on 127.0.0.1:8098 under
  `/api/architect/`, so the page talks to one origin; a page script (`frontend/architect.js`); the
  menu item and the page's section in `frontend/index.html` and `frontend/app.js`.
- **Read-only first.** No Yes, Run or approve buttons: approval stays spoken, and a run starts only
  by "trick or treat" with the deadman held. A Stop button may follow, since stopping only disarms.
  With the runner down, the page says so; it never shows a stale program as armed.
- **Owners.** `frontend/app.js`, `frontend/index.html` and `backend/app.py` are shared paths (small,
  scoped edits, said in the PR); the two new files have no lane yet, so the orchestrator is asked
  first. The runner's own page (`GET /` on 8098) stays as the desk view.

`--agent ssa` (the default) has ARCHITECT write the program. Its tools may read the robot and ask
camera questions while writing, but every motion refuses until a program is approved.

## On the real arms: what must stay true

Read this before any run that moves the arms, whichever way it is started.

**The deadman, every time anything moves.** Every path that sends an arm or gripper goal applies the
show runner's own gates. Each move or grip is checked before it is sent: `runner.preflight`, meaning
the e-stop clear, no fault, CH5 in AUTO, the arms active and the deadman held. While it runs,
`runner.monitor` re-checks the same gates every tick, and the moment one fails or stops being known
every goal is cancelled and the arms hold where they are. The paths are:

- **The live arm service** (`live_arms.LiveArms`, served by `robot_node.py`):
  - **Moves:** every move (`move_to_point`, `move_by`, `move_to_named`, `stow`) is checked twice
    before it is sent, before planning and again just before the goal goes out.
  - **Grips:** every grip (`set_gripper`) is checked before it is sent.
  - **Watching:** both are watched by the same `_watch`.
  - **Tests:** `tests/test_live_arms.py` (`Gates`) pins this down for every request.
- **Shows, including the listener's show mode** (`listener --show`, `SHOW=` in `dev.sh listen`): the
  show runner itself runs them. It applies the same gates and monitoring, and its deadman start:
  "trick or treat" only arms the run, and the supervisor's fresh deadman press is the go.
- **A move routed around the body** (`routing.py`) is for a recorded show only: it refuses to become
  live points, so it reaches the arms only through the show runner.

Keep it so. A change that sends arm goals by any other way is not ready for the robot.

**One motion source at a time.** The arm lease (plan 5.3) is not built, so nothing stops the show
runner and the live arm service from sending goals at once. Run one or the other, never both.

- `ARMS=ros dev.sh live` refuses while `opendubs-runner` is active. Stop it with whoever runs it,
  and start it again after the run.
- The show path (the Shows page, or `listener --show`) uses the show runner alone. Keep the live arm
  service (`archit-architect-arms`) down while it runs: `dev.sh down`.

**The head centred and the lift down, and left there.** The dashboard does not serve the head's pan
and tilt or the lift's height yet. So every clearance check here, and the clearance sweep of a
recorded show, assumes the arm config's `head` poses (default `[[0, 0]]`, centred) and `lift_mm`
(default 0, down).

- **Before a run:** centre the head and lower the lift fully.
- **During the run:** nothing may move them: no head tracking or gaze, no lift commands, no show
  that moves the head or the lift.
- **If either is elsewhere,** the twin's clearance is not the robot's.

**Also:** the workspace boxes must be measured for this robot (`tools/workspace_box.py`, then a look
at the robot), never widened past the arm column's front (x 0.423 in the chassis frame). The Arms lane
reviews the live arm service, since it is a second way of driving the arms.

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

The arm config's workspace box (where a program may send each gripper) comes from
`tools/workspace_box.py`, which asks the live planner, on the twin, where each arm reaches cleanly
and checks every move inside the box it proposes. Run it with the robot's own limits
(`studio.py limits --from-robot opendubs2` brings them to the laptop), then copy the box into
`~/architect-data/architect-arms.yaml` after a look at the robot. The template is
`live_arms.CONFIG_TEMPLATE`.

`dev.sh up` has ARCHITECT write the program instead of the fixed example. It needs
`~/architect-data/architect.env` (mode 600) with ssa's API settings.

## The candy-bucket hand-off (2026-10-24)

`examples/bucket_offer.py` ran on the robot from a spoken "trick or treat" (show mode, the
supervisor's deadman press): the right arm lifts clear, comes over the rim of a 9 in bucket in front
of the arm column, goes straight down to the candy, lifts it out, swings out to the right and forward,
and lets go. 17.4 s, swept at 38.9 mm. What it took, learned at the robot:

- **The chassis frame faces +x** (head screen and camera at x 0.51); the shoulders are at x 0.423. A
  target behind that line is behind the chest: the robot went exactly where the twin said, and a
  scratch workspace box that reached back to x 0.25 let the wrong target through. Keep a box in front
  of the arm column.
- **Heights:** the twin's floor is at chassis z -0.094, the base's top at 0.356. In front of the torso
  the right hand reaches no lower than about z 0.46 (x 0.48 to 0.56, right of centre) or 0.50 at x 0.60.
- **The IK keeps to the arm's current posture**, so the same target can solve cleanly from rest and
  fold the wrist into the arm column from a nearby pose (17.7 mm). A lift to a NAMED pose (joint
  angles, no IK) before the reach gave a clean approach.
- **The twin knows only the robot.** Neither the sweep nor CBiRRT knows the bucket: a straight move
  from rest passed 3 to 4.6 cm outside its wall below the rim. The program keeps clear of it by
  design, and it was checked by sampling the hand along every move against a cylinder for the bucket
  (to be made a tool if obstacles become routine).
- **Every move in it is straight.** CBiRRT routes a move the straight-line planner refuses (below); the
  hand-off needed a hand-designed route instead.

## Routing around the body (the show path)

`live_arms.Planner` moves an arm in a smoothed straight line in joint space and refuses a move that
would pass within 30 mm of the body. On the twin a reach to `[0.3, -0.05, 1.1]` is refused that
way, passing 17.5 mm from the arm column, though the pose at its end is clear. That point is
**behind** the shoulder line (the arms' first links are at x = 0.423 in the chassis frame, which faces
+x), and on the robot the hand went there, behind the chest: the twin and the robot agree, the target
was wrong. A hand-off in front of the chest, inside the template's box (x from 0.60), is a straight
move and needs no routing: `[0.62, -0.15, 1.0]` is clear at 34 mm. With `PLANNER=cbirrt` (and `PLANNER_ROOT`, a checkout of
`feat/arm-motion-planner-ui`), such a move goes to the arm motion planner's CBiRRT, and its path
comes back as four waypoints. Each leg between them is checked as a straight move is, for every head
pose in the config; the waypoints are kept inside the validator's joint margin; and they are
recorded as arm cues, so the publish-time sweep and validator check the show as the runner plays it.

```bash
PLANNER=cbirrt PLANNER_ROOT=<checkout of feat/arm-motion-planner-ui> software/architect/tools/dev.sh dry
```

On the twin (2026-10-24), a program reaching to that point, refused without it, published in
7.4 to 8.1 s in 8 runs of 8, as a 12 to 14 s show swept at 33.7 to 38.9 mm. CBiRRT
samples at random, so one call in eight found no path; the router tries twice. Routing is for a
recorded show only: the live arm service gets no router, and a routed move refuses to become live
points. cuRobo (`PLANNER=curobo`) builds on the Thor with patches but has no collision spheres for
the OpenArm yet, so it is not a router yet.

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
- **The frame.** Settled: the chassis frame is the robot description's `datum` link, not `base_link` (see Perception).
- **The planner.** A move is a smoothed straight line in joint space, refused if it passes too close.
  CBiRRT from `sim/planner` (on `feat/arm-motion-planner-ui`) could route around instead.

## The robot side: one ROS 2 service per primitive

Each primitive is a service `/robot/<category>/<name>` (the table is `api.PRIMITIVES`), or one of
the perception services below, of the JSON-over-ROS types in `ros/robot_api_interfaces`. Their
`.msg`/`.srv` files are kept identical to the perception services' copy
(`software/perception/robot_api_interfaces` on `archit/perception-grasp`), so one build talks to both:

- `RobotQuery`: no request fields; response `ResultCode result_code`, `string data` (JSON).
- `RobotCommand`: request `string req` (JSON); the same response.
- `ResultCode`: `SUCCESS=0, FAILURE=1, INVALID_INPUT=2, TIMEOUT=3, SERVICE_NOT_RUNNING=4,
  NOT_FOUND=5` (nothing with that label in view). The arm services add **`REFUSED=6`**, a value
  outside the message's constants so the shared message is unchanged: a safety gate said no. The
  client raises `MotionRefused`, and the generated program is told never to catch it.

## Perception

`detect_objects` and `locate_object` are the perception services of `archit/perception-grasp`
(`software/perception/API.md`): `/perception/grasp_object` (AnyGrasp's best grasp on the best match
for a label) and `/perception/locate` (the middle of its visible surface). They answer in the
colour camera's optical frame; `perception.py` and `camera_frames.py` turn that into the chassis
frame, the twin's `chassis` body and the robot description's `datum` link (0.324 m behind
`base_link`). The chain is read from `description/opendubs/opendubs.urdf` and matches the twin's
to 0.1 mm at every head pose tested. The head's pan and tilt and the lift's height are not published
yet, so they are taken from the arm config's first `head` pose and `lift_mm`. The camera's own
camera_link -> colour offset is nominal (the camera's marks) until the driver's /tf_static is read.

`PROFILE=arms_see tools/dev.sh dry|up` offers them to the program, and a show recorded at approval
goes where the camera saw things then. For those calls the container joins the robot's ROS domain
0 with `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`, as API.md says a caller must; every call only reads.

**The services, not the program, decide whether anything moves.** A motion service runs only
with CH5 in AUTO and the deadman held, through the same gate the show runner uses
(`software/choreo/arm_output.py`), and holds the arm where it is the moment either is lost.
Workspace bounds, named poses and grip effort live on the robot and are read by the program
(`get_workspace`, `get_named_poses`), never written into a prompt.
