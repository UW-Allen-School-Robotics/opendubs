"""AnyGrasp over a WebSocket: one object's mask, colour and depth -> grasps in the camera frame.

AnyGrasp runs on a GPU workstation. Two servers speak the same payload:

- `anygrasp_server/server.py` in this package (route `/grasp`, default port 8768), which also
  returns candidates, the selection mode and the real error, and accepts per-request options;
- the original server it was written from (route `/get_grasp_config`, port 8767), which ignores
  the options and returns only the best grasp.

The payload is JSON with base64 arrays: `rgb` (uint8 HxWx3, RGB), `depth` (uint16 mm, aligned to
rgb), `mask` (uint8, >0 = object), `intrinsics` (3x3 float64), `depth_scale` (metres per depth
unit). Both servers build the cloud only from masked pixels, so everything is cropped to the
mask's box and the principal point shifted to match: the points are identical, and a 1280x720
frame (7.4 MB as base64) would exceed aiohttp's default 4 MB message limit on the original server.

Grasps follow GraspNet's convention, in the camera optical frame (x right, y down, z forward):
rotation column 0 is the approach direction, column 1 the closing direction, column 2 their cross
product; translation is in metres. `upright_score` and `tilt_from_floor_deg` are only meaningful
when the server knows where "up" is (`up_cam` here, or a camera-to-base quaternion).
"""

from __future__ import annotations

import base64
import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# The servers' AnyGrasp workspace, in the camera frame: points beyond it are cut away.
WORKSPACE_MAX_Z_M = 1.5
WORKSPACE_MAX_XY_M = 1.0
MIN_POINTS = 50  # the servers refuse sparser clouds


class GraspError(RuntimeError):
    """The request could not be answered. `kind` says why:
    input (the object or its data cannot be used), timeout, unreachable (no connection to the
    server), server (the server answered with an error, e.g. no grasp)."""

    def __init__(self, message: str, kind: str = "input"):
        super().__init__(message)
        self.kind = kind


@dataclass
class Grasp:
    translation: np.ndarray        # (3,) metres, camera optical frame
    rotation: np.ndarray           # (3, 3), columns: approach, closing, binormal
    width: float                   # metres
    score: float                   # AnyGrasp's raw score
    upright_score: float = float("nan")
    tilt_from_floor_deg: float = float("nan")

    def quaternion(self) -> tuple[float, float, float, float]:
        """-> (x, y, z, w) of the rotation."""
        return rotation_to_quaternion(self.rotation)


@dataclass
class GraspReply:
    best: Grasp
    candidates: list = field(default_factory=list)  # other grasps, best first (new server only)
    mode: str = ""                  # how the best was chosen (new server only)
    points: int = 0                 # object points sent
    request_bytes: int = 0
    server_ms: float = float("nan")
    round_trip_ms: float = 0.0


def crop_to_mask(rgb, depth, mask, K, margin_px: int = 16):
    """Crop rgb, depth and mask to the mask's box plus a margin; shift K's principal point to match.

    -> (rgb, depth, mask, K, (x0, y0)). Raises GraspError on an empty mask.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise GraspError("the mask is empty")
    h, w = mask.shape[:2]
    x0, x1 = max(0, int(xs.min()) - margin_px), min(w, int(xs.max()) + 1 + margin_px)
    y0, y1 = max(0, int(ys.min()) - margin_px), min(h, int(ys.max()) + 1 + margin_px)
    K = np.array(K, dtype=np.float64).copy()
    K[0, 2] -= x0
    K[1, 2] -= y0
    crop = (slice(y0, y1), slice(x0, x1))
    return (np.ascontiguousarray(rgb[crop]), np.ascontiguousarray(depth[crop]),
            np.ascontiguousarray(mask[crop]), K, (x0, y0))


def erode(mask: np.ndarray, px: int) -> np.ndarray:
    """Shrink a mask by `px` pixels (square kernel). A time-of-flight camera returns mixed depth at
    an object's edge, between the object and what is behind it; eroding drops those pixels."""
    m = mask > 0
    if px <= 0:
        return m
    for axis in (0, 1):
        n = m.shape[axis]
        out = m.copy()
        for s in range(1, px + 1):
            if s >= n:
                out[:] = False
                break
            fwd = [slice(None)] * 2
            bwd = [slice(None)] * 2
            fwd[axis], bwd[axis] = slice(s, None), slice(None, -s)
            out[tuple(bwd)] &= m[tuple(fwd)]
            out[tuple(fwd)] &= m[tuple(bwd)]
            edge = [slice(None)] * 2
            edge[axis] = slice(0, s)
            out[tuple(edge)] = False
            edge[axis] = slice(n - s, None)
            out[tuple(edge)] = False
        m = out
    return m


def object_points(depth, mask, K, depth_scale: float) -> np.ndarray:
    """Masked pixels with depth -> camera-frame points (N, 3), the way the servers build them."""
    v, u = np.nonzero((mask > 0) & (depth > 0))
    z = depth[v, u].astype(np.float64) * depth_scale
    x = (u - K[0, 2]) / K[0, 0] * z
    y = (v - K[1, 2]) / K[1, 1] * z
    return np.stack([x, y, z], axis=1)


def _check_reach(depth, mask, K, depth_scale: float) -> int:
    """Raise a precise GraspError if too few object points fall inside AnyGrasp's workspace."""
    pts = object_points(depth, mask, K, depth_scale)
    if len(pts) < MIN_POINTS:
        raise GraspError(f"only {len(pts)} object pixels have depth (need {MIN_POINTS}): "
                         "too small, too far, or a surface the depth camera cannot see")
    inside = ((pts[:, 2] <= WORKSPACE_MAX_Z_M) & (np.abs(pts[:, 0]) <= WORKSPACE_MAX_XY_M)
              & (np.abs(pts[:, 1]) <= WORKSPACE_MAX_XY_M))
    if inside.sum() < MIN_POINTS:
        raise GraspError(f"the object is {np.median(pts[:, 2]):.2f} m away; AnyGrasp's workspace "
                         f"ends at {WORKSPACE_MAX_Z_M} m from the camera")
    return int(len(pts))


def _b64(arr: np.ndarray) -> dict:
    arr = np.ascontiguousarray(arr)
    return {"dtype": str(arr.dtype), "shape": list(arr.shape),
            "data": base64.b64encode(arr.tobytes()).decode("ascii")}


def build_payload(rgb, depth, mask, K, *, depth_scale: float = 0.001, erode_px: int = 2,
                  margin_px: int = 16, crop: bool = True, up_cam=None, options: Optional[dict] = None):
    """-> (payload dict, number of object points). Inputs are full frames from one camera.

    rgb: uint8 HxWx3 in RGB order. depth: aligned to rgb, uint16 in units of `depth_scale` metres
    (float metres are converted to mm). mask: HxW, >0 = object. up_cam: optional 3-vector, the
    world's up direction in the camera frame (from an accelerometer: minus gravity).
    """
    rgb = np.asarray(rgb)
    depth = np.asarray(depth)
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise GraspError(f"rgb must be uint8 HxWx3, got {rgb.dtype} {rgb.shape}")
    if depth.dtype.kind == "f":  # metres -> mm, the servers' default unit
        depth = np.clip(np.round(np.nan_to_num(depth) * 1000.0), 0, 65535).astype(np.uint16)
        depth_scale = 0.001
    if depth.shape[:2] != rgb.shape[:2] or np.asarray(mask).shape[:2] != rgb.shape[:2]:
        raise GraspError(f"rgb {rgb.shape[:2]}, depth {depth.shape[:2]} and mask "
                         f"{np.asarray(mask).shape[:2]} must be the same size (depth aligned to colour)")
    mask = erode(np.asarray(mask), erode_px)
    if not mask.any():
        raise GraspError(f"the mask is empty after eroding {erode_px} px")
    K = np.asarray(K, dtype=np.float64).reshape(3, 3)
    n = _check_reach(depth, mask, K, depth_scale)
    if crop:
        rgb, depth, mask, K, _ = crop_to_mask(rgb, depth, mask, K, margin_px)
    payload = {"rgb": _b64(rgb), "depth": _b64(depth.astype(np.uint16)),
               "mask": _b64(mask.astype(np.uint8)), "intrinsics": _b64(K),
               "depth_scale": depth_scale}
    opts = dict(options or {})
    if up_cam is not None:
        opts["up_cam"] = [float(v) for v in up_cam]
    if opts:
        payload["options"] = opts
    return payload, n


def _num(v) -> float:
    return float("nan") if v is None else float(v)


def parse_grasp(d: dict) -> Grasp:
    """A server's grasp; upright and tilt are null (NaN here) when it knew no up direction."""
    return Grasp(translation=np.array(d["translation"], dtype=np.float64),
                 rotation=np.array(d["rotation"], dtype=np.float64),
                 width=float(d["width"]), score=float(d["score"]),
                 upright_score=_num(d.get("upright_score")),
                 tilt_from_floor_deg=_num(d.get("tilt_from_floor_deg")))


class AnyGraspClient:
    """One persistent WebSocket to an AnyGrasp server, reopened on failure. One request at a time."""

    def __init__(self, url: str, timeout_s: float = 20.0, open_timeout_s: float = 5.0):
        if not url:
            raise ValueError("no AnyGrasp URL (set ANYGRASP_URL, e.g. ws://host:8768/grasp)")
        self.url = url
        self.timeout = timeout_s
        self.open_timeout = open_timeout_s
        self._ws = None
        self._lock = threading.Lock()

    def _connect(self):
        from websockets.sync.client import connect

        # The original server's reply to a large payload is small, but requests are not:
        # no client-side size limit.
        self._ws = connect(self.url, open_timeout=self.open_timeout, max_size=None,
                           close_timeout=2, compression=None)

    def close(self):
        with self._lock:
            if self._ws is not None:
                self._ws.close()
                self._ws = None

    def request(self, payload: dict) -> dict:
        """Send one payload, -> the server's JSON reply. Retries once on a stale connection."""
        text = json.dumps(payload)
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._ws is None:
                        self._connect()
                    self._ws.send(text)
                    return json.loads(self._ws.recv(timeout=self.timeout))
                except TimeoutError as exc:
                    self._drop()
                    raise GraspError(f"no reply from {self.url} within {self.timeout:.0f} s", "timeout") from exc
                except Exception as exc:  # noqa: BLE001 - connection closed, refused, too big …
                    self._drop()
                    if attempt == 1:
                        raise GraspError(f"{self.url}: {type(exc).__name__}: {exc}", "unreachable") from exc
        raise AssertionError("unreachable")

    def _drop(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def grasp(self, rgb, depth, mask, K, **kw) -> GraspReply:
        """Full frames of one object -> its grasps. Keyword arguments as build_payload."""
        payload, n = build_payload(rgb, depth, mask, K, **kw)
        t0 = time.perf_counter()
        reply = self.request(payload)
        rt = (time.perf_counter() - t0) * 1000.0
        if reply.get("status") != "success":
            raise GraspError(f"AnyGrasp: {reply.get('message', reply)}", "server")
        return GraspReply(best=parse_grasp(reply["best_grasp"]),
                          candidates=[parse_grasp(c) for c in reply.get("candidates", [])],
                          mode=str(reply.get("mode", "")), points=n,
                          request_bytes=len(json.dumps(payload)),
                          server_ms=float(reply.get("timing_ms", {}).get("total", float("nan"))),
                          round_trip_ms=rt)


def rotation_to_quaternion(R) -> tuple[float, float, float, float]:
    """3x3 rotation -> (x, y, z, w), w >= 0."""
    R = np.asarray(R, dtype=np.float64)
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s)
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = (0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s, (R[2, 1] - R[1, 2]) / s)
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = ((R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s, (R[0, 2] - R[2, 0]) / s)
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = ((R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s, (R[1, 0] - R[0, 1]) / s)
    q = np.array(q)
    q /= np.linalg.norm(q)
    if q[3] < 0:
        q = -q
    return tuple(float(v) for v in q)
