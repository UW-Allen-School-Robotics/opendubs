"""OpenDubs SLAM: the robot-body filter on /scan (scan_body_filter.py), then slam_toolbox.

Run by slam.sh under opendubs-slam.service, which is off unless /etc/opendubs/slam.enabled exists.

The mode follows the robot's site file (/var/lib/opendubs/maps/site.yaml, see floorplan.py):
  - it names a saved map whose pose graph is there  -> LOCALIZATION on that map, starting at the
    site file's start_pose (where the mapping drive began). The map frame then stays the saved
    map's, which is what the floor plan was fitted to. Correct the start with /initialpose.
  - otherwise                                        -> MAPPING, a fresh map from wherever it starts.
"""
import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription, LogInfo
from launch.launch_description_sources import PythonLaunchDescriptionSource

HERE = os.path.dirname(os.path.realpath(__file__))
SITE = os.environ.get("OPENDUBS_SITE", "/var/lib/opendubs/maps/site.yaml")
PARAMS_OUT = "/tmp/opendubs-slam-params.yaml"


def slam_params(base_path=os.path.join(HERE, "slam_toolbox.yaml"), site_path=SITE):
    """(mode, parameters file contents) for this robot. Pure but for reading the two files."""
    params = yaml.safe_load(open(base_path))
    p = params["slam_toolbox"]["ros__parameters"]
    site = yaml.safe_load(open(site_path)) if os.path.exists(site_path) else None
    if site and site.get("map"):
        base = os.path.join(os.path.dirname(site_path), site["map"])
        if os.path.exists(base + ".posegraph"):
            p["mode"] = "localization"
            p["map_file_name"] = base
            p["map_start_pose"] = [float(v) for v in site.get("start_pose", (0.0, 0.0, 0.0))]
            return "localization", params
    p["mode"] = "mapping"
    return "mapping", params


def generate_launch_description():
    st = get_package_share_directory("slam_toolbox")
    mode, params = slam_params()
    with open(PARAMS_OUT, "w") as f:
        yaml.safe_dump(params, f)
    launch = "localization_launch.py" if mode == "localization" else "online_async_launch.py"
    what = (f"localizing on {params['slam_toolbox']['ros__parameters']['map_file_name']}"
            if mode == "localization" else "mapping (no saved map named in the site file)")
    return LaunchDescription([
        LogInfo(msg=f"opendubs slam: {what}"),
        ExecuteProcess(cmd=["python3", os.path.join(HERE, "scan_body_filter.py")], name="scan_body_filter",
                       output="screen"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(st, "launch", launch)),
            launch_arguments={"slam_params_file": PARAMS_OUT, "use_sim_time": "false"}.items()),
    ])
