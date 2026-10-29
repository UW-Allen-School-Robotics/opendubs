# Trick-or-treat ROS 2 services: API reference

The ROS 2 services of the trick-or-treat program (branch
[`archit/treat-follow-ros`](https://github.com/UW-Allen-School-Robotics/opendubs/tree/archit/treat-follow-ros)), served
by one node, [`tools/treat_services.py`](tools/treat_services.py), and called in order by
[`tools/treat_sequence.py`](tools/treat_sequence.py). How to run them: [README.md, "As ROS 2 services"](README.md#as-ros-2-services-branch-archittreat-follow-ros).

## The interface

Every service is one of the two `robot_api_interfaces` types the rest of `/perception` uses
([`robot_api_interfaces/`](robot_api_interfaces/)):

```
# robot_api_interfaces/srv/RobotCommand          # robot_api_interfaces/srv/RobotQuery
string req           # JSON object               ---
---                                              robot_api_interfaces/ResultCode result_code
robot_api_interfaces/ResultCode result_code      string data          # JSON object
string data          # JSON object

# robot_api_interfaces/msg/ResultCode
uint8 result_code    # SUCCESS 0, FAILURE 1, INVALID_INPUT 2, TIMEOUT 3, SERVICE_NOT_RUNNING 4, NOT_FOUND 5
string message       # what happened, in words
```

- `req` and `data` are JSON objects as strings. An empty `req` means `{}`.
- **Every service returns when its step has ended:** done (`SUCCESS`), or failed, timed out or interrupted (another
  code, and `message` says why).
- **One step at a time.** A call while another runs is refused with `FAILURE`, `"busy: <name> is running"`.
  `stop`, `rearm` and `status` are always answered.
- When a step is refused, `data` is `{"stopped": <reason or null>}`. If the request is not valid JSON, `data` is `{}`.
- **Frames.** Positions are in `arm_mount`, metres: x forward, y to the robot's left, z up. Joints are the right arm's
  `openarm_right_joint1..7`. Quaternions are `[w, x, y, z]`.
- **Real arm or simulation.** The node is started either for the simulated arm or with `--real` for THE REAL ARM. On the
  real arm, every motion is refused unless CH5 is MANUAL or AUTO, the e-stop is clear and the deadman is held. Losing
  that permit during a motion cancels it (the arm holds where it is) and **latches a stop**: every later motion is refused
  until `rearm`.

## Services

All names are under `/perception/treat/`. "Moves" says what physically moves.

### Arm

#### `ready`: the arm to its ready pose
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the arm (the arm service's `ready`: a straight joint line to the model's `ready` keyframe) |
| `req` | `{}` |
| `data` on SUCCESS | `{}` |
| Fails | `FAILURE` (the move failed or was refused; on the real arm also: motion not permitted, stopped), `SERVICE_NOT_RUNNING` (no arm service), `TIMEOUT` |

#### `move_pose`: the hand to a pose
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the arm (the arm service's `reach_pose`: it plans the path itself) |
| `req` | `position` [x, y, z] **required**; `orientation` [w, x, y, z] (left out: the hand keeps its present one); `speed` rad/s; `tolerance_mm` (25) |
| `data` on SUCCESS | the arm service's own: `error_mm`, `error_deg`, `hand` ... |
| Fails | `INVALID_INPUT` (no position; real arm: z over 0.25 m above the arm's shoulder), `NOT_FOUND` (out of reach), `FAILURE` (the planner found no path, e.g. "the planned path leaves a joint's range") |

On the real arm `speed` defaults to 0.4 and is capped at 0.6 rad/s.

#### `move_joints`: one continuous move to joint angles
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the arm: a straight joint-space line from where the arm is now, checked for self-collision first (`engage.PathCheck`), minimum-jerk, played by the arm service's `follow_joint_trajectory` |
| `req` | `joints` [7 rad] **or** `joints_deg` [7] **required**; `speed` rad/s, the fastest joint's peak (0.4; real arm: at most 0.6) |
| `data` on SUCCESS | `{"seconds": 6.7, "joints_deg": [7]}` |
| Fails | `INVALID_INPUT` (not 7 joints), `FAILURE` ("the straight path collides ...": nothing sent; the move aborted; cancelled by `stop`), `SERVICE_NOT_RUNNING` (no arm joints on `/joint_states`) |

#### `hold`: wait at the treat
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | nothing (the arm stays where it is) |
| `req` | `seconds` (5) |
| `data` on SUCCESS | `{}` |
| Fails | real arm: `FAILURE` "STOPPED: ..." if the permit is lost meanwhile (the stop is latched) |

#### `gripper_open` / `gripper_close`: the gripper
**Not working on opendubs2 yet:** the gripper has no measured motor angles for the arm service (README.md).

| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the gripper (the arm service's `/right_gripper_controller/gripper_cmd`, `control_msgs/GripperCommand`) |
| `req` | `position` m of finger travel (open 0.044, close 0.0); `max_effort` (0: the driver's); `timeout_s` (10) |
| `data` on SUCCESS | `{"position_mm": 44.0, "holding": false}`; for a close that stopped on something, `"holding": true` |
| Fails | `FAILURE` ("the gripper did not move (still 42.7 mm open) ..." for a close that never moved; stalled; cancelled), `TIMEOUT`, `SERVICE_NOT_RUNNING` |

### Perception and head

#### `centre_person`: the head centres the person and holds
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the head only (never while the arm moves). It searches if nobody is in view, then centres the nearest person's shoulders 15 deg above the picture's centre (`--head-aim-deg`) so the body and the hands are in frame, and stops there |
| `req` | `timeout_s` (20) |
| `data` on SUCCESS | `{"tilt": 98.8, "pan": 93.1}` (the neck's degrees) |
| Fails | `NOT_FOUND` "nobody centred in 20 s (...)", `SERVICE_NOT_RUNNING` (the node runs without the head) |

#### `get_hand`: a hand held out and still
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | nothing |
| `req` | `timeout_s` (10); `stable_frames` (5: tracker frames in a row the hand must be out and still); `still_m` (0.05) |
| `data` on SUCCESS | `{"side": "right", "palm": [x, y, z], "frame": "arm_mount"}`: the latest palm of the still run. `side` is the person's own hand |
| Fails | `NOT_FOUND` "no hand held out and still in 10 s" |

#### `find_hand`: `centre_person`, then `get_hand`, in one call
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | the head only |
| `req` | `find_s` (20, for `centre_person`); `wait_s` (10, for `get_hand`); `stable_frames`, `still_m` |
| `data` on SUCCESS | `{"head": {"tilt", "pan"}, "side", "palm", "frame"}` |
| Fails | as `centre_person` ("nobody centred ...") or `get_hand` ("no hand held out ...") |

#### `plan_treat`: where the arm goes for a palm (no motion)
| | |
|---|---|
| Type | `RobotCommand` |
| Moves | nothing: it only computes |
| `req` | `palm` [x, y, z] **required**; `jaws_level` (true: the gripper's jaws level and pointing forward, from the candy posture; false: the IK's own orientation); `standoff` m (0.10; real arm at least 0.20); `max_up` m above the arm's shoulder (0.35; real arm at most 0.25) |
| `data` on SUCCESS | `{"joints": [7 rad], "joints_deg": [7], "pose": {"position": [3], "orientation": [w,x,y,z]}, "goal": [3], "mm_from_goal": 132.0}`. Hand `joints` to `move_joints`, or `pose` to `move_pose` |
| Fails | `INVALID_INPUT` (no palm), `NOT_FOUND` ("no jaws-level pose toward that palm") |

A palm the arm cannot reach comfortably is reached toward: `mm_from_goal` says how far short.

### Control

#### `stop`: cancel what is moving
| | |
|---|---|
| Type | `RobotCommand` (answered even while a step runs) |
| Effect | cancels the running arm or gripper goal (the arm holds where it is) and stops the head. The step that was running returns `FAILURE` "cancelled (stop) ..." |
| `req` / `data` | `{}` / `{}` |

#### `rearm`: clear a latched stop (real arm)
| | |
|---|---|
| Type | `RobotCommand` (answered even while a step runs) |
| Effect | after the permit was lost, the stop that refuses every motion is cleared. Nothing moves |
| `req` / `data` | `{}` / `{}` |

#### `status`: the node's state
| | |
|---|---|
| Type | **`RobotQuery`** |
| `data` | `{"mode": "real" or "simulation", "permit": {"ok", "why"} (real) or null, "stopped": reason or null, "busy": the running step or null, "arm_joints_deg": [7], "arm_joints_age_s", "head": {"mode", "acquired", "why", "pan", "tilt"} or null, "tracker_age_s"}` |

## The program, as service calls

What `treat_sequence.py` does (`treat_follow.sh sequence`). Each call's `data` goes into the next one's `req`:

```text
ready          {}
find_hand      {"find_s": 20, "wait_s": 10}            -> data.palm
plan_treat     {"palm": <palm>, "jaws_level": true}    -> data.joints
move_joints    {"joints": <joints>}
hold           {"seconds": 5}
ready          {}
```

With `--gripper`, `gripper_close {}` runs first and `gripper_open {}` runs after `move_joints`. Exit codes: 0 done,
1 a step failed, 2 no hand held out, 3 nobody found, 4 stopped (deadman), 130 Ctrl-C (`stop` is called).

## Calling one service

From a terminal on the Thor, in `software/perception/tools`:

```sh
./treat_follow.sh call status
./treat_follow.sh call find_hand '{"wait_s": 15}'
./treat_follow.sh call plan_treat '{"palm": [0.80, -0.10, 0.90]}'
./treat_follow.sh call move_joints '{"joints_deg": [60.8, 17.4, -20.4, 83.9, 20.4, -17.4, -48.5]}'
```

`call` sends the request **once** and waits for it. Don't use `perception_call.sh` for these: it sends an unanswered
request again after 10 s, and a move that takes longer would get a second one.

With the plain ROS 2 CLI (source `/opt/ros/jazzy/setup.bash` and `~/robot_api_ws/install/setup.bash`, robot domain 0):

```sh
ros2 service call /perception/treat/get_hand robot_api_interfaces/srv/RobotCommand '{req: "{\"timeout_s\": 10}"}'
ros2 service call /perception/treat/status robot_api_interfaces/srv/RobotQuery
```
