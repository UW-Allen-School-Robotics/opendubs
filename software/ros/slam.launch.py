"""OpenDubs SLAM (mapping): the robot-body filter on /scan, then slam_toolbox online async.

Run by slam.sh under opendubs-slam.service, which is off unless /etc/opendubs/slam.enabled exists.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node

HERE = os.path.dirname(os.path.realpath(__file__))


def generate_launch_description():
    st = get_package_share_directory("slam_toolbox")
    return LaunchDescription([
        Node(package="laser_filters", executable="scan_to_scan_filter_chain", name="scan_body_filter",
             parameters=[os.path.join(HERE, "scan_body_filter.yaml")],
             remappings=[("scan", "/scan"), ("scan_filtered", "/scan_filtered")]),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(st, "launch", "online_async_launch.py")),
            launch_arguments={"slam_params_file": os.path.join(HERE, "slam_toolbox.yaml"),
                              "use_sim_time": "false"}.items()),
    ])
