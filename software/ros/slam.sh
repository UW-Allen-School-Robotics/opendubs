#!/usr/bin/env bash
# OpenDubs SLAM wrapper: the robot-body scan filter and slam_toolbox, see slam.launch.py.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
ros2 launch /opt/opendubs/ros/slam.launch.py &
LAUNCH=$!
trap 'kill -INT "$LAUNCH" 2>/dev/null; wait "$LAUNCH"' TERM INT
# slam_toolbox's launch file activates the node only once configure has replied. Loading a saved map
# can take long enough that the reply is lost, leaving it configured but INACTIVE: no TF, no
# /initialpose (seen 2026-10-21 with a 10 MB pose graph). Finish the job whenever it sits inactive.
(
  for _ in $(seq 1 60); do
    sleep 5
    case "$(ros2 lifecycle get /slam_toolbox 2>/dev/null)" in
      active*) exit 0 ;;
      inactive*) echo "slam_toolbox is configured but inactive: activating it"
                 ros2 lifecycle set /slam_toolbox activate ;;
    esac
  done
  echo "slam_toolbox did not become active within 5 min"
) &
wait "$LAUNCH"
