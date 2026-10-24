"""The perception tasks node: actions that combine the perception server with off-robot models.

A separate process from perception_server, so a slow or unreachable workstation never holds up
the model worker or the navigation stream. It needs no GPU.

  ANYGRASP_URL=ws://HOST:8768/grasp \
  python3 -m perception_tasks.node --config /work/software/perception/config/perception_tasks.yaml

Actions: `grasp_object` (perception_msgs/action/GraspObject).
"""

from __future__ import annotations

import argparse
import os

import rclpy
import yaml
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from perception_msgs.action import GraspObject

from .grasp_object import GraspObjectTask


class PerceptionTasks(Node):
    def __init__(self, cfg: dict):
        super().__init__("perception_tasks")
        cb = ReentrantCallbackGroup()
        grasp_cfg = cfg.get("grasp_object", {})
        # The workstation's address is site-specific: from the environment, else the config.
        url = os.environ.get("ANYGRASP_URL") or grasp_cfg.get("anygrasp", {}).get("url", "")
        self.grasp = GraspObjectTask(self, grasp_cfg, url, cb)
        ActionServer(self, GraspObject, "grasp_object", execute_callback=self.grasp.execute,
                     goal_callback=lambda _g: GoalResponse.ACCEPT,
                     cancel_callback=lambda _g: CancelResponse.REJECT, callback_group=cb)
        self.get_logger().info(f"perception tasks up: action=grasp_object anygrasp={url}")

    def shutdown(self):
        self.grasp.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args, ros_args = ap.parse_known_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    rclpy.init(args=ros_args)
    node = PerceptionTasks(cfg)
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
