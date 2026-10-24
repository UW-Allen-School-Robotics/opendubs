"""Latest frames per camera, and ROS Image <-> numpy without cv_bridge.

cv_bridge is built against the apt numpy; converting with numpy directly keeps the server free of
that ABI coupling. Depth is matched to colour by the nearest stamp within a tolerance, so a query's
result carries the depth of the same moment as its colour image. Over best-effort QoS a large depth
frame is sometimes lost, so the newest colour frame with a depth match is used, if one is recent.
"""

from __future__ import annotations

import collections
import threading
from dataclasses import dataclass

import numpy as np
from sensor_msgs.msg import CameraInfo, Image


def stamp_s(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def image_to_numpy(msg: Image) -> np.ndarray:
    """rgb8 / bgr8 / rgba8 / bgra8 -> RGB uint8 HxWx3; 16UC1 / mono16 (mm) and 32FC1 (m) -> float32 metres."""
    enc = msg.encoding.lower()
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    if enc in ("rgb8", "bgr8", "rgba8", "bgra8"):
        c = 4 if enc.endswith("a8") else 3
        img = buf.reshape(msg.height, msg.step)[:, : msg.width * c].reshape(msg.height, msg.width, c)
        img = img[:, :, :3]
        return np.ascontiguousarray(img[:, :, ::-1] if enc.startswith("bgr") else img)
    if enc in ("16uc1", "mono16"):
        d = buf.view(np.uint16).reshape(msg.height, msg.step // 2)[:, : msg.width]
        return d.astype(np.float32) / 1000.0
    if enc == "32fc1":
        return buf.view(np.float32).reshape(msg.height, msg.step // 4)[:, : msg.width].copy()
    raise ValueError(f"unsupported image encoding {msg.encoding!r}")


def numpy_to_image(arr: np.ndarray, encoding: str, header) -> Image:
    msg = Image()
    msg.header = header
    msg.height, msg.width = int(arr.shape[0]), int(arr.shape[1])
    msg.encoding = encoding
    msg.is_bigendian = 0
    arr = np.ascontiguousarray(arr)
    msg.step = int(arr.strides[0])
    msg.data = arr.tobytes()
    return msg


@dataclass
class Frame:
    rgb_msg: Image
    depth_msg: Image | None
    info: CameraInfo | None

    @property
    def stamp(self) -> float:
        return stamp_s(self.rgb_msg.header)


class CameraFrames:
    """Keeps the latest colour frame, recent depth frames and the camera info of one camera."""

    def __init__(self, slop_s: float = 0.05, max_age_s: float = 0.5):
        self.slop = slop_s
        self.max_age = max_age_s  # how much older a colour frame with depth may be than the newest
        self._lock = threading.Lock()
        self._rgb: collections.deque = collections.deque(maxlen=8)
        self._depth: collections.deque = collections.deque(maxlen=15)
        self._info: CameraInfo | None = None

    def on_rgb(self, msg: Image):
        with self._lock:
            self._rgb.append(msg)

    def on_depth(self, msg: Image):
        with self._lock:
            self._depth.append(msg)

    def on_info(self, msg: CameraInfo):
        with self._lock:
            self._info = msg

    def latest(self) -> Frame | None:
        """The newest colour frame with a depth match within max_age, else the newest colour frame."""
        with self._lock:
            if not self._rgb:
                return None
            newest = stamp_s(self._rgb[-1].header)
            for rgb in reversed(self._rgb):
                t = stamp_s(rgb.header)
                if newest - t > self.max_age:
                    break
                depth = min(self._depth, key=lambda d: abs(stamp_s(d.header) - t), default=None)
                if depth is not None and abs(stamp_s(depth.header) - t) <= self.slop:
                    return Frame(rgb, depth, self._info)
            return Frame(self._rgb[-1], None, self._info)


def intrinsics(info: CameraInfo | None) -> np.ndarray | None:
    if info is None or info.k[0] == 0.0:
        return None
    return np.array(info.k, dtype=np.float64).reshape(3, 3)
