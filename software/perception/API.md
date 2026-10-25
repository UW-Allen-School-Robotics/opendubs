# Perception API

ROS 2 services for finding objects and grasping them. Each takes and returns JSON.

| Service | Type | Does |
|---|---|---|
| `/perception/detect` | `robot_api_interfaces/srv/RobotCommand` | every object matching the labels, with its 3D centre |
| `/perception/segment` | `robot_api_interfaces/srv/RobotCommand` | the same, with each object's mask |
| `/perception/locate` | `robot_api_interfaces/srv/RobotCommand` | the 3D position of the best match for one label |
| `/perception/grasp_object` | `robot_api_interfaces/srv/RobotCommand` | a grasp pose (position + quaternion) for the best match for one label |
| `/perception/status` | `robot_api_interfaces/srv/RobotQuery` | health of the camera, the models and the grasp planner |

## Code

```sh
git clone git@github.com:UW-Allen-School-Robotics/opendubs.git
cd opendubs
git checkout archit/perception-grasp
```

Everything is under `software/perception/`. The service and message definitions are in
`software/perception/robot_api_interfaces/`.

## Starting the perception stack on the robot

On the Thor it starts at boot. To check it, and to start it if it is not running (in the clone at
`~/opendubs-perception`):

```sh
cd ~/opendubs-perception/software/perception
./tools/grasp_stack.sh status       # what is running
./tools/grasp_stack.sh up           # start whatever is not running; waits until the services are ready
./tools/grasp_stack.sh logs         # follow the logs
./tools/grasp_stack.sh down         # stop
```

`up` is the same as `sudo systemctl start archit-perception-camera archit-perception`. It runs two
containers: the camera driver (Orbbec Femto Bolt) and the perception server with these services.

`grasp_object` also needs the AnyGrasp server on its GPU workstation (`software/perception/anygrasp_server/`,
started there with `python3 server.py --checkpoint_path log/checkpoint_detection.tar --use_raw_best --top_down_grasp false`).
Its address is the one line in `~/perception-data/anygrasp_url` on the robot. The other services
work without it.

After changing the code, restart: `sudo systemctl restart archit-perception`. After changing
`image/Dockerfile`, `perception_msgs/` or `robot_api_interfaces/`, rebuild the image first, from
the clone's root:

```sh
sudo docker build -f software/perception/image/Dockerfile -t archit-perception:dev software/perception
sudo systemctl restart archit-perception
```

## Building the interfaces to call the API

A caller needs `robot_api_interfaces` built in its own workspace (ROS 2 Jazzy):

```sh
mkdir -p ~/robot_api_ws/src
cp -r <repo>/software/perception/robot_api_interfaces ~/robot_api_ws/src/
cd ~/robot_api_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select robot_api_interfaces
source install/setup.bash
```

Or, on the Thor, `software/perception/tools/perception_call.sh` builds it into `~/robot_api_ws` on
its first run.

Every process that calls the services needs:

```sh
export ROS_DOMAIN_ID=0
export FASTDDS_BUILTIN_TRANSPORTS=UDPv4
```

Without `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`, a caller on the Thor finds the services but never
receives a reply.

## Calling it

From a terminal on the Thor:

```sh
./tools/perception_call.sh grasp_object '{"label": "bottle"}'
./tools/perception_call.sh status
```

With the ROS CLI (interfaces sourced, environment set as above):

```sh
ros2 service list --no-daemon | grep /perception/
ros2 service call /perception/grasp_object robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"bottle\"}"}'
ros2 service call /perception/status robot_api_interfaces/srv/RobotQuery
```

From Python:

```python
import json
import rclpy
from rclpy.node import Node
from robot_api_interfaces.msg import ResultCode
from robot_api_interfaces.srv import RobotCommand

rclpy.init()
node = Node("my_grasp_caller")
grasp = node.create_client(RobotCommand, "/perception/grasp_object")  # create once and keep
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

Create each client once and reuse it. A client created just before a call can miss its reply; if
you create one per call, wait about half a second after `wait_for_service` and resend a call that
gets no answer (every call only reads, so a resend is safe). Use a timeout: a call takes 0.1–1.5 s,
and the first call for a new label up to about 3 s.

## Interfaces

```
# RobotCommand.srv
string req                                      # JSON-encoded input payload
---
robot_api_interfaces/ResultCode result_code
string data                                     # JSON-encoded output payload

# RobotQuery.srv
---
robot_api_interfaces/ResultCode result_code
string data                                     # JSON-encoded payload

# ResultCode.msg
uint8 SUCCESS               = 0
uint8 FAILURE               = 1
uint8 INVALID_INPUT         = 2
uint8 TIMEOUT               = 3
uint8 SERVICE_NOT_RUNNING   = 4
uint8 NOT_FOUND             = 5

uint8 result_code
string message
```

| `result_code` | Means |
|---|---|
| `SUCCESS` (0) | `data` holds the result |
| `FAILURE` (1) | it ran but could not do it: no grasp found (`grasp_object`), or the object has no depth (`locate`) |
| `INVALID_INPUT` (2) | `req` is not JSON, a field is missing or wrong, an unknown camera; for `grasp_object` also an object farther than 1.5 m from the camera or with fewer than 50 pixels with depth |
| `TIMEOUT` (3) | the models (10 s) or the grasp planner (5 s) did not answer in time |
| `SERVICE_NOT_RUNNING` (4) | the perception server, the camera or the grasp planner is down or unreachable |
| `NOT_FOUND` (5) | nothing with that label in view |

`message` is always one readable line saying what happened. On anything but `SUCCESS`, `data` is
only `{"elapsed_ms": …}`.

### Coordinates

All positions and orientations are in the camera's optical frame, `camera_color_optical_frame`:
**x right, y down, z forward (away from the camera)**, in metres, for the colour frame taken at
`stamp` (seconds). Pixel boxes are `[x0, y0, x1, y1]` in the 1280×720 colour image.

## Request fields

Shared by `detect`, `segment`, `locate` and `grasp_object`:

| Field | Type | Default | |
|---|---|---|---|
| `labels` | list of strings | required by `detect`, `segment` | what to look for, free text: `["bottle", "red cup"]` (a single string is accepted too) |
| `label` | string | required by `locate`, `grasp_object` | one name: `"bottle"` |
| `camera` | string | `"femto"` | the camera to use |
| `min_score` | number, 0–1 | `0` (meaning 0.25) | the detector's confidence floor |
| `source` | string | `"auto"` | `"detector"`: one result per object. `"clipseg"`: regions matching the text (touching objects may merge into one). `"auto"`: the detector, then regions only if it finds nothing |

## Response fields shared by all four commands

```json
"camera": "femto",
"frame_id": "camera_color_optical_frame",
"stamp": 1792889390.669225,
"image_size": [1280, 720],
"intrinsics": {"fx": 749.12, "fy": 748.87, "cx": 646.16, "cy": 358.27},
"elapsed_ms": 290.8
```

`elapsed_ms` is the service's own time for the call; `intrinsics` is the colour camera's pinhole
model (the image is undistorted).

## `/perception/detect`

Every object matching any of the labels, best first.

Request:

```json
{"labels": ["chair", "bottle", "person"]}
```

```sh
ros2 service call /perception/detect robot_api_interfaces/srv/RobotCommand '{req: "{\"labels\": [\"chair\", \"bottle\", \"person\"]}"}'
```

Response `data`:

```json
{
 "camera": "femto", "frame_id": "camera_color_optical_frame", "stamp": 1792889356.736721,
 "image_size": [1280, 720],
 "intrinsics": {"fx": 749.12, "fy": 748.87, "cx": 646.16, "cy": 358.27},
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
 ],
 "elapsed_ms": 183.1
}
```

| Field | |
|---|---|
| `index` | position in the list, best score first |
| `label` | which of the requested labels it matched |
| `score` | the detector's confidence, 0–1 |
| `box` | `[x0, y0, x1, y1]` in pixels |
| `mask_px` | pixels in the object's mask |
| `center_m` | `[x, y, z]` in metres: the median of the object's visible points; `null` if none has depth |
| `depth_valid` | share of the mask's pixels that have depth, 0–1 |
| `segment_ms` | time spent in the models |

`NOT_FOUND` when none of the labels is in view.

## `/perception/segment`

The same as `detect`, plus each object's mask.

Request:

```json
{"labels": ["chair"], "include_masks": true}
```

| Extra field | Type | Default | |
|---|---|---|---|
| `include_masks` | bool | `false` | add `mask_png_b64` to each instance |

```sh
ros2 service call /perception/segment robot_api_interfaces/srv/RobotCommand '{req: "{\"labels\": [\"chair\"], \"include_masks\": true}"}'
```

Response `data`: as `detect`, with in each instance

| Field | |
|---|---|
| `mask_png_b64` | the mask as a base64 PNG, 8-bit, the size of the colour image: 255 on the object, 0 elsewhere (about 5 kB) |

Decoding it in Python:

```python
import base64, cv2, numpy as np
mask = cv2.imdecode(np.frombuffer(base64.b64decode(inst["mask_png_b64"]), np.uint8), cv2.IMREAD_GRAYSCALE)
```

## `/perception/locate`

The 3D position of the best match for one label.

Request:

```json
{"label": "chair"}
```

```sh
ros2 service call /perception/locate robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"chair\"}"}'
```

Response `data`:

```json
{
 "camera": "femto", "frame_id": "camera_color_optical_frame", "stamp": 1792889291.967093,
 "image_size": [1280, 720],
 "intrinsics": {"fx": 749.12, "fy": 748.87, "cx": 646.16, "cy": 358.27},
 "label": "chair", "score": 0.802, "box": [446, 417, 816, 719],
 "position": {"x": -0.1005, "y": 0.3223, "z": 1.205},
 "depth_valid": 0.98,
 "instances_found": 2,
 "segment_ms": 240.5,
 "elapsed_ms": 241.3
}
```

| Field | |
|---|---|
| `position` | metres: the median of the object's visible points |
| `score`, `box`, `depth_valid` | as in `detect` |
| `instances_found` | how many matches there were; the one with the highest score is returned |

`NOT_FOUND` if nothing matches; `FAILURE` if the match has no depth.

## `/perception/grasp_object`

A parallel-jaw grasp for the best match for one label.

Request:

```json
{"label": "chair"}
```

| Extra field | Type | Default | |
|---|---|---|---|
| `up_cam` | `[x, y, z]` | none | the world's up direction in the camera frame, if known. Grasps whose jaws close level are then preferred, and `upright_score` and `tilt_from_floor_deg` are filled in |

```sh
ros2 service call /perception/grasp_object robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"chair\"}"}'
```

Response `data`:

```json
{
 "camera": "femto", "frame_id": "camera_color_optical_frame", "stamp": 1792889390.669225,
 "image_size": [1280, 720],
 "intrinsics": {"fx": 749.12, "fy": 748.87, "cx": 646.16, "cy": 358.27},
 "label": "chair", "score": 0.776, "box": [446, 408, 818, 718],
 "position":    {"x": -0.0456, "y": 0.3006, "z": 1.0339},
 "orientation": {"x": -0.479171, "y": -0.519996, "z": -0.519996, "w": 0.479171},
 "width": 0.0377,
 "grasp_score": 0.0422,
 "upright_score": null,
 "tilt_from_floor_deg": null,
 "mode": "raw (no up direction)",
 "instances_found": 2,
 "candidates": [
  {"position": {"x": -0.03, "y": 0.2911, "z": 1.0466},
   "orientation": {"x": -0.566846, "y": -0.345948, "z": -0.744745, "w": 0.066033},
   "width": 0.0671, "grasp_score": 0.0333, "upright_score": null, "tilt_from_floor_deg": null}
 ],
 "segment_ms": 73.7,
 "grasp_ms": 216.6,
 "elapsed_ms": 290.8
}
```

| Field | |
|---|---|
| `position` | the grasp centre, metres |
| `orientation` | quaternion `x, y, z, w` of the gripper frame, GraspNet convention: **x is the approach direction** (the gripper moves along +x into the object), **y is the closing direction** (the jaws close along y), z = x × y. Map it to your gripper's tool frame before planning |
| `width` | jaw opening, metres (at most 0.10) |
| `grasp_score` | the grasp planner's score; higher is better (not a probability) |
| `upright_score` | 1 when the jaws close horizontally on a side grasp, 0 for a top-down grasp; `null` without `up_cam` |
| `tilt_from_floor_deg` | the approach's angle from the floor: 0 is a side grasp, 90 top-down; `null` without `up_cam` |
| `mode` | how the best grasp was chosen: `raw …` (the planner's best score) or `refined …` (score × upright², with `up_cam`) |
| `candidates` | up to 5 more grasps, best first, with the same fields |
| `label`, `score`, `box`, `instances_found` | the object grasped, as in `locate` |
| `segment_ms`, `grasp_ms` | time in the models and in the grasp planner |

The object must be within 1.5 m of the camera and have at least 50 pixels with depth (dark, shiny
or see-through surfaces often return none); otherwise `INVALID_INPUT` says which.

## `/perception/status`

Health of everything the other services depend on. No request.

```sh
ros2 service call /perception/status robot_api_interfaces/srv/RobotQuery
```

Response `data`:

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

| Field | |
|---|---|
| `cameras.<name>.last_info_age_s` | seconds since the camera's last frame; above 2 counts as down |
| `cameras.<name>.camera_info_received` | frames seen since the service started |
| `perception_server` | `up` or `down` (the models) |
| `anygrasp.health` | the grasp planner's own health report, or `{"status": "unreachable", "error": …}` |
| `uptime_s` | seconds since the service started |

`result_code` is `SUCCESS` with message `all up`, or `SERVICE_NOT_RUNNING` with what is down
(`no camera frames`, `perception server down`, `AnyGrasp unreachable`). `data` is filled in either
way.
