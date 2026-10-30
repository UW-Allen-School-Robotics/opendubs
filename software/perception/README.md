# Perception services

Object detection, segmentation, 3D location and grasp poses for the robot, as ROS 2 services with
JSON payloads. Give a name ("bottle"); get back where it is, or how to grasp it.

> **Where the code is:** branch [`archit/perception-grasp`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/archit/perception-grasp)
> (from `archit/perception`). It is **not on `main`** and not part of the robot's deploy. On
> opendubs2 it runs from a clone of that branch, `~archit/opendubs-perception`, as a test install
> under the `archit` account.

## What runs

| Where | What | How it starts |
|---|---|---|
| Thor | `archit-perception-camera`: the Orbbec Femto Bolt driver (colour 1280×720 with depth aligned to it, 30 Hz) | systemd unit `archit-perception-camera.service`, at boot |
| Thor | `archit-perception-grasp`: the perception server (YOLO-World, NanoSAM, CLIPSeg on the GPU) and the node that serves the API below | systemd unit `archit-perception.service`, at boot, after the camera |
| GPU workstation | AnyGrasp, which turns an object's points into grasps (`anygrasp_server/`) | by hand on the workstation (below) |

Everything is on the robot's ROS domain **0** under the namespace **`/perception`**. The
workstation's address is read from `~archit/perception-data/anygrasp_url` on the robot (one line,
`ws://HOST:8768/grasp`), or from `ANYGRASP_URL`.

## Running it

On the Thor, as `archit`:

```sh
cd ~/opendubs-perception/software/perception

./tools/grasp_stack.sh status   # units, containers, camera rates, AnyGrasp health
./tools/perception_call.sh status   # the same through the API: "SUCCESS: all up"
```

**If it is not running** (after a reboot it should start by itself, in about 30 s):

```sh
./tools/grasp_stack.sh up       # starts what is not running and waits until it is ready
                                # (same as: sudo systemctl start archit-perception-camera archit-perception)
./tools/grasp_stack.sh logs     # follow the logs (journalctl -u archit-perception -u archit-perception-camera)
./tools/grasp_stack.sh down     # stop both (they still start at the next boot)
./tools/grasp_stack.sh install  # (re)install the two units and start them
./tools/grasp_stack.sh uninstall    # remove the units: nothing starts at boot any more
```

**AnyGrasp on the workstation** (in the environment where the AnyGrasp SDK runs, from its
`grasp_detection/` directory, with `anygrasp_server/server.py` copied there):

```sh
python3 server.py --checkpoint_path log/checkpoint_detection.tar --use_raw_best --top_down_grasp false
curl http://localhost:8768/health
```

Without it, `detect`, `segment` and `locate` still work; `grasp_object` returns
`SERVICE_NOT_RUNNING`.

**After pulling new code** into `~/opendubs-perception`: `sudo systemctl restart archit-perception`.
If `image/Dockerfile`, `perception_msgs/` or `robot_api_interfaces/` changed, rebuild the image
first: `sudo docker build -f software/perception/image/Dockerfile -t archit-perception:dev software/perception`
(from the clone's root).

## Calling the API

### From a terminal on the Thor

```sh
./tools/perception_call.sh grasp_object '{"label": "bottle"}'
./tools/perception_call.sh status
```

The first run builds `robot_api_interfaces` into `~/robot_api_ws` (about a minute, in your home;
nothing is installed on the system). With the plain ROS CLI:

```sh
source /opt/ros/jazzy/setup.bash && source ~/robot_api_ws/install/setup.bash
export ROS_DOMAIN_ID=0 FASTDDS_BUILTIN_TRANSPORTS=UDPv4

ros2 service list --no-daemon | grep /perception/
ros2 service call /perception/grasp_object robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"bottle\"}"}'
ros2 service call /perception/status robot_api_interfaces/srv/RobotQuery
```

### From your own node

1. **Build `robot_api_interfaces`** (`software/perception/robot_api_interfaces/`) into your
   workspace or image.
2. **Domain 0**, and **`FASTDDS_BUILTIN_TRANSPORTS=UDPv4`** in your process's environment.
   Without it a node on the Thor finds the service but never receives a reply: the perception
   container shares memory with the camera only, and Fast DDS tries shared memory with any process
   on the same host.
3. **Create the client once and keep it.** A client created just before each call sometimes loses
   the reply (the server's reply path to it is not matched yet; about 1 call in 20 here). If you
   must create one per call, wait half a second after `wait_for_service`, and resend a call that
   gets no answer: every call below only reads.
4. **Set a timeout.** Calls take 0.1–0.5 s; the first call with a new label takes 1–3 s.

```python
import json
import rclpy
from rclpy.node import Node
from robot_api_interfaces.msg import ResultCode
from robot_api_interfaces.srv import RobotCommand

rclpy.init()
node = Node("my_grasp_caller")
grasp = node.create_client(RobotCommand, "/perception/grasp_object")  # once, kept
grasp.wait_for_service()

fut = grasp.call_async(RobotCommand.Request(req=json.dumps({"label": "bottle"})))
rclpy.spin_until_future_complete(node, fut, timeout_sec=10.0)
res = fut.result()
if res is not None and res.result_code.result_code == ResultCode.SUCCESS:
    data = json.loads(res.data)
    p, q = data["position"], data["orientation"]   # metres, camera optical frame
    print(p["x"], p["y"], p["z"], q["x"], q["y"], q["z"], q["w"])
else:
    print("failed:", res.result_code.message if res else "no reply")
```

## The interfaces

`robot_api_interfaces` (in this directory):

```
# RobotCommand.srv                       # RobotQuery.srv
string req          # JSON input         ---
---                                      ResultCode result_code
ResultCode result_code                   string data          # JSON
string data         # JSON output

# ResultCode.msg
uint8 SUCCESS=0  FAILURE=1  INVALID_INPUT=2  TIMEOUT=3  SERVICE_NOT_RUNNING=4  NOT_FOUND=5
uint8 result_code
string message      # one human-readable line, also on success
```

| `result_code` | Means here |
|---|---|
| `SUCCESS` (0) | `data` holds the result |
| `FAILURE` (1) | it ran but could not do it: AnyGrasp found no grasp (`grasp_object`), or the object has no depth (`locate`) |
| `INVALID_INPUT` (2) | `req` is not JSON, a field is missing or wrong, an unknown camera; for `grasp_object` also an object AnyGrasp cannot use: beyond 1.5 m from the camera, or fewer than 50 pixels with depth |
| `TIMEOUT` (3) | the models (10 s) or AnyGrasp (5 s) did not answer in time |
| `SERVICE_NOT_RUNNING` (4) | the perception server, the camera, or AnyGrasp is down or unreachable |
| `NOT_FOUND` (5) | nothing with that label in view |

On anything but `SUCCESS`, `data` holds only `{"elapsed_ms": …}`; the reason is in `message`.

**Coordinates.** Every position and orientation is in the camera's optical frame,
`camera_color_optical_frame`: **x right, y down, z forward (away from the camera)**, in metres, as
seen in the colour image at `stamp` (seconds, the camera's clock). There is no transform to the
robot's base yet. Pixel boxes are `[x0, y0, x1, y1]` in the 1280×720 colour image.

### Request fields

Shared by the four `RobotCommand` services:

| Field | Type | Default | |
|---|---|---|---|
| `label` / `labels` | string / list of strings | required | what to look for: free text, e.g. `"bottle"`, `"red cup"`. `detect` and `segment` take `labels` (a single string is accepted too); `locate` and `grasp_object` take one `label` |
| `camera` | string | `"femto"` | the only camera today |
| `min_score` | number | `0` = 0.25 | the detector's score floor, 0–1 |
| `source` | string | `"auto"` | `"detector"`: one result per object (YOLO-World boxes, NanoSAM masks). `"clipseg"`: text-matched regions (touching objects may merge). `"auto"`: the detector, and regions only when it finds nothing |

Every `data` on success also has the frame it used:

```json
"camera": "femto", "frame_id": "camera_color_optical_frame", "stamp": 1792889390.669225,
"image_size": [1280, 720],
"intrinsics": {"fx": 749.12, "fy": 748.87, "cx": 646.16, "cy": 358.27},
"elapsed_ms": 290.8          // the service's own time for this call
```

### `/perception/detect` — RobotCommand

Every object matching any of the labels, best first, with its 3D centre.

```sh
ros2 service call /perception/detect robot_api_interfaces/srv/RobotCommand '{req: "{\"labels\": [\"chair\", \"bottle\", \"person\"]}"}'
```

```json
{
 "...": "the frame fields above",
 "segment_ms": 110.6,
 "instances": [
  {"index": 0, "label": "chair", "score": 0.753, "box": [448, 410, 816, 718], "mask_px": 67039,
   "center_m": [-0.1007, 0.3241, 1.196], "depth_valid": 0.97},
  {"index": 1, "label": "chair", "score": 0.731, "box": [55, 430, 313, 714], "mask_px": 38729,
   "center_m": [-0.6531, 0.2597, 1.144], "depth_valid": 0.92},
  {"index": 2, "label": "person", "score": 0.514, "box": [10, 463, 226, 715], "mask_px": 39239,
   "center_m": [-0.6439, 0.4346, 1.096], "depth_valid": 0.05},
  {"index": 3, "label": "bottle", "score": 0.314, "box": [582, 525, 644, 715], "mask_px": 9832,
   "center_m": [-0.0537, 0.4032, 1.138], "depth_valid": 0.94}
 ]
}
```

| Field | |
|---|---|
| `score` | the detector's confidence, 0–1 |
| `mask_px` | pixels in the object's mask |
| `center_m` | `[x, y, z]`: the median of the object's points (its visible surface, not its volume centre); `null` if no pixel has depth |
| `depth_valid` | the share of the mask's pixels with depth |

`NOT_FOUND` when none of the labels is in view.

### `/perception/segment` — RobotCommand

The same as `detect`, plus each object's mask when asked for.

| Extra field | Type | Default | |
|---|---|---|---|
| `include_masks` | bool | `false` | add `mask_png_b64` to each instance |

```sh
ros2 service call /perception/segment robot_api_interfaces/srv/RobotCommand '{req: "{\"labels\": [\"chair\"], \"include_masks\": true}"}'
```

`mask_png_b64` is a base64 PNG, 8-bit, the size of the colour image (`image_size`): 255 on the
object, 0 elsewhere. About 5 kB per object. In Python:
`cv2.imdecode(np.frombuffer(base64.b64decode(s), np.uint8), cv2.IMREAD_GRAYSCALE)`.

### `/perception/locate` — RobotCommand

Where one object is: the best match for `label`, as a 3D point.

```sh
ros2 service call /perception/locate robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"chair\"}"}'
```

```json
{
 "...": "the frame fields above",
 "label": "chair", "score": 0.802, "box": [446, 417, 816, 719],
 "position": {"x": -0.1005, "y": 0.3223, "z": 1.205},
 "depth_valid": 0.98, "instances_found": 2, "segment_ms": 240.5
}
```

`position` is the median of the object's points, as `center_m` above. `instances_found`: how many
matches there were; the highest-scoring one is returned. `NOT_FOUND` if there is none; `FAILURE` if
it has no depth.

### `/perception/grasp_object` — RobotCommand

How to grasp one object: the best match for `label` is segmented, and AnyGrasp computes a
parallel-jaw grasp from its points.

| Extra field | Type | Default | |
|---|---|---|---|
| `up_cam` | `[x, y, z]` | none | the world's up direction in the camera frame, if known. With it, grasps that close level are preferred, and `upright_score` / `tilt_from_floor_deg` are filled in |

```sh
ros2 service call /perception/grasp_object robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"chair\"}"}'
```

```json
{
 "...": "the frame fields above",
 "label": "chair", "score": 0.776, "box": [446, 408, 818, 718],
 "position":    {"x": -0.0456, "y": 0.3006, "z": 1.0339},
 "orientation": {"x": -0.479171, "y": -0.519996, "z": -0.519996, "w": 0.479171},
 "width": 0.0377, "grasp_score": 0.0422,
 "upright_score": null, "tilt_from_floor_deg": null,
 "mode": "raw (no up direction)",
 "instances_found": 2,
 "candidates": [
  {"position": {"x": -0.03, "y": 0.2911, "z": 1.0466},
   "orientation": {"x": -0.566846, "y": -0.345948, "z": -0.744745, "w": 0.066033},
   "width": 0.0671, "grasp_score": 0.0333, "upright_score": null, "tilt_from_floor_deg": null}
 ],
 "segment_ms": 73.7, "grasp_ms": 216.6, "elapsed_ms": 290.8
}
```

| Field | |
|---|---|
| `position` | the grasp centre, metres |
| `orientation` | quaternion (x, y, z, w) of the gripper frame, in the **GraspNet convention**: its **x axis is the approach direction** (the gripper moves along +x into the object), **y is the closing direction** (the jaws close along y), z = x × y. Map it to your gripper's tool frame before planning |
| `width` | the jaw opening, metres (at most 0.10) |
| `grasp_score` | AnyGrasp's score (higher is better; it is not a probability) |
| `upright_score` | 1 when the jaws close horizontally on a side grasp; `null` without `up_cam` |
| `tilt_from_floor_deg` | the approach's angle from the floor: 0 side grasp, 90 top-down; `null` without `up_cam` |
| `mode` | how the best grasp was chosen: `raw …` (AnyGrasp's best score) or `refined …` (score × upright², with `up_cam`) |
| `candidates` | up to 5 more grasps, best first, same fields |

Limits: the object must be within 1.5 m of the camera (else `INVALID_INPUT` with its distance),
and have at least 50 pixels with depth (else `INVALID_INPUT`; dark, shiny or see-through surfaces
often return none). The
object's mask is shrunk by 2 px before grasping, to drop the mixed depth at its edges.

### `/perception/status` — RobotQuery

Health of everything the API depends on. No request.

```sh
ros2 service call /perception/status robot_api_interfaces/srv/RobotQuery
```

```json
{
 "cameras": {"femto": {"last_info_age_s": 0.0, "camera_info_received": 635}},
 "perception_server": "up",
 "anygrasp": {"url": "ws://HOST:8768/grasp",
              "health": {"status": "ok", "busy": false, "requests": 15, "errors": 1}},
 "services": ["detect", "segment", "locate", "grasp_object", "status"],
 "uptime_s": 23.1
}
```

`SUCCESS` with message `all up`, or `SERVICE_NOT_RUNNING` with what is down (`no camera frames`,
`perception server down`, `AnyGrasp unreachable`); `data` is filled in either way.
`last_info_age_s`: seconds since the camera's last frame (above 2 s counts as down).

## Trick or treat: hand tracking and the right arm

A person raises a hand, the robot's right arm brings a candy to it. On branch
[`archit/treat-follow`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/archit/treat-follow), not on `main`;
on opendubs2 it runs from `~archit/treat-follow` (a copy of that branch). Versions are tags, `treat-follow-v0` ... `-v2.3`.

**How it sees.** The hand tracker (`hand_tracker/`, MediaPipe's hand and pose landmarkers on the Femto Bolt's colour and
depth) publishes both palms, both shoulders and the torso's angle off the camera's axis on `/perception/hand/state`. The
scripts move those into the arm's frame (`arm_mount`) through the head's joints, re-tilted to the camera's accelerometer,
and decide when a hand is held out (`treat_brain.py`). The tracker and the camera start on demand (`tools/hand_stack.sh`).

All commands on the Thor, in `~/treat-follow/software/perception/tools`. THE HEAD MOVES BY ITSELF in every mode but
`--no-head`.

### In the simulation (no real arm)

The simulated arm is the arm service itself on MuJoCo (`grasp_to_sim.sh sim`, ROS domain 78, viewer on port 8098).

```sh
# V0: live follow: the arm follows a held-out hand, home when it drops
./treat_follow.sh up            # tracker, simulated arm, follower; nothing moves yet
./treat_follow.sh start         # the head centres the person; the arm follows
./treat_follow.sh stop          # or `down`: everything off

# V1.2: one reach per run: ready -> reach_pose to a raised, still hand -> ready, then exit
./treat_follow.sh down          # `reach` refuses while a follower runs
./treat_follow.sh reach                       # a person in front of the camera
./treat_follow.sh reach --jaws-level          # ... ending with the jaws level (the real arm's default)
./treat_follow.sh reach --dummy               # no camera, no head: a scripted person raises a hand
./treat_follow.sh ctl activate --allow-large --move-seconds 8   # the simulated arm back to ready, if it wedged
```

`reach` exits 0 when the hand was reached and the arm got home, 1 when not, 2 with no hand held out in 10 s, 3 with
nobody found in 20 s.

### On the real right arm (supervised)

The arm service runs by hand from a staged copy of this branch (`treat_arm_service.sh`), so nothing in `/etc` changes
and nothing is deployed; its header has the staging steps (`/opt/treat-follow-archit`: the tree, `opendubs_arm_interfaces`
built with colcon, a setup script). **Controller:** CH5 MANUAL, CH7 OFF, the e-stop in reach, CH6 (the deadman) held
whenever the arm moves. **Do not press the dashboard's arm buttons:** the deployed dashboard starts the old stack.

```sh
A=/opt/treat-follow-archit/arm-service        # the same script as tools/treat_arm_service.sh
openarm-up status; openarm-up down             # the old stack off (--drop only with both arms at stow)
$A up                                          # motors on, holding: no motion (it says if arm_right needs
                                               #   `sudo systemctl restart openarm-can@arm_right`)
$A status                                      # must list /right_arm/command
$A ctl supervise
$A ctl activate                                # DEADMAN: stow -> ready
./treat_follow.sh reach-real --head --left-stowed   # DEADMAN: ready -> centre the head -> a raised, still hand ->
                                                    #   one move there, jaws level -> 5 s -> ready
$A ctl park                                    # DEADMAN: ready -> stow
$A ctl unsupervise; $A down
```

`reach-real` waits for the deadman before anything moves, and letting go of it at any time stops the arm and ends the
script (exit 4); nothing moves again after that. Its limits: the head and the arm never move at the same time; the
hand stops 20 cm short of the palm and at most 25 cm above the arm's shoulder; joints at 0.4 rad/s (`--speed`, up to
0.6). Options: `--no-head` keeps the head still, `--head-pan` pans as well, `--no-jaws-level` ends with the IK's own
orientation (V2.2), `--jaws-steps` reaches in eight `reach_pose` steps instead of one move, `--no-return` stays at the
treat, `--treat-s` sets the wait. If the driver says "CAN bus 'arm_right' is not healthy" after the old stack ran, cycle
the e-stop, restart the link, and `$A up` again.

### As ROS 2 services (branch `archit/treat-follow-ros`)

The same steps as separate services, `robot_api_interfaces` like the rest of `/perception`: `RobotCommand` with a JSON
`req`, a result code and JSON `data`; each returns when its step has ended (done, failed, interrupted). One node serves
them (`tools/treat_services.py`); `tools/treat_sequence.py` is the program: plain calls in order, each one's data into
the next. Nothing else passes between them (the services read the tracker and the arm's joints themselves).
**Every service, its type, request fields and response data: [TREAT_SERVICES.md](TREAT_SERVICES.md).**

| `/perception/treat/...` | Request | Does |
|---|---|---|
| `ready` | `{}` | the arm to ready |
| `move_pose` | `{"position": [x,y,z], "orientation"?: [w,x,y,z], "speed"?}` | the hand to a pose (`reach_pose`) |
| `move_joints` | `{"joints": [7 rad]}` or `{"joints_deg": [7]}`, `"speed"?` | one continuous, collision-checked joint move |
| `centre_person` | `{"timeout_s"?: 20}` | the head centres the person and holds, the shoulders 15 deg above the picture's centre so the hands stay in frame (`--head-aim-deg`) |
| `get_hand` | `{"timeout_s"?: 10}` | a hand held out and still: `side`, `palm` (arm_mount) |
| `find_hand` | `{"find_s"?: 20, "wait_s"?: 10}` | `centre_person`, then `get_hand` |
| `plan_treat` | `{"palm": [x,y,z], "jaws_level"?: true}` | no motion: `joints`, `pose` toward that palm |
| `hold` | `{"seconds"?: 5}` | wait at the treat |
| `gripper_open`, `gripper_close` | `{}` | the gripper (the arm service's `open_gripper` / `close_gripper` commands, 2026-10-29); a close that stops on something is "holding", one that never moved or closed on nothing fails. **Not yet run on opendubs2 this way** (below) |
| `stop`, `rearm` | `{}` | cancel any move (the arm holds); clear a latched stop |
| `status` | `RobotQuery` | mode, permit, stopped, busy, the arm's joints, the head |

**On the real arm, two commands** (on the Thor, in `software/perception/tools`; CH5 MANUAL, CH7 OFF, the e-stop in
reach, no dashboard arm buttons):

```sh
./treat_follow.sh prepare        # 1. everything ready; HOLD THE DEADMAN when it asks (from stow it activates the arm)
./treat_follow.sh sequence       # 2. HOLD THE DEADMAN: ready -> find the hand -> one move there -> 5 s -> ready
./treat_follow.sh sequence       #    again, as often as you like (the arm ends at ready, the services stay up)
```

`prepare` (`tools/treat_prepare.sh`) does, in order, and stops at the first thing it cannot put right safely, saying
what to do: the controller (CH5, the e-stop, motion power); the old arm stack off (`openarm-up down`); the right arm's
CAN link (restarted if not `ERROR-ACTIVE`); the arm service from `/opt/treat-follow-archit` up, `/right_arm/command`
listed, supervised; the arm at ready (from stow: waits for the deadman, then `activate`; from anywhere else it stops:
`ctl park --allow-large` with someone watching); the treat services up (`--real --head --left-stowed`, or the options
you give it). `prepare --check` only looks and reports. When you are done: `arm-service ctl park` (deadman), then
`./treat_follow.sh services-down` and `/opt/treat-follow-archit/arm-service down`.

Everything by hand, or in the simulation:

```sh
./treat_follow.sh services --dummy            # simulation, a scripted person (no camera, no head)
./treat_follow.sh services                    # simulation, the camera and the head
./treat_follow.sh services --real --head --left-stowed   # THE REAL ARM (arm service up, supervised, activated)
./treat_follow.sh sequence                    # ready -> find_hand -> plan_treat -> move_joints -> hold -> ready
./treat_follow.sh sequence --gripper          # ... gripper_close first and gripper_open at the treat (once calibrated)
./treat_follow.sh call find_hand '{}'         # any one service (sent once: never resent)
./treat_follow.sh services-down
```

One step at a time (a second call is refused while one runs); the head and the arm never move together. With `--real`
the deadman must be held before the sequence starts: a move is refused unless it is held, and releasing it during a run
cancels the move and latches a stop that refuses every later move until `rearm`. `services-down` stops the node (TERM,
then KILL after 5 s); a move it interrupts ends and the arm holds.

**The gripper on opendubs2 is not calibrated for the arm service** (2026-10-28). The robot's
`/etc/opendubs/openarm-gafro-right-only.yaml` has `use_gripper: false`, and the arm service needs the gripper motor's
MEASURED angles at fully open and fully shut. A trial with angles derived from the old stack's finger calibration
(`gripper_open_position: -1.16`, `gripper_closed_position: 0.02`) failed: the gripper never moved, `activate` then
stopped on its tracking check, and the arm service exited with the arm held part-way. Recovery: one person supported the
arm, the e-stop was cycled and the arm lowered by hand, then `openarm-can@arm_right` restarted, `arm-service up` and
`ctl park --allow-large`. Until the angles are measured, leave `--gripper` off: the sequence then works as tested.
In the simulation the OpenArm has no gripper ("the robot's grippers: []"); the services were checked against a stand-in.

Pitfalls seen on the robot: run `arm-service up` in a command of its own, because the arm service refuses to start if
any command line on the machine contains `ros2_control_node`, its own launcher's included. If `up` says
"CAN bus 'arm_right' is not healthy" after the arm was in error, the e-stop cycle clears it, but the arm goes limp:
support it first unless it is at stow.

## Measured on opendubs2 (2026-10-24)

From a terminal on the Thor: `status` 51 ms; `locate` 130–250 ms; `detect` / `segment` 150–900 ms;
`grasp_object` 300 ms – 1.4 s (1.6 s for the first call after a boot). 40 calls in a row from fresh
clients: all answered. The units came back on their own after a reboot.

## Files

| Path | |
|---|---|
| `perception_tasks/api.py` | the five services |
| `perception_tasks/grasp_object.py`, `perception_clients/anygrasp.py` | segmentation, then AnyGrasp |
| `perception_server/` | the models |
| `robot_api_interfaces/` | the service and message definitions |
| `tools/grasp_stack.sh`, `tools/serve_grasp.sh`, `deploy/*.service` | running it |
| `tools/perception_call.sh`, `tools/perception_call.py` | calling it from a terminal |
| `anygrasp_server/` | the workstation's AnyGrasp server |
| `notes/workplan-perception.md` | how it was built and tested |
| `hand_tracker/`, `tools/hand_stack.sh` | trick or treat: the MediaPipe hand tracker and its container |
| `tools/treat_follow.sh` | trick or treat: every mode (`up`/`start` V0, `reach`, `reach-real`) |
| `tools/treat_follow.py`, `tools/treat_reach.py`, `tools/treat_brain.py` | the live follower, the one-shot reach, the decisions |
| `tools/treat_arm_service.sh` | the real arm's service by hand from this branch |
| `tools/treat_services.py`, `tools/treat_sequence.py` | the treat steps as ROS 2 services, and the program that calls them |
| `TREAT_SERVICES.md` | those services' API: types, requests, responses, result codes |
| `tools/treat_prepare.sh` | `treat_follow.sh prepare`: the real arm and the services ready for `sequence` |
| `tests/test_treat_brain.py` | the decisions that, wrong, move the arm or head when they should not |
