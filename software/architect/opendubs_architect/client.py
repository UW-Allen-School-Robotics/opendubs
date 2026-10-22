"""Live ROS 2 client for the OpenDubs robot API (see api.py).

Every public method is one entry of ``api.PRIMITIVES`` and one service call under /robot/,
using the JSON-over-ROS service types of architect-franka's ``robot_api_interfaces``, rebuilt
for ROS 2:

    RobotQuery   - no request fields; response {result_code, data: JSON string}
    RobotCommand - request {req: JSON string}; response {result_code, data: JSON string}

The client checks argument shapes and nothing else. Whether a motion may happen (CH5, the
deadman, the arm lease, the workspace box) is the robot's decision, made in the service; a
refusal comes back as REFUSED and is raised here as ``MotionRefused``, which a generated
program is told never to catch.

Every call has a timeout. A service that stops answering ends the program with an error
instead of hanging it; the arms are safe either way, because a motion source that goes
quiet loses its lease and the arm holds where it is.
"""

from __future__ import annotations

import json

from opendubs_architect import api

#: Seconds to wait for a service to appear, and for a reply. Motions get their own
#: budget: the robot finishes or refuses a move within it, or the call is abandoned.
SERVICE_WAIT_S = 5.0
QUERY_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 30.0
MOTION_TIMEOUT_S = 60.0
NAVIGATION_TIMEOUT_S = 180.0


class MotionRefused(RuntimeError):
    """A safety gate on the robot refused the call: deadman released, CH5 not in AUTO,
    the arm lease lost, a target outside the workspace. Not a task failure to correct."""


class OpenDubsRobotClient:
    """Exposes the robot API as blocking Python calls over ROS 2 services."""

    def __init__(self, node_name: str = "architect_client") -> None:
        try:
            import rclpy
            from robot_api_interfaces.srv import RobotCommand, RobotQuery
        except ImportError as exc:
            raise RuntimeError(
                "rclpy or robot_api_interfaces is not importable: source /opt/ros/jazzy/setup.bash "
                f"and the workspace that builds robot_api_interfaces ({exc})"
            ) from None
        self._rclpy = rclpy
        self._types = {"query": RobotQuery, "command": RobotCommand}
        if not rclpy.ok():
            rclpy.init()
        self._node = rclpy.create_node(node_name)
        self._clients: dict[str, object] = {}
        self._services = {name: (service, kind) for name, service, kind in api.PRIMITIVES}

    def destroy_node(self) -> None:
        self._node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()

    # ------------------------------------------------------------------
    # The one call path
    # ------------------------------------------------------------------

    def _call(self, name: str, payload: dict | None = None, wait_s: float = 0.0) -> dict:
        """Call one primitive's service. ``wait_s`` is how long the robot may legitimately take
        on top of its usual reply (a navigation's or a handoff's own timeout)."""
        service, kind = self._services[name]
        service = "/robot/" + service
        srv = self._types[kind]
        client = self._clients.get(service)
        if client is None:
            client = self._clients[service] = self._node.create_client(srv, service)
        if not client.wait_for_service(timeout_sec=SERVICE_WAIT_S):
            raise RuntimeError(f"{service} is not running (waited {SERVICE_WAIT_S:.0f} s)")
        req = srv.Request() if kind == "query" else srv.Request(req=json.dumps(payload or {}))
        if name == "navigate_to":
            timeout = NAVIGATION_TIMEOUT_S
        elif name in api.MOTION_PRIMITIVES:
            timeout = MOTION_TIMEOUT_S
        elif kind == "query":
            timeout = QUERY_TIMEOUT_S
        else:
            timeout = COMMAND_TIMEOUT_S
        timeout = max(timeout, wait_s + COMMAND_TIMEOUT_S)
        future = client.call_async(req)
        self._rclpy.spin_until_future_complete(self._node, future, timeout_sec=timeout)
        if not future.done():
            future.cancel()
            raise RuntimeError(f"{service} did not reply within {timeout:.0f} s")
        resp = future.result()
        code, message = resp.result_code.result_code, resp.result_code.message
        if code == api.REFUSED:
            raise MotionRefused(f"{name}: {message}")
        if code != api.SUCCESS:
            raise RuntimeError(f"{name}: {message or f'result code {code}'}")
        return json.loads(resp.data) if resp.data else {}

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def detect_objects(self, text_prompt: str, camera: str = "head",
                       x_offset: float = 0.0, y_offset: float = 0.0, z_offset: float = 0.0) -> dict:
        return self._call("detect_objects", {
            "text_prompt": text_prompt, "camera": api.check_camera(camera),
            "x_offset": x_offset, "y_offset": y_offset, "z_offset": z_offset,
        })

    def detect_markers(self, camera: str = "head") -> dict:
        return self._call("detect_markers", {"camera": api.check_camera(camera)})

    def get_vqa_response(self, prompt: str, camera: str = "head") -> dict:
        return self._call("get_vqa_response", {"prompt": prompt, "camera": api.check_camera(camera)})

    def verify_grasp(self, object_name: str, arm: str) -> dict:
        return self._call("verify_grasp", {"object_name": object_name, "arm": api.check_arm(arm)})

    def get_hand_pose(self) -> dict:
        return self._call("get_hand_pose")

    # ------------------------------------------------------------------
    # Proprioception
    # ------------------------------------------------------------------

    def get_current_joints(self) -> dict:
        return self._call("get_current_joints")

    def get_current_ee_pose(self) -> dict:
        return self._call("get_current_ee_pose")

    def get_current_gripper_width(self) -> dict:
        return self._call("get_current_gripper_width")

    def get_workspace(self) -> dict:
        return self._call("get_workspace")

    def get_named_poses(self) -> dict:
        return self._call("get_named_poses")

    # ------------------------------------------------------------------
    # Arm and gripper control
    # ------------------------------------------------------------------

    def move_ee_to_pose(self, arm: str, target_pose: dict) -> bool:
        self._call("move_ee_to_pose", {"arm": api.check_arm(arm), "target_pose": api.check_pose(target_pose)})
        return True

    def move_ee_to_rel_pose(self, arm: str, delta: dict) -> bool:
        self._call("move_ee_to_rel_pose", {"arm": api.check_arm(arm), "delta": api.check_delta(delta)})
        return True

    def move_arm_to_named_pose(self, arm: str, name: str) -> bool:
        self._call("move_arm_to_named_pose", {"arm": api.check_arm(arm), "name": name})
        return True

    def set_gripper_width(self, arm: str, opening: float) -> bool:
        self._call("set_gripper_width", {"arm": api.check_arm(arm), "opening": api.check_opening(opening)})
        return True

    def reset_robot(self) -> bool:
        self._call("reset_robot")
        return True

    def place_in_hand(self, arm: str, timeout_s: float = 15.0) -> dict:
        return self._call("place_in_hand", {"arm": api.check_arm(arm), "timeout_s": float(timeout_s)},
                          wait_s=float(timeout_s))

    # ------------------------------------------------------------------
    # Head and expression
    # ------------------------------------------------------------------

    def set_head_pose(self, pan_deg: float, tilt_deg: float) -> bool:
        self._call("set_head_pose", {"pan_deg": float(pan_deg), "tilt_deg": float(tilt_deg)})
        return True

    def say(self, text: str) -> bool:
        self._call("say", {"text": api.check_say(text)})
        return True

    def set_expression(self, name: str) -> bool:
        self._call("set_expression", {"name": name})
        return True

    def face_gesture(self, name: str) -> bool:
        self._call("face_gesture", {"name": name})
        return True

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def get_base_pose(self) -> dict:
        return self._call("get_base_pose")

    def list_places(self) -> dict:
        return self._call("list_places")

    def navigate_to(self, place: str | None = None, pose: dict | None = None,
                    timeout_s: float = 120.0) -> bool:
        if (place is None) == (pose is None):
            raise ValueError("navigate_to() takes exactly one of place= or pose=")
        self._call("navigate_to", {"place": place, "pose": pose, "timeout_s": float(timeout_s)},
                   wait_s=float(timeout_s))
        return True

    def move_base_rel(self, dx: float = 0.0, dy: float = 0.0, dyaw_deg: float = 0.0) -> bool:
        self._call("move_base_rel", {"dx": float(dx), "dy": float(dy), "dyaw_deg": float(dyaw_deg)})
        return True
