"""The perception services (software/perception on archit/perception-grasp, API.md) as ARCHITECT's
detect_objects and locate_object: the requests they take, and their replies turned into the
chassis frame. Transport-free: client.py sends the requests.

  detect_objects -> /perception/grasp_object {"label", "camera", "up_cam"}: AnyGrasp's best grasp on
                    the best match. up_cam (the world's up, in the camera frame) makes it prefer
                    grasps whose jaws close level.
  locate_object  -> /perception/locate {"label", "camera"}: the middle of the best match's visible
                    surface.

Callers on the Thor set ROS_DOMAIN_ID=0 and use the robot's DDS profile (dev.sh's DDS). Every call
only reads, so an unanswered one is sent again (client.py, as perception_call.py does).
"""

from __future__ import annotations

from opendubs_architect import api, camera_frames

#: ARCHITECT's camera names -> the perception services'. No wrist camera is fitted yet.
CAMERAS = {"head": "femto"}


def camera_name(camera: str) -> str:
    camera = api.check_camera(camera)
    if camera not in CAMERAS:
        raise RuntimeError(f"no {camera} camera is fitted; use camera=\"head\"")
    return CAMERAS[camera]


class Perception:
    """Requests and replies for one head pose. The lift's height, which the robot does not publish
    yet (camera_frames.py), comes from the arm config's `lift_mm`; the head's pan and tilt are the
    caller's (`turned`, from /head/joint_states), else the config's first `head` pose."""

    def __init__(self, head_pan_deg: float = 0.0, head_tilt_deg: float = 0.0, lift_m: float = 0.0,
                 frames: camera_frames.CameraFrames | None = None):
        self.frames = frames or camera_frames.CameraFrames()
        self.lift_m = lift_m
        self.T = self.frames.camera_to_chassis(head_pan_deg, head_tilt_deg, lift_m)

    def turned(self, head_pan_deg: float, head_tilt_deg: float) -> "Perception":
        """The same robot with the head at these angles (degrees, the URDF's signs: + pan left, + tilt down)."""
        return Perception(head_pan_deg, head_tilt_deg, self.lift_m, self.frames)

    @classmethod
    def from_arm_config(cls) -> "Perception":
        from opendubs_architect.live_arms import ArmConfig
        cfg = ArmConfig.load()
        pan, tilt = cfg.head[0] if cfg.head else (0.0, 0.0)
        return cls(pan, tilt, cfg.lift_mm / 1000.0)

    def grasp_request(self, label: str, camera: str) -> dict:
        return {"label": str(label), "camera": camera_name(camera), "up_cam": camera_frames.up_in_camera(self.T)}

    def grasp_reply(self, data: dict, offsets=(0.0, 0.0, 0.0)) -> dict:
        pos, quat = camera_frames.to_chassis_grasp(self.T, data["position"], data["orientation"])
        pos = [round(p + float(o), 4) for p, o in zip(pos, offsets)]
        return {"best_grasp": {"translation_wrt_base": pos, "quaternion_wrt_base": quat,
                               "score": data.get("grasp_score"), "width": data.get("width"),
                               "label": data.get("label"), "detection_score": data.get("score")}}

    def locate_request(self, label: str, camera: str) -> dict:
        return {"label": str(label), "camera": camera_name(camera)}

    def locate_reply(self, data: dict) -> dict:
        x, y, z = camera_frames.to_chassis_point(self.T, data["position"])
        return {"position": {"x": x, "y": y, "z": z}, "score": data.get("score"),
                "instances_found": data.get("instances_found")}
