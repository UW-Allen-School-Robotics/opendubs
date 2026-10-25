"""A stand-in robot for ``--dry-run``: prints each call and returns a plausible reply.

It has the live client's signatures and argument checks, so a program with a wrong arm name,
a missing argument or a pose in the wrong shape fails here, at the desk, as it would on the robot.
"""

from __future__ import annotations

from opendubs_architect import api

_POSE = {"position": {"x": 0.45, "y": -0.25, "z": 0.75}, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}


class DryRunRobot:
    """Prints each call and returns a plausible reply. Same signatures as OpenDubsRobotClient."""

    def __init__(self, console) -> None:
        self._console = console
        self._opening = {"left": 1.0, "right": 1.0}

    def _log(self, name, *args, **kwargs):
        parts = [repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()]
        self._console.print(f"  [dim]\\[dry-run][/dim] [cyan]{name}[/cyan]({', '.join(parts)})")

    # Perception
    def detect_objects(self, text_prompt, camera="head", x_offset=0.0, y_offset=0.0, z_offset=0.0):
        self._log("detect_objects", text_prompt, camera=api.check_camera(camera),
                  x_offset=x_offset, y_offset=y_offset, z_offset=z_offset)
        p = _POSE["position"]
        return {"best_grasp": {"translation_wrt_base": [p["x"] + x_offset, p["y"] + y_offset, p["z"] + z_offset],
                               "quaternion_wrt_base": dict(_POSE["orientation"]), "score": 0.9}}

    def locate_object(self, label, camera="head"):
        self._log("locate_object", label, camera=api.check_camera(camera))
        return {"position": dict(_POSE["position"]), "score": 0.9, "instances_found": 1}

    def detect_markers(self, camera="head"):
        self._log("detect_markers", camera=api.check_camera(camera))
        return {"markers": []}

    def get_vqa_response(self, prompt, camera="head"):
        self._log("get_vqa_response", prompt, camera=api.check_camera(camera))
        return {"data": {"answer": "yes"}}

    def verify_grasp(self, object_name, arm):
        self._log("verify_grasp", object_name, arm=api.check_arm(arm))
        return {"data": {"grasped": True, "reason": "dry-run"}}

    def get_hand_pose(self):
        self._log("get_hand_pose")
        return {"hand": {"position": {"x": 0.55, "y": 0.0, "z": 0.9},
                         "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}, "stable": True}}

    # Proprioception
    def get_current_joints(self):
        self._log("get_current_joints")
        return {arm: {f"joint{i}": {"position": 0.0, "velocity": 0.0, "effort": 0.0} for i in range(1, 8)}
                for arm in api.ARMS}

    def get_current_ee_pose(self):
        self._log("get_current_ee_pose")
        return {arm: {"position": dict(_POSE["position"], y=0.25 if arm == "left" else -0.25),
                      "orientation": dict(_POSE["orientation"])} for arm in api.ARMS}

    def get_current_gripper_width(self):
        self._log("get_current_gripper_width")
        return dict(self._opening)

    def get_workspace(self):
        # Dry-run numbers only, chosen so a program has something to clamp against.
        # The robot's own box comes from the robot; nothing here is a measured limit.
        self._log("get_workspace")
        return {"left": {"x": [0.25, 0.6], "y": [-0.1, 0.45], "z": [0.6, 1.1]},
                "right": {"x": [0.25, 0.6], "y": [-0.45, 0.1], "z": [0.6, 1.1]}}

    def get_named_poses(self):
        self._log("get_named_poses")
        return {"poses": ["stow"]}

    # Arm and gripper control
    def move_ee_to_pose(self, arm, target_pose):
        self._log("move_ee_to_pose", api.check_arm(arm), api.check_pose(target_pose))
        return True

    def move_ee_to_rel_pose(self, arm, delta):
        self._log("move_ee_to_rel_pose", api.check_arm(arm), api.check_delta(delta))
        return True

    def move_arm_to_named_pose(self, arm, name):
        self._log("move_arm_to_named_pose", api.check_arm(arm), name)
        return True

    def set_gripper_width(self, arm, opening):
        self._opening[api.check_arm(arm)] = api.check_opening(opening)
        self._log("set_gripper_width", arm, opening)
        return True

    def reset_robot(self):
        self._log("reset_robot")
        return True

    def place_in_hand(self, arm, timeout_s=15.0):
        self._log("place_in_hand", api.check_arm(arm), timeout_s=timeout_s)
        self._opening[arm] = 1.0
        return {"released": True, "reason": "dry-run"}

    # Head and expression
    def set_head_pose(self, pan_deg, tilt_deg):
        self._log("set_head_pose", float(pan_deg), float(tilt_deg))
        return True

    def say(self, text):
        self._log("say", api.check_say(text))
        return True

    def set_expression(self, name):
        if name not in api.EXPRESSIONS:
            raise ValueError(f"expression must be one of {api.EXPRESSIONS}, got {name!r}")
        self._log("set_expression", name)
        return True

    def face_gesture(self, name):
        if name not in api.GESTURES:
            raise ValueError(f"gesture must be one of {api.GESTURES}, got {name!r}")
        self._log("face_gesture", name)
        return True

    # Navigation
    def get_base_pose(self):
        self._log("get_base_pose")
        return {"frame": "map", "x": 0.0, "y": 0.0, "yaw_deg": 0.0}

    def list_places(self):
        self._log("list_places")
        return {"places": []}

    def navigate_to(self, place=None, pose=None, timeout_s=120.0):
        if (place is None) == (pose is None):
            raise ValueError("navigate_to() takes exactly one of place= or pose=")
        self._log("navigate_to", place=place, pose=pose, timeout_s=timeout_s)
        return True

    def move_base_rel(self, dx=0.0, dy=0.0, dyaw_deg=0.0):
        self._log("move_base_rel", dx=dx, dy=dy, dyaw_deg=dyaw_deg)
        return True


def namespace_for(robot) -> dict:
    """The execution namespace a program sees: exactly the primitives in api.PRIMITIVES."""
    return {name: getattr(robot, name) for name in api.PRIMITIVE_NAMES}
