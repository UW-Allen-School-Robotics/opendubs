"""The perception tasks node: what the robot's other nodes call, built on the perception server and
off-robot models.

A separate process from perception_server, so a slow or unreachable workstation never holds up
the model worker. It needs no GPU.

  ANYGRASP_URL=ws://HOST:8768/grasp \
  python3 -m perception_tasks.node --config /work/software/perception/config/perception_tasks.yaml \
    --ros-args -r __ns:=/perception

Services (robot_api_interfaces, JSON payloads; api.py): detect, segment, locate, grasp_object
(RobotCommand) and status (RobotQuery). Action: grasp_object_action
(perception_msgs/action/GraspObject), which can also return the images.
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

from .api import PerceptionApi
from .grasp_object import GraspObjectTask


class PerceptionTasks(Node):
    def __init__(self, cfg: dict):
        super().__init__("perception_tasks")
        cb = ReentrantCallbackGroup()
        grasp_cfg = cfg.get("grasp_object", {})
        # The workstation's address is site-specific: from the environment, else the config.
        url = os.environ.get("ANYGRASP_URL") or grasp_cfg.get("anygrasp", {}).get("url", "")
        self.grasp = GraspObjectTask(self, grasp_cfg, url, cb)
        ActionServer(self, GraspObject, "grasp_object_action", execute_callback=self.grasp.execute,
                     goal_callback=lambda _g: GoalResponse.ACCEPT,
                     cancel_callback=lambda _g: CancelResponse.REJECT, callback_group=cb)
        self.api = PerceptionApi(self, self.grasp, cfg.get("api", {}), url, cb)
        self.get_logger().info(
            f"perception tasks up: services=detect,segment,locate,grasp_object,status "
            f"action=grasp_object_action anygrasp={url}")

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
    # Every service and action callback waits on a segment goal, whose replies need threads of
    # their own: room for several callers at once.
    executor = MultiThreadedExecutor(num_threads=8)
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
