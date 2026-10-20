# The Orbbec Astra camera stack (retired 2026-10-20)

The original Orbbec Astra (USB `2bc5:0401`) was a stopgap camera on the robot. It has been removed,
and nothing in the stack starts it any more: `software/ros/provision-ros.sh` lists
`opendubs-camera` under `RETIRED`, so provisioning stops, disables and removes the unit on any
robot that still has it.

Kept here for the record, in case an Astra is ever fitted again:

- `astra-jazzy-build.sh` builds the legacy `orbbec/ros2_astra_camera` driver on ROS 2 Jazzy and
  aarch64, with the four patches it needs. The modern OrbbecSDK_ROS2 reports zero devices for the
  original Astra.
- `camera.sh` and `opendubs-camera.service` launched it, publishing `/camera/{color,depth,ir}/*`.

Nothing in the repo consumed `/camera/*` when it was retired. The head tablet's cameras are
`/tablet/camera/*`, and the RealSense tracker reads its D415 directly.
