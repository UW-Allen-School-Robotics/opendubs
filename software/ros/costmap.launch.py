"""Nav2 costmap of the body-filtered lidars and the depth camera. Started by costmap.sh.

The costmap executable's node name is costmap, and that is the name costmap.yaml is keyed
under. It publishes nav_msgs/OccupancyGrid on /costmap, header frame map.

Lidar inputs are /scan_filtered and /scan_rear_filtered from scan_body_filter.py (started
with opendubs-slam). The depth image becomes a point cloud, then a LaserScan on /scan_depth
in base_link (depth_scan.yaml). head_joints.py runs in the same launch so that scan can see
the camera.
"""
import os

from launch import LaunchDescription
from launch.actions import ExecuteProcess
from launch_ros.actions import ComposableNodeContainer, Node
from launch_ros.descriptions import ComposableNode

HERE = os.path.dirname(os.path.realpath(__file__))


def generate_launch_description():
    return LaunchDescription([
        # Lift and neck at the URDF zero, so camera_link connects to base_link for the depth scan.
        ExecuteProcess(
            cmd=["python3", os.path.join(HERE, "head_joints.py")],
            name="head_joints",
            output="screen",
        ),
        ComposableNodeContainer(
            name="depth_scan_container",
            namespace="",
            package="rclcpp_components",
            executable="component_container",
            output="screen",
            composable_node_descriptions=[
                ComposableNode(
                    package="depth_image_proc",
                    plugin="depth_image_proc::PointCloudXyzNode",
                    name="depth_to_cloud",
                    remappings=[
                        ("image_rect", "/camera/depth/image_raw"),
                        ("points", "/camera/depth/points"),
                    ],
                ),
                ComposableNode(
                    package="pointcloud_to_laserscan",
                    plugin="pointcloud_to_laserscan::PointCloudToLaserScanNode",
                    name="depth_scan",
                    remappings=[
                        ("cloud_in", "/camera/depth/points"),
                        ("scan", "/scan_depth"),
                    ],
                    parameters=[os.path.join(HERE, "depth_scan.yaml")],
                ),
            ],
        ),
        Node(
            package="nav2_costmap_2d",
            executable="nav2_costmap_2d",
            name="costmap",
            output="screen",
            parameters=[os.path.join(HERE, "costmap.yaml")],
        ),
    ])
