"""Nav2 behind the base lease. Started by nav2.sh (opendubs-nav2.service, off unless
/etc/opendubs/nav2.enabled exists, like SLAM). Needs SLAM running: Nav2 localizes on its map -> odom
and plans on its /map; there is no AMCL and no map server.

  - controller_server, planner_server, behavior_server, bt_navigator, velocity_smoother and their
    lifecycle manager, from nav2.yaml;
  - the remaps: the controller and behaviours publish cmd_vel_nav, the velocity smoother turns that into
    /base/<owner>/cmd_vel, the lease's topic. Nothing publishes /cmd_vel;
  - nav2_lease.py: claims the base while a goal is active, releases it after;
  - the depth camera flattened to /scan_depth (depth_image_proc + pointcloud_to_laserscan,
    depth_scan.yaml), one of the costmaps' three sources. It needs base_link -> camera_link in TF,
    which the head's joint states give (opendubs-head-joints).
"""
import os

from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode

HERE = os.path.dirname(os.path.realpath(__file__))
PARAMS = os.path.join(HERE, "nav2.yaml")
OWNER = os.environ.get("OPENDUBS_NAV_OWNER", "nav")
NAV = ["controller_server", "planner_server", "behavior_server", "velocity_smoother", "bt_navigator"]


def generate_launch_description():
    to_smoother = [("cmd_vel", "cmd_vel_nav")]
    remaps = {"controller_server": to_smoother, "behavior_server": to_smoother,
              "velocity_smoother": [("cmd_vel", "cmd_vel_nav"), ("cmd_vel_smoothed", f"/base/{OWNER}/cmd_vel")]}
    pkg = {"controller_server": "nav2_controller", "planner_server": "nav2_planner",
           "behavior_server": "nav2_behaviors", "velocity_smoother": "nav2_velocity_smoother",
           "bt_navigator": "nav2_bt_navigator"}
    nodes = [Node(package=pkg[n], executable=n, name=n, output="screen", parameters=[PARAMS],
                  remappings=remaps.get(n, [])) for n in NAV]
    return LaunchDescription(nodes + [
        Node(package="nav2_lifecycle_manager", executable="lifecycle_manager", name="lifecycle_manager_navigation",
             output="screen", parameters=[PARAMS]),
        ExecuteProcess(cmd=["python3", os.path.join(HERE, "nav2_lease.py")], name="nav2_lease", output="screen",
                       additional_env={"OPENDUBS_NAV_OWNER": OWNER}),
        ComposableNodeContainer(
            name="depth_scan_container", namespace="", package="rclcpp_components",
            executable="component_container", output="screen",
            composable_node_descriptions=[
                ComposableNode(package="depth_image_proc", plugin="depth_image_proc::PointCloudXyzNode",
                               name="depth_to_cloud",
                               remappings=[("image_rect", "/camera/depth/image_raw"), ("points", "/camera/depth/points")]),
                ComposableNode(package="pointcloud_to_laserscan",
                               plugin="pointcloud_to_laserscan::PointCloudToLaserScanNode", name="depth_scan",
                               remappings=[("cloud_in", "/camera/depth/points"), ("scan", "/scan_depth")],
                               parameters=[os.path.join(HERE, "depth_scan.yaml")]),
            ]),
    ])
