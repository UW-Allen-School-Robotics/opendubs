"""The perception server: one process, one copy of each model, two kinds of client.

- Navigation (stream): the navigation camera's latest frame, every `navigation.period_s`, becomes
  /perception/<camera>/traversability (mono8) and /perception/<camera>/obstacles (PointCloud2).
- Queries (action `segment`, perception_msgs/action/Segment): labels -> one mask per instance,
  with the colour, depth and camera_info of the frame used.

One worker thread makes every model call, so the models are never used concurrently. It runs a
navigation frame when one is due, and at most one query between two navigation frames, so queries
cannot starve navigation and navigation delays a query by at most one frame.

  python3 -m perception_server.server --config /work/software/perception/config/perception.yaml
"""

from __future__ import annotations

import argparse
import queue
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import rclpy
import yaml
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, PointCloud2, PointField

from perception_msgs.action import Segment
from perception_msgs.msg import Instance

from .frames import CameraFrames, image_to_numpy, intrinsics, numpy_to_image
from .models import SharedModels
from .nav import NavAdapter
from .query import QueryAdapter


@dataclass
class _Job:
    goal: Segment.Goal
    done: threading.Event = field(default_factory=threading.Event)
    result: Segment.Result | None = None


class PerceptionServer(Node):
    def __init__(self, cfg: dict):
        super().__init__("perception_server")
        self.cfg = cfg
        log = lambda s: self.get_logger().info(str(s))  # noqa: E731

        nav_cfg = cfg.get("navigation", {})
        groups = nav_cfg.get("groups", {})
        names = list(groups.keys())
        prompts = [p for g in names for p in groups[g]]
        group_ids = [i for i, g in enumerate(names) for _ in groups[g]]
        self.models = SharedModels(cfg["models"], prompts, group_ids, names, log=log)
        self.nav = NavAdapter(self.models, nav_cfg)
        self.query = QueryAdapter(self.models, cfg.get("query", {}))

        cb = ReentrantCallbackGroup()
        self.cameras: dict[str, CameraFrames] = {}
        for name, topics in cfg.get("cameras", {}).items():
            frames = CameraFrames(float(topics.get("slop_s", 0.05)))
            self.create_subscription(Image, topics["rgb"], frames.on_rgb, qos_profile_sensor_data, callback_group=cb)
            if topics.get("depth"):
                self.create_subscription(Image, topics["depth"], frames.on_depth, qos_profile_sensor_data, callback_group=cb)
            if topics.get("info"):
                self.create_subscription(CameraInfo, topics["info"], frames.on_info, qos_profile_sensor_data, callback_group=cb)
            self.cameras[name] = frames

        self.nav_enabled = bool(nav_cfg.get("enabled", False))
        self.nav_camera = nav_cfg.get("camera")
        self.nav_period = float(nav_cfg.get("period_s", 0.1))
        if self.nav_enabled:
            if self.nav_camera not in self.cameras:
                raise ValueError(f"navigation.camera {self.nav_camera!r} is not in cameras")
            self.pub_trav = self.create_publisher(Image, f"/perception/{self.nav_camera}/traversability", 5)
            self.pub_cloud = self.create_publisher(PointCloud2, f"/perception/{self.nav_camera}/obstacles", 5)

        self.jobs: queue.Queue[_Job] = queue.Queue()
        self.stats = {"nav": [], "query": [], "nav_skipped_stale": 0}
        self._last_nav_stamp = None
        self._running = True
        self.worker = threading.Thread(target=self._work, daemon=True)

        ActionServer(self, Segment, "segment", execute_callback=self._execute,
                     goal_callback=lambda _g: GoalResponse.ACCEPT,
                     cancel_callback=lambda _g: CancelResponse.REJECT, callback_group=cb)
        self.create_timer(10.0, self._report, callback_group=cb)
        self.worker.start()
        self.get_logger().info(
            f"perception server up: cameras={list(self.cameras)} "
            f"navigation={'on ' + self.nav_camera if self.nav_enabled else 'off'} action=segment")

    # --- the worker: every model call happens here -------------------------------------------

    def _work(self):
        next_nav = time.monotonic()
        while self._running and rclpy.ok():
            now = time.monotonic()
            if self.nav_enabled and now >= next_nav:
                next_nav = max(next_nav + self.nav_period, now)
                self._run_nav()
                if not self.jobs.empty():  # at most one query between navigation frames
                    self._run_query(self.jobs.get())
                continue
            timeout = max(0.0, next_nav - now) if self.nav_enabled else 0.5
            try:
                self._run_query(self.jobs.get(timeout=timeout))
            except queue.Empty:
                pass

    def _run_nav(self):
        frame = self.cameras[self.nav_camera].latest()
        if frame is None or frame.stamp == self._last_nav_stamp:
            self.stats["nav_skipped_stale"] += 1
            return
        self._last_nav_stamp = frame.stamp
        t0 = time.perf_counter()
        rgb = image_to_numpy(frame.rgb_msg)
        depth = image_to_numpy(frame.depth_msg) if frame.depth_msg is not None else None
        trav, points = self.nav.run(rgb, depth, intrinsics(frame.info))
        hdr = frame.rgb_msg.header
        self.pub_trav.publish(numpy_to_image(trav, "mono8", hdr))
        self.pub_cloud.publish(_cloud(points, hdr))
        self.stats["nav"].append((time.perf_counter() - t0) * 1000.0)

    def _run_query(self, job: _Job):
        g = job.goal
        res = Segment.Result()
        t0 = time.perf_counter()
        try:
            frames = self.cameras.get(g.camera)
            if frames is None:
                raise ValueError(f"unknown camera {g.camera!r}; known: {list(self.cameras)}")
            frame = frames.latest()
            if frame is None:
                raise RuntimeError(f"no frame received yet from camera {g.camera!r}")
            rgb = image_to_numpy(frame.rgb_msg)
            found = self.query.run(rgb, list(g.labels), float(g.min_score), int(g.source))
            hdr = frame.rgb_msg.header
            for f in found:
                inst = Instance()
                inst.label, inst.score = f["label"], float(f["score"])
                inst.box = [int(v) for v in f["box"]]
                inst.mask = numpy_to_image(f["mask"], "mono8", hdr)
                res.instances.append(inst)
            res.header, res.rgb = hdr, frame.rgb_msg
            if frame.depth_msg is not None:
                res.depth = frame.depth_msg
            if frame.info is not None:
                res.camera_info = frame.info
            res.success = True
            res.message = f"{len(found)} instance(s)"
        except Exception as exc:  # noqa: BLE001 - report to the client, keep serving
            res.success, res.message = False, f"{type(exc).__name__}: {exc}"
            self.get_logger().error(res.message)
        res.latency_ms = (time.perf_counter() - t0) * 1000.0
        self.stats["query"].append(res.latency_ms)
        job.result = res
        job.done.set()

    # --- ROS callbacks -------------------------------------------------------------------------

    def _execute(self, goal_handle):
        job = _Job(goal_handle.request)
        fb = Segment.Feedback()
        fb.stage = f"queued ({self.jobs.qsize()} ahead)"
        goal_handle.publish_feedback(fb)
        self.jobs.put(job)
        job.done.wait()
        if job.result.success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return job.result

    def _report(self):
        nav, qry = self.stats["nav"], self.stats["query"]
        t = self.models.timing
        parts = []
        if nav:
            parts.append(f"nav {len(nav) / 10.0:.1f} Hz, {np.mean(nav):.1f} ms mean, {np.max(nav):.1f} max")
        if qry:
            parts.append(f"queries {len(qry)}, {np.mean(qry):.1f} ms mean")
        if parts:
            stages = ", ".join(f"{k} {v:.1f}" for k, v in sorted(t.items()))
            self.get_logger().info(" | ".join(parts) + f" | last stages (ms): {stages}")
        self.stats["nav"], self.stats["query"] = [], []

    def shutdown(self):
        self._running = False


def _cloud(points: np.ndarray, header) -> PointCloud2:
    msg = PointCloud2()
    msg.header = header
    msg.height, msg.width = 1, int(points.shape[0])
    msg.fields = [PointField(name=n, offset=4 * i, datatype=PointField.FLOAT32, count=1)
                  for i, n in enumerate("xyz")]
    msg.is_bigendian, msg.point_step = False, 12
    msg.row_step = 12 * msg.width
    msg.is_dense = True
    msg.data = np.ascontiguousarray(points, dtype=np.float32).tobytes()
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args, ros_args = ap.parse_known_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    rclpy.init(args=ros_args)
    node = PerceptionServer(cfg)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
