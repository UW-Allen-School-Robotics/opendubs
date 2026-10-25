"""GraspObject: a label -> the best grasp on that object, in the camera's optical frame.

Segment (perception_server) gives the instance's mask with the colour, aligned depth and
camera_info of the same frame; AnyGrasp (perception_clients.anygrasp) turns them into grasps. The
depth goes over raw, uint16 mm as the camera publishes it. The highest-scoring instance is used.

`run()` does the work for both the action (`execute`) and the /perception/grasp_object service
(api.py). Its errors are GraspErrors whose `kind` the service turns into a ResultCode.
"""

from __future__ import annotations

import threading
import time

import numpy as np
from rclpy.action import ActionClient
from sensor_msgs.msg import Image

from perception_clients.anygrasp import AnyGraspClient, GraspError
from perception_msgs.action import GraspObject, Segment
from perception_msgs.msg import Grasp


def image_array(msg: Image) -> np.ndarray:
    """rgb8 / bgr8 -> RGB uint8 HxWx3; mono8 -> uint8 HxW; 16UC1 -> uint16 HxW; 32FC1 -> float32 HxW."""
    enc = msg.encoding.lower()
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    if enc in ("rgb8", "bgr8"):
        a = buf.reshape(msg.height, msg.step)[:, : msg.width * 3].reshape(msg.height, msg.width, 3)
        return np.ascontiguousarray(a[:, :, ::-1] if enc == "bgr8" else a)
    if enc == "mono8":
        return buf.reshape(msg.height, msg.step)[:, : msg.width]
    if enc in ("16uc1", "mono16"):
        return buf.view(np.uint16).reshape(msg.height, msg.step // 2)[:, : msg.width]
    if enc == "32fc1":
        return buf.view(np.float32).reshape(msg.height, msg.step // 4)[:, : msg.width]
    raise ValueError(f"unsupported image encoding {msg.encoding!r}")


def grasp_msg(g) -> Grasp:
    m = Grasp()
    m.pose.position.x, m.pose.position.y, m.pose.position.z = (float(v) for v in g.translation)
    q = g.quaternion()
    m.pose.orientation.x, m.pose.orientation.y, m.pose.orientation.z, m.pose.orientation.w = q
    m.width, m.score = float(g.width), float(g.score)
    m.upright_score, m.tilt_from_floor_deg = float(g.upright_score), float(g.tilt_from_floor_deg)
    return m


class GraspObjectTask:
    def __init__(self, node, cfg: dict, url: str, callback_group):
        self.node = node
        self.segment_client = ActionClient(node, Segment, cfg.get("segment_action", "segment_action"),
                                           callback_group=callback_group)
        self.segment_timeout = float(cfg.get("segment_timeout_s", 10.0))
        ag = cfg.get("anygrasp", {})
        self.client = AnyGraspClient(url, timeout_s=float(ag.get("timeout_s", 5.0)))
        self.kw = {"erode_px": int(ag.get("erode_px", 2)), "margin_px": int(ag.get("margin_px", 16))}
        self.options = {"top_k": int(ag.get("top_k", 5))}

    def segment(self, labels, camera: str, min_score: float = 0.0, source: int = 0):
        """-> Segment.Result, or raise. Waits on events: this runs inside an action or service callback."""
        if not self.segment_client.wait_for_server(timeout_sec=2.0):
            raise GraspError("the perception server (segment_action) is not running", "unavailable")
        done, box = threading.Event(), {}

        def on_result(fut):
            box["result"] = fut.result()
            done.set()

        def on_goal(fut):
            handle = fut.result()
            if not handle.accepted:
                box["result"] = None
                done.set()
                return
            handle.get_result_async().add_done_callback(on_result)

        sg = Segment.Goal(labels=list(labels), camera=camera, min_score=float(min_score), source=int(source))
        self.segment_client.send_goal_async(sg).add_done_callback(on_goal)
        if not done.wait(self.segment_timeout):
            raise GraspError(f"segment did not answer within {self.segment_timeout:.0f} s", "timeout")
        if box["result"] is None:
            raise GraspError("segment rejected the goal", "unavailable")
        seg = box["result"].result
        if not seg.success:
            # The server's own failures: an unknown camera is the caller's, no frames is the camera's.
            kind = "input" if "unknown camera" in seg.message else "unavailable"
            raise GraspError(f"segment: {seg.message}", kind)
        return seg

    def run(self, label: str, camera: str, min_score: float = 0.0, source: int = 0, up=None) -> dict:
        """-> {seg, instance, reply, segment_ms, grasp_ms}, or raise GraspError."""
        if not label.strip():
            raise GraspError("no label", "input")
        t0 = time.perf_counter()
        seg = self.segment([label], camera, min_score, source)
        segment_ms = (time.perf_counter() - t0) * 1000.0
        if not seg.instances:
            raise GraspError(f"no {label!r} found on camera {camera!r}", "not_found")
        if not seg.depth.data:
            raise GraspError(f"camera {camera!r} sent no depth with this frame", "unavailable")
        if seg.camera_info.k[0] == 0.0:
            raise GraspError(f"camera {camera!r} has no camera_info", "unavailable")
        inst = seg.instances[0]  # Segment sorts by score
        K = np.array(seg.camera_info.k, dtype=np.float64).reshape(3, 3)
        up = None if up is None or np.linalg.norm(up) < 1e-6 else np.asarray(up, dtype=np.float64)
        t1 = time.perf_counter()
        reply = self.client.grasp(image_array(seg.rgb), image_array(seg.depth), image_array(inst.mask), K,
                                  up_cam=up, options=self.options, **self.kw)
        return {"seg": seg, "instance": inst, "reply": reply, "segment_ms": segment_ms,
                "grasp_ms": (time.perf_counter() - t1) * 1000.0}

    def execute(self, goal_handle):
        goal = goal_handle.request
        res = GraspObject.Result()
        fb = GraspObject.Feedback()
        try:
            fb.stage = "segmenting, then grasping"
            goal_handle.publish_feedback(fb)
            r = self.run(goal.label, goal.camera, goal.min_score, goal.source,
                         [goal.up_cam.x, goal.up_cam.y, goal.up_cam.z])
            seg, inst, reply = r["seg"], r["instance"], r["reply"]
            res.segment_ms, res.grasp_ms = r["segment_ms"], r["grasp_ms"]
            res.header, res.rgb, res.camera_info = seg.header, seg.rgb, seg.camera_info
            res.instances_found = len(seg.instances)
            res.instance = inst
            res.grasp = grasp_msg(reply.best)
            res.candidates = [grasp_msg(c) for c in reply.candidates]
            res.mode = reply.mode
            if not goal.include_images:  # pose only: a few hundred bytes, fine over UDP
                res.rgb = Image()
                res.instance.mask = Image()
            res.success = True
            t = reply.best.translation
            res.message = (f"{inst.label} ({inst.score:.2f}, 1 of {len(seg.instances)}): grasp at "
                           f"({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}) m, width {reply.best.width:.3f} m, "
                           f"score {reply.best.score:.3f}")
        except GraspError as exc:
            res.success, res.message = False, str(exc)
        except Exception as exc:  # noqa: BLE001 - report to the client, keep serving
            res.success, res.message = False, f"{type(exc).__name__}: {exc}"
        line = (f"grasp_object_action {goal.label!r} on {goal.camera}: {res.message} | "
                f"segment {res.segment_ms:.0f} ms, grasp {res.grasp_ms:.0f} ms")
        # One call site per severity: rclpy raises if a call site's severity changes between calls.
        if res.success:
            self.node.get_logger().info(line)
            goal_handle.succeed()
        else:
            self.node.get_logger().error(line)
            goal_handle.abort()
        return res

    def shutdown(self):
        self.client.close()
