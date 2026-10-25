"""From the head camera's frame to the robot's chassis frame, for the perception services.

The perception services (software/perception on archit/perception-grasp, API.md) answer in the
colour camera's optical frame, `camera_color_optical_frame`: x right, y down, z forward. ARCHITECT's
programs, the twin's IK and the shows use the CHASSIS frame: x forward, y left, z up, the twin's
`chassis` body. In the robot description (description/opendubs/opendubs.urdf) that is the `datum`
link, 0.324 m behind `base_link` (the centre of the wheel rectangle) and 0.024 m above it.

The chain is read from the URDF: datum -> zaxis (the lift) -> head_pan -> head_tilt ->
femto_mount_link -> camera_link. The camera's own camera_link -> colour optical frame is the
driver's (it publishes it on /tf_static from the camera's calibration); without it, NOMINAL_OPTICAL
places the colour camera where the camera's front marks put it (description/opendubs/dimensions.yaml:
32 mm left of the depth camera, where camera_link sits) and turns it to the optical convention.

The head's pan and tilt and the lift's height are not published yet (software/ros/robot-state-publisher.sh:
"which nothing publishes yet"), so the caller says where they are: the arm config's first `head` pose
and its `lift_mm`, the same assumption the clearance check makes. A program that moves the head
before it looks would break that assumption; the arms profiles have no head primitive.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

URDF = Path(__file__).resolve().parents[3] / "description" / "opendubs" / "opendubs.urdf"
ROOT, CAMERA_LINK = "datum", "camera_link"
#: camera_link -> camera_color_optical_frame when the driver's is not known: the colour camera
#: 32 mm to the left of the depth camera (y +0.032) and 0.5 mm lower, turned to x right, y down,
#: z forward (roll -90, yaw -90 degrees: REP 103's optical frame).
NOMINAL_OPTICAL = {"xyz": (0.0, 0.032, -0.0005), "rpy": (-math.pi / 2, 0.0, -math.pi / 2)}
#: GraspNet's gripper frame (x the approach, y the closing direction) -> the tool site's (its +z out
#: through the fingers, ik.py): tool z = grasp x, tool y = grasp y, tool x = -grasp z. ASSUMED: the
#: tool site's closing axis is its y; the arm services do not yet hold an orientation anyway.
GRASP_TO_TOOL = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])


def rpy_matrix(r: float, p: float, y: float) -> np.ndarray:
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def axis_matrix(axis, angle: float) -> np.ndarray:
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * k @ k


def transform(xyz=(0, 0, 0), rot=None) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = np.eye(3) if rot is None else rot
    T[:3, 3] = xyz
    return T


def quat_matrix(q: dict) -> np.ndarray:
    x, y, z, w = (float(q[k]) for k in "xyzw")
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def matrix_quat(R: np.ndarray) -> dict:
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s)
    else:
        i = int(np.argmax(np.diag(R)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k]) * 2
        v = [0.0, 0.0, 0.0]
        v[i] = 0.25 * s
        v[j] = (R[j, i] + R[i, j]) / s
        v[k] = (R[k, i] + R[i, k]) / s
        q = (*v, (R[k, j] - R[j, k]) / s)
    return {"x": float(q[0]), "y": float(q[1]), "z": float(q[2]), "w": float(q[3])}


class CameraFrames:
    """The robot description's chain from `datum` to the head camera."""

    def __init__(self, urdf: str | Path = URDF, optical: dict | None = None):
        root = ET.parse(str(urdf)).getroot()
        self.joints = {}
        for j in root.iter("joint"):
            o = j.find("origin")
            ax = j.find("axis")
            self.joints[j.find("child").get("link")] = {
                "name": j.get("name"), "type": j.get("type"), "parent": j.find("parent").get("link"),
                "xyz": [float(v) for v in (o.get("xyz", "0 0 0") if o is not None else "0 0 0").split()],
                "rpy": [float(v) for v in (o.get("rpy", "0 0 0") if o is not None else "0 0 0").split()],
                "axis": [float(v) for v in (ax.get("xyz") if ax is not None else "1 0 0").split()]}
        self.optical = optical or NOMINAL_OPTICAL

    def link_pose(self, link: str, positions: dict) -> np.ndarray:
        """`link` in the chassis (datum) frame, with movable joints at `positions` (rad, or m)."""
        T = np.eye(4)
        while link != ROOT:
            j = self.joints[link]
            step = transform(j["xyz"], rpy_matrix(*j["rpy"]))
            q = float(positions.get(j["name"], 0.0))
            if j["type"] in ("revolute", "continuous"):
                step = step @ transform(rot=axis_matrix(j["axis"], q))
            elif j["type"] == "prismatic":
                step = step @ transform(np.asarray(j["axis"]) * q)
            T = step @ T
            link = j["parent"]
        return T

    def camera_to_chassis(self, head_pan_deg: float = 0.0, head_tilt_deg: float = 0.0,
                          lift_m: float = 0.0) -> np.ndarray:
        """The colour camera's optical frame in the chassis frame (4x4), the head turned
        `head_pan_deg` / `head_tilt_deg` from centre and the lift raised `lift_m`."""
        pos = {"head_pan": math.radians(head_pan_deg), "head_tilt": math.radians(head_tilt_deg), "zaxis": lift_m}
        return self.link_pose(CAMERA_LINK, pos) @ transform(self.optical["xyz"], rpy_matrix(*self.optical["rpy"]))


def to_chassis_point(T: np.ndarray, p) -> list:
    """A camera-frame point ([x, y, z] or {x, y, z}) in the chassis frame, as [x, y, z]."""
    v = np.array([p[k] for k in "xyz"] if isinstance(p, dict) else p, dtype=float)
    return [round(float(c), 4) for c in T[:3, :3] @ v + T[:3, 3]]


def to_chassis_grasp(T: np.ndarray, position: dict, orientation: dict) -> tuple:
    """A perception grasp (GraspNet's frame, in the camera frame) as the tool's pose in the chassis
    frame: ([x, y, z], quaternion dict)."""
    R = T[:3, :3] @ quat_matrix(orientation) @ GRASP_TO_TOOL
    return to_chassis_point(T, position), matrix_quat(R)


def up_in_camera(T: np.ndarray) -> list:
    """The world's up (chassis +z) in the camera frame: grasp_object's `up_cam`."""
    return [round(float(c), 5) for c in T[:3, :3].T @ np.array([0.0, 0.0, 1.0])]
