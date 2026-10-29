"""The robot API ARCHITECT's generated programs call on OpenDubs.

The API spec below is injected into the LLM system prompt. The constants and
``check_*`` helpers are shared by the dry-run stubs (``config.py``) and the
live client (``client.py``), so a program that names an arm or a camera
wrongly fails the same way in dry-run as on the robot. ``PRIMITIVES`` is the
one list of function names; the dry-run namespace, the live namespace and the
spec are all checked against it (``tests/test_api.py``).

Numbers the robot owns (workspace bounds, named poses, gripper effort, speed
caps) are NOT written here. The program reads them from the robot
(``get_workspace``, ``get_named_poses``) and the robot enforces them, so a
stale copy in a prompt can never widen what the arms are allowed to do.
"""

from __future__ import annotations

import re

#: Every function a program may call, as (name, ROS 2 service, kind). A service lives under
#: /robot/ unless it starts with "/": detect_objects and locate_object are the perception
#: services (software/perception on archit/perception-grasp, API.md), which answer in the
#: camera's frame; the client turns that into the chassis frame (camera_frames.py). "query" is
#: a RobotQuery (no request fields), "command" a RobotCommand (a JSON request). Motion services
#: are the ones the robot gates on CH5, the deadman and the arm lease.
PRIMITIVES = (
    ("detect_objects",            "/perception/grasp_object",          "command"),
    ("locate_object",             "/perception/locate",                "command"),
    ("detect_markers",            "perception/detect_markers",         "command"),
    ("get_vqa_response",          "perception/get_vqa_response",       "command"),
    ("verify_grasp",              "perception/verify_grasp",           "command"),
    ("get_hand_pose",             "perception/get_hand_pose",          "query"),
    ("get_current_joints",        "proprioception/get_current_joints", "query"),
    ("get_current_ee_pose",       "proprioception/get_current_ee_pose", "query"),
    ("get_current_gripper_width", "proprioception/get_current_gripper_width", "query"),
    ("get_workspace",             "proprioception/get_workspace",      "query"),
    ("get_named_poses",           "proprioception/get_named_poses",    "query"),
    ("move_ee_to_pose",           "control/move_ee_to_pose",           "command"),
    ("move_ee_to_rel_pose",       "control/move_ee_to_rel_pose",       "command"),
    ("move_arm_to_named_pose",    "control/move_arm_to_named_pose",    "command"),
    ("set_gripper_width",         "control/set_gripper_width",         "command"),
    ("reset_robot",               "control/reset_robot",               "command"),
    ("place_in_hand",             "control/place_in_hand",             "command"),
    ("wait",                      "control/wait",                      "command"),
    ("set_head_pose",             "head/set_head_pose",                "command"),
    ("say",                       "head/say",                          "command"),
    ("set_expression",            "head/set_expression",               "command"),
    ("face_gesture",              "head/face_gesture",                 "command"),
    ("get_base_pose",             "navigation/get_base_pose",          "query"),
    ("list_places",               "navigation/list_places",            "query"),
    ("navigate_to",               "navigation/navigate_to",            "command"),
    ("move_base_rel",             "navigation/move_base_rel",          "command"),
)
PRIMITIVE_NAMES = tuple(name for name, _, _ in PRIMITIVES)

#: Calls that move an arm, a gripper or the base. The trace records these as
#: control steps, and they are the ones the robot refuses without the deadman.
MOTION_PRIMITIVES = frozenset({
    "move_ee_to_pose", "move_ee_to_rel_pose", "move_arm_to_named_pose", "set_gripper_width",
    "reset_robot", "place_in_hand", "navigate_to", "move_base_rel",
})

#: ResultCode values: robot_api_interfaces/msg/ResultCode.msg, kept identical to the perception
#: services' copy (0-4 are architect-franka's; NOT_FOUND is perception's: nothing with that label
#: in view). REFUSED is OpenDubs' arm services' own, a value outside the message's constants so the
#: shared message is unchanged: a safety gate said no, which is not a failure to diagnose.
SUCCESS, FAILURE, INVALID_INPUT, TIMEOUT, SERVICE_NOT_RUNNING, NOT_FOUND = range(6)
REFUSED = 6

ARMS = ("left", "right")

#: Cameras a perception call may name. The head camera is an Orbbec on the
#: pan/tilt neck; wrist cameras are optional ZED Minis and may be absent.
CAMERAS = ("head", "wrist_left", "wrist_right")

#: Names other robots' prompts and scorers use. ``task_scorers`` calls
#: ``get_vqa_response(question, "scene")`` positionally, so "scene" must work.
CAMERA_ALIASES = {"scene": "head"}

#: Copied from opendubs software/head-protocol/head_protocol.py (EMOTES, MOVES).
#: The robot validates again; this copy only catches typos in dry-run.
EXPRESSIONS = ("NEUTRAL", "ALERT", "SQUINT", "SOFT", "SLEEPY")
GESTURES = ("wink_l", "wink_r", "blink2", "look_l", "look_r", "roll", "bob", "sparkle",
            "side_eye_l", "side_eye_r", "to_camera", "double_take", "startle")

#: Longest sentence ``say`` accepts, in characters. Keeps a looping program
#: from reading out a paragraph to a child at the door.
SAY_MAX_CHARS = 200


def check_arm(arm: str) -> str:
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    return arm


def check_camera(camera: str) -> str:
    camera = CAMERA_ALIASES.get(camera, camera)
    if camera not in CAMERAS:
        raise ValueError(f"camera must be one of {CAMERAS}, got {camera!r}")
    return camera


def check_opening(opening: float) -> float:
    if not isinstance(opening, (int, float)) or not 0.0 <= opening <= 1.0:
        raise ValueError(f"gripper opening must be a number in [0.0, 1.0], got {opening!r}")
    return float(opening)


def check_pose(pose: dict, name: str = "target_pose") -> dict:
    try:
        p, q = pose["position"], pose["orientation"]
        [float(p[k]) for k in "xyz"]
        [float(q[k]) for k in "xyzw"]
        if pose.get("approach") is not None:
            a = [float(pose["approach"][k]) for k in "xyz"]
            if sum(v * v for v in a) < 1e-6:
                raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            f"{name} must be {{'position': {{x, y, z}}, 'orientation': {{x, y, z, w}}}} and optionally "
            f"'approach': {{x, y, z}} (not zero), got {pose!r}"
        ) from None
    return pose


def approach_of(pose: dict):
    """A checked pose's "approach" as [x, y, z], or None when it names none."""
    a = pose.get("approach")
    return None if a is None else [float(a[k]) for k in "xyz"]


WAIT_MAX_S = 10.0


def check_wait(seconds: float) -> float:
    if not isinstance(seconds, (int, float)) or not 0.0 <= seconds <= WAIT_MAX_S:
        raise ValueError(f"wait takes 0 to {WAIT_MAX_S:g} seconds, got {seconds!r}")
    return float(seconds)


def check_delta(delta: dict) -> dict:
    try:
        [float(delta.get(k, 0.0)) for k in "xyz"]
    except (AttributeError, TypeError, ValueError):
        raise ValueError(f"delta must be {{'x': dx, 'y': dy, 'z': dz}} in meters, got {delta!r}") from None
    return delta


def check_say(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("say() needs a non-empty sentence")
    if len(text) > SAY_MAX_CHARS:
        raise ValueError(f"say() takes at most {SAY_MAX_CHARS} characters, got {len(text)}")
    return text


API_SPEC = """\
OpenDubs: a mobile robot with two 7-joint OpenArm arms (left and right), a parallel gripper on each,
a pan/tilt head with an Orbbec RGB-D camera, an animated face on a tablet, and a speaker.

Frames and units: every pose is in the robot's chassis frame (x forward, y left, z up), in
meters; orientations are quaternions {x, y, z, w}. A gripper's pose is that of its tool point,
between the fingers. Perception answers in this frame too. Angles you pass are in degrees unless
stated.

Failure behavior: a primitive that fails raises RuntimeError and the program stops. A motion
the robot refuses raises MotionRefused, a RuntimeError subclass: the deadman released, the wrong
authority mode, a target outside the workspace or out of the arm's reach, a move that would bring
the arm too close to the robot's own body or the other arm. Every move is checked before it starts
and watched while it runs. Never catch MotionRefused: the person supervising decides what happens
next, not the program.

## Perception

detect_objects(text_prompt: str, camera: str = "head", x_offset: float = 0.0, y_offset: float = 0.0, z_offset: float = 0.0)
    Open-vocabulary detection of one object, returning a grasp pose for it (the best match for
    `text_prompt`, a short name such as "candy bowl"). camera: "head" (default), "wrist_left" or
    "wrist_right" (wrist cameras may not be fitted). Offsets shift the returned grasp position,
    in meters. Raises RuntimeError when nothing with that name is in view, or the object is more
    than 1.5 m from the camera.
    Returns:
    {"best_grasp": {"translation_wrt_base": [x, y, z],
                    "quaternion_wrt_base": {"x": .., "y": .., "z": .., "w": ..},
                    "score": float}}

locate_object(label: str, camera: str = "head")
    Where the best match for `label` is: the middle of its visible surface.
    Returns {"position": {"x": .., "y": .., "z": ..}, "score": float, "instances_found": int}.
    Raises RuntimeError when nothing with that name is in view.

detect_markers(camera: str = "head")
    Detect ArUco markers. Returns:
    {"markers": [{"marker_id": int,
                  "pose_wrt_base_link": {"position": {x, y, z}, "orientation": {x, y, z, w}},
                  "grasp_pose": {"position": {x, y, z}, "orientation": {x, y, z, w}}}]}

get_vqa_response(prompt: str, camera: str = "head")
    Ask a short question about what a camera sees. Returns {"data": {"answer": "<string>"}}.
    Ask yes/no questions phrased so that "no" means failure.

verify_grasp(object_name: str, arm: str)
    Check whether `arm`'s gripper is holding `object_name` (gripper state plus a camera check).
    Returns {"data": {"grasped": bool, "reason": str}}.

get_hand_pose()
    The nearest person's open hand held out toward the robot, from the hand tracker.
    Returns {"hand": None} when no hand is held out, else
    {"hand": {"position": {x, y, z}, "orientation": {x, y, z, w}, "stable": bool}}.
    `stable` is True once the hand has stayed still long enough to place something in it.

## Proprioception

get_current_joints()
    Returns {"left": {"joint1": {"position": rad, "velocity": rad/s, "effort": Nm}, ... "joint7": ...},
             "right": {...}}

get_current_ee_pose()
    Returns {"left": {"position": {x, y, z}, "orientation": {x, y, z, w}}, "right": {...}}

get_current_gripper_width()
    Returns {"left": opening, "right": opening}, each from 0.0 (closed) to 1.0 (fully open).

get_workspace()
    The box each arm's gripper may move in, as the robot enforces it. Returns
    {"left": {"x": [min, max], "y": [min, max], "z": [min, max]}, "right": {...}} in the chassis frame meters.
    Read this before computing a target; a target outside it raises MotionRefused.

get_named_poses()
    Returns {"poses": ["stow", ...]}: joint-space poses the robot knows by name.

## Arm and gripper control

move_ee_to_pose(arm: str, target_pose: dict)
    Move `arm`'s gripper tool point to target_pose["position"] in the chassis frame, checked by the robot.
    target_pose: {"position": {x, y, z}, "orientation": {x, y, z, w}, "approach": {x, y, z}?}
    The orientation is not yet controlled: the arm reaches the position in the posture nearest
    its current one. "approach", if given, is the direction the fingers point at the goal, held
    within 2 degrees (e.g. {"x": 0, "y": 0, "z": -1}: straight down, into a container from above);
    the gripper's roll about it is free. Returns True.

move_ee_to_rel_pose(arm: str, delta: dict)
    Move `arm`'s gripper tool point by {"x": dx, "y": dy, "z": dz} meters in the chassis frame
    (x forward, y left, z up), in the posture nearest its current one. Returns True.

move_arm_to_named_pose(arm: str, name: str)
    Move `arm` in joint space to a pose from get_named_poses(). Returns True.

set_gripper_width(arm: str, opening: float)
    Open or close `arm`'s gripper: 0.0 closed, 1.0 fully open. The robot caps grip effort.
    Returns True.

reset_robot()
    Move both arms to "stow". Grippers keep their state, so a held object is not dropped.
    Returns True.

place_in_hand(arm: str, timeout_s: float = 15.0)
    Hand the object held by `arm` to the person whose hand get_hand_pose() reports: the robot
    tracks the hand, moves the gripper into it, opens, and retracts. Waits up to `timeout_s`
    for a stable hand. Returns {"released": bool, "reason": str}; released is False when no
    stable hand appeared in time (the gripper is still closed and the arm has retracted).

wait(seconds: float)
    Hold still for `seconds` (0 to 10) before the next call, e.g. a pause before letting go.
    Returns True.

## Head and expression (no arm or base motion)

set_head_pose(pan_deg: float, tilt_deg: float)
    Point the head (and its camera). 0, 0 is straight ahead and level; positive pan turns left,
    positive tilt looks up. The robot clamps to its limits. Returns True.

say(text: str)
    Speak one short sentence (at most 200 characters) from the robot's speaker. Returns True
    once it has been queued; it does not wait for the speech to finish.

set_expression(name: str)
    Set the face's expression: NEUTRAL, ALERT, SQUINT, SOFT or SLEEPY. Returns True.

face_gesture(name: str)
    Play a one-shot face gesture: wink_l, wink_r, blink2, look_l, look_r, roll, bob, sparkle,
    side_eye_l, side_eye_r, to_camera, double_take or startle. Returns True.

## Navigation (the robot refuses these unless navigation is enabled for the session)

get_base_pose()
    Returns {"frame": "map", "x": m, "y": m, "yaw_deg": deg}.

list_places()
    Returns {"places": [{"name": "204", "x": m, "y": m}, ...]}: named places on the current map.

navigate_to(place: str | None = None, pose: dict | None = None, timeout_s: float = 120.0)
    Drive to a named place or to {"x": m, "y": m, "yaw_deg": deg} in the map frame. Give exactly one.
    Both arms must be at "stow" first (call reset_robot()). Returns True on arrival.

move_base_rel(dx: float = 0.0, dy: float = 0.0, dyaw_deg: float = 0.0)
    Short relative base move in the chassis frame: dx forward, dy left (the base can strafe), meters;
    dyaw_deg counter-clockwise. Both arms must be at "stow". Returns True.
"""


#: Which primitives a session offers. "arms" is the first one for the physical robot: no
#: perception, no handoff, no speech or navigation, only what a recorded show can play.
#: "arms_see" adds the perception services' detect and locate: a show recorded with it goes where
#: the camera saw things when it was approved.
PROFILES = {
    "full": PRIMITIVE_NAMES,
    "arms_see": ("detect_objects", "locate_object", "get_current_joints", "get_current_ee_pose",
                 "get_current_gripper_width", "get_workspace", "get_named_poses", "move_ee_to_pose",
                 "move_ee_to_rel_pose", "move_arm_to_named_pose", "set_gripper_width", "reset_robot", "wait"),
    "arms": ("get_current_joints", "get_current_ee_pose", "get_current_gripper_width", "get_workspace",
             "get_named_poses", "move_ee_to_pose", "move_ee_to_rel_pose", "move_arm_to_named_pose",
             "set_gripper_width", "reset_robot", "wait"),
}

_ENTRY = re.compile(r"^([a-z_]+)\(")


def spec_for(names) -> str:
    """API_SPEC with only the entries for `names`, and only the sections that keep one."""
    names = set(names)
    head, sections = [], []
    for line in API_SPEC.splitlines():
        if line.startswith("## "):
            sections.append({"header": [line], "entries": []})
        elif not sections:
            head.append(line)
        elif _ENTRY.match(line):
            sections[-1]["entries"].append((_ENTRY.match(line).group(1), [line]))
        elif sections[-1]["entries"]:
            sections[-1]["entries"][-1][1].append(line)
        else:
            sections[-1]["header"].append(line)
    out = list(head)
    for s in sections:
        kept = [lines for name, lines in s["entries"] if name in names]
        if kept:
            out += s["header"] + [line for lines in kept for line in lines]
    return "\n".join(out).rstrip() + "\n"
