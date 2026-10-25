"""The perception API: ROS 2 services with JSON payloads (robot_api_interfaces), for any node on the
robot. Under the node's namespace (/perception):

  detect        RobotCommand  {"labels": ["bottle", "cup"]}   -> instances: label, score, box, center_m
  segment       RobotCommand  the same, "include_masks": true -> each mask as a base64 PNG
  locate        RobotCommand  {"label": "bottle"}             -> the best instance's 3D position
  grasp_object  RobotCommand  {"label": "bottle"}             -> grasp position + orientation (AnyGrasp)
  status        RobotQuery                                    -> camera, models, AnyGrasp health

Optional request fields: "camera" (default from the config), "min_score" (detector floor; 0 = the
server's), "source" ("auto" | "detector" | "clipseg"); grasp_object also "up_cam" [x, y, z], the
world's up direction in the camera frame. Positions and orientations are in the camera's optical
frame (x right, y down, z forward; "frame_id" in the reply), in metres; a grasp's orientation is
GraspNet's (its x axis the approach, y the closing direction). `detect` and `segment` run the
same models (a box needs its mask for a 3D centre); `segment` adds the masks to the reply.

Replies are small (no images unless masks are asked for), so they cross between processes on this
host over UDP. A caller outside the perception container sets FASTDDS_BUILTIN_TRANSPORTS=UDPv4.
"""

from __future__ import annotations

import base64
import json
import math
import threading
import time
import urllib.request

import cv2
import numpy as np
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo

from perception_clients.anygrasp import GraspError
from robot_api_interfaces.msg import ResultCode
from robot_api_interfaces.srv import RobotCommand, RobotQuery

from .grasp_object import image_array

SOURCES = {"auto": 0, "detector": 1, "clipseg": 2}
KIND_CODES = {
    "input": ResultCode.INVALID_INPUT,
    "not_found": ResultCode.NOT_FOUND,
    "timeout": ResultCode.TIMEOUT,
    "unreachable": ResultCode.SERVICE_NOT_RUNNING,
    "unavailable": ResultCode.SERVICE_NOT_RUNNING,
    "server": ResultCode.FAILURE,
}


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _clean(v):
    """JSON has no NaN or infinity: they become null."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _r(x, n=4):
    return round(float(x), n)


def _stamp(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


class PerceptionApi:
    def __init__(self, node, grasp_task, cfg: dict, url: str, callback_group):
        self.node = node
        self.task = grasp_task
        self.url = url
        self.camera = cfg.get("default_camera", "femto")
        self._info_lock = threading.Lock()
        self._info: dict[str, tuple[float, int]] = {}  # camera -> (last camera_info time, count)
        self._t_start = time.time()
        for name, topic in cfg.get("camera_info", {"femto": "/camera/color/camera_info"}).items():
            node.create_subscription(CameraInfo, topic, lambda _m, n=name: self._on_info(n),
                                     qos_profile_sensor_data, callback_group=callback_group)
        for name, handler in (("detect", self.detect), ("segment", self.segment),
                              ("locate", self.locate), ("grasp_object", self.grasp_object)):
            node.create_service(RobotCommand, name, self._command(name, handler), callback_group=callback_group)
        node.create_service(RobotQuery, "status", self._status, callback_group=callback_group)

    # --- plumbing ---------------------------------------------------------------------------------

    def _on_info(self, camera: str):
        with self._info_lock:
            _, n = self._info.get(camera, (0.0, 0))
            self._info[camera] = (time.time(), n + 1)

    def _command(self, name, handler):
        def serve(request, response):
            t0 = time.perf_counter()
            try:
                try:
                    req = json.loads(request.req) if request.req.strip() else {}
                except json.JSONDecodeError as exc:
                    raise ApiError(ResultCode.INVALID_INPUT, f"req is not JSON: {exc}") from exc
                if not isinstance(req, dict):
                    raise ApiError(ResultCode.INVALID_INPUT, "req must be a JSON object")
                data, message = handler(req)
                code = ResultCode.SUCCESS
            except ApiError as exc:
                data, message, code = {}, str(exc), exc.code
            except GraspError as exc:
                data, message, code = {}, str(exc), KIND_CODES.get(exc.kind, ResultCode.FAILURE)
            except Exception as exc:  # noqa: BLE001 - report to the caller, keep serving
                data, message, code = {}, f"{type(exc).__name__}: {exc}", ResultCode.FAILURE
            data["elapsed_ms"] = _r((time.perf_counter() - t0) * 1000.0, 1)
            response.result_code.result_code = code
            response.result_code.message = message
            response.data = json.dumps(_clean(data))
            line = f"{name} {request.req.strip()[:120]}: [{code}] {message} | {data['elapsed_ms']:.0f} ms"
            # One call site per severity: rclpy raises if a call site's severity changes between calls.
            if code == ResultCode.SUCCESS:
                self.node.get_logger().info(line)
            else:
                self.node.get_logger().warning(line)
            return response
        return serve

    def _args(self, req: dict, *, many: bool):
        if many:
            labels = req.get("labels", req.get("label"))
            if isinstance(labels, str):
                labels = [labels]
            if not isinstance(labels, list) or not labels or not all(isinstance(s, str) and s.strip() for s in labels):
                raise ApiError(ResultCode.INVALID_INPUT, 'give "labels": a non-empty list of names')
        else:
            labels = req.get("label")
            if not isinstance(labels, str) or not labels.strip():
                raise ApiError(ResultCode.INVALID_INPUT, 'give "label": a name, e.g. "bottle"')
        source = req.get("source", "auto")
        if source not in SOURCES:
            raise ApiError(ResultCode.INVALID_INPUT, f'"source" must be one of {list(SOURCES)}')
        try:
            min_score = float(req.get("min_score", 0.0))
        except (TypeError, ValueError) as exc:
            raise ApiError(ResultCode.INVALID_INPUT, '"min_score" must be a number') from exc
        camera = req.get("camera", self.camera)
        if not isinstance(camera, str):
            raise ApiError(ResultCode.INVALID_INPUT, '"camera" must be a string')
        return labels, camera, min_score, SOURCES[source]

    @staticmethod
    def _frame(seg, camera: str) -> dict:
        k = seg.camera_info.k
        return {"camera": camera, "frame_id": seg.header.frame_id, "stamp": _stamp(seg.header),
                "image_size": [int(seg.rgb.width), int(seg.rgb.height)],
                "intrinsics": {"fx": k[0], "fy": k[4], "cx": k[2], "cy": k[5]}}

    @staticmethod
    def _instances(seg, include_masks: bool) -> list:
        depth = None
        if seg.depth.data:  # 16UC1 in mm as the Femto Bolt publishes it; 32FC1 in metres
            d = image_array(seg.depth).astype(np.float64)
            depth = d if seg.depth.encoding.lower() == "32fc1" else d / 1000.0
        K = np.array(seg.camera_info.k, dtype=np.float64).reshape(3, 3) if seg.camera_info.k[0] else None
        out = []
        for i, inst in enumerate(seg.instances):
            mask = image_array(inst.mask) > 0
            entry = {"index": i, "label": inst.label, "score": _r(inst.score, 3),
                     "box": [int(v) for v in inst.box], "mask_px": int(mask.sum()),
                     "center_m": None, "depth_valid": None}
            if depth is not None and K is not None and mask.any():
                v, u = np.nonzero(mask & (depth > 0))
                entry["depth_valid"] = _r(len(v) / int(mask.sum()), 2)
                if len(v):
                    z = depth[v, u]
                    pts = np.stack([(u - K[0, 2]) * z / K[0, 0], (v - K[1, 2]) * z / K[1, 1], z], 1)
                    entry["center_m"] = [_r(c) for c in np.median(pts, 0)]
            if include_masks:
                ok, png = cv2.imencode(".png", mask.astype(np.uint8) * 255)
                entry["mask_png_b64"] = base64.b64encode(png.tobytes()).decode("ascii") if ok else None
            out.append(entry)
        return out

    # --- the services -----------------------------------------------------------------------------

    def _segmented(self, req: dict, include_masks: bool):
        labels, camera, min_score, source = self._args(req, many=True)
        t0 = time.perf_counter()
        seg = self.task.segment(labels, camera, min_score, source)
        data = self._frame(seg, camera)
        data["segment_ms"] = _r((time.perf_counter() - t0) * 1000.0, 1)
        data["instances"] = self._instances(seg, include_masks)
        if not data["instances"]:
            raise ApiError(ResultCode.NOT_FOUND, f"none of {labels} found on camera {camera!r}")
        found = ", ".join(f"{d['label']} {d['score']}" for d in data["instances"])
        return data, f"{len(data['instances'])} instance(s): {found}"

    def detect(self, req):
        return self._segmented(req, include_masks=False)

    def segment(self, req):
        return self._segmented(req, include_masks=bool(req.get("include_masks", False)))

    def locate(self, req):
        label, camera, min_score, source = self._args(req, many=False)
        t0 = time.perf_counter()
        seg = self.task.segment([label], camera, min_score, source)
        instances = self._instances(seg, include_masks=False)
        if not instances:
            raise ApiError(ResultCode.NOT_FOUND, f"no {label!r} found on camera {camera!r}")
        best = instances[0]  # Segment sorts by score
        if best["center_m"] is None:
            raise ApiError(ResultCode.FAILURE, f"{label!r} found, but none of its pixels has depth")
        x, y, z = best["center_m"]
        data = self._frame(seg, camera)
        data.update({"label": best["label"], "score": best["score"], "box": best["box"],
                     "position": {"x": x, "y": y, "z": z}, "depth_valid": best["depth_valid"],
                     "instances_found": len(instances),
                     "segment_ms": _r((time.perf_counter() - t0) * 1000.0, 1)})
        return data, f"{label} ({best['score']}) at ({x:.3f}, {y:.3f}, {z:.3f}) m"

    def grasp_object(self, req):
        label, camera, min_score, source = self._args(req, many=False)
        up = req.get("up_cam")
        if up is not None and (not isinstance(up, list) or len(up) != 3):
            raise ApiError(ResultCode.INVALID_INPUT, '"up_cam" must be [x, y, z]')
        r = self.task.run(label, camera, min_score, source, up)
        seg, inst, reply = r["seg"], r["instance"], r["reply"]

        def pose(g):
            qx, qy, qz, qw = g.quaternion()
            t = g.translation
            return {"position": {"x": _r(t[0]), "y": _r(t[1]), "z": _r(t[2])},
                    "orientation": {"x": _r(qx, 6), "y": _r(qy, 6), "z": _r(qz, 6), "w": _r(qw, 6)},
                    "width": _r(g.width), "grasp_score": _r(g.score),
                    "upright_score": _r(g.upright_score), "tilt_from_floor_deg": _r(g.tilt_from_floor_deg, 2)}

        b = reply.best
        data = self._frame(seg, camera)
        data.update({"label": inst.label, "score": _r(inst.score, 3), "box": [int(v) for v in inst.box],
                     **pose(b), "mode": reply.mode, "instances_found": len(seg.instances),
                     "candidates": [pose(c) for c in reply.candidates],
                     "segment_ms": _r(r["segment_ms"], 1), "grasp_ms": _r(r["grasp_ms"], 1)})
        t = b.translation
        return data, (f"{inst.label} ({inst.score:.2f}): grasp at ({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}) m, "
                      f"width {b.width:.3f} m, score {b.score:.3f}")

    def _status(self, _request, response):
        now = time.time()
        with self._info_lock:
            cams = {c: {"last_info_age_s": _r(now - t, 2), "camera_info_received": n}
                    for c, (t, n) in self._info.items()}
        server_up = self.task.segment_client.server_is_ready()
        health = None
        try:
            hurl = self.url.replace("wss://", "https://").replace("ws://", "http://").rsplit("/", 1)[0] + "/health"
            with urllib.request.urlopen(hurl, timeout=2.0) as f:
                health = json.loads(f.read().decode())
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            health = {"status": "unreachable", "error": f"{type(exc).__name__}: {exc}"}
        cam_ok = bool(cams) and all(c["last_info_age_s"] < 2.0 for c in cams.values())
        anygrasp_ok = health.get("status") == "ok"
        problems = [p for p, ok in (("no camera frames", cam_ok), ("perception server down", server_up),
                                    ("AnyGrasp unreachable", anygrasp_ok)) if not ok]
        data = {"cameras": cams, "perception_server": "up" if server_up else "down",
                "anygrasp": {"url": self.url, "health": health},
                "services": ["detect", "segment", "locate", "grasp_object", "status"],
                "uptime_s": _r(now - self._t_start, 1)}
        response.result_code.result_code = ResultCode.SUCCESS if not problems else ResultCode.SERVICE_NOT_RUNNING
        response.result_code.message = "all up" if not problems else "; ".join(problems)
        response.data = json.dumps(_clean(data))
        return response
