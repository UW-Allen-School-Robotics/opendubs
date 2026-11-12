# Spot lane workplan

A Boston Dynamics Spot playing its ENCORE part alongside opendubs2. The design is
[`design/spot-driver.md`](design/spot-driver.md). Owned by the Spot lane, managed by the Choreography
lane. Nothing moves without a person present and the maintainers' GO, step by step.

## Waiting on others

| Item | From | State |
|---|---|---|
| `t0_wall` on the runner's status, from WAITING_FOR_START | Choreography (runner) | queued, after the demo freeze |
| `driver.beat_steps: false` in ENCORE's `spot` profile, its verdicts in `spot_driver.plan()` | Choreography, made in ENCORE | queued |
| The plan's window length as a `spot_driver.plan()` parameter (about 2 s) | Choreography, made in ENCORE | asked |
| A reserved address or name for Spot on the robot's router | Network | later, with the maintainers' word and a person at the robot |

## Spot as a capture platform (HallPass)

Spot records walks for HallPass's capture contract (`opendubs.capture/1`, with Spot support in
opendubs-site #26). Attended; the Ouster rides on the stowed arm, which doesn't move. The group's earlier Spot work (LRN, arXiv
2504.13149; VAMOS, arXiv 2510.20818) used an Ouster OS1 and a Jetson Orin AGX on Spot. Neither paper
states the mount, the power or the time sync.

| # | Step | State |
|---|---|---|
| C1 | At the robot, read only: the OS1's channel count and profile; the Orin's ROS 2, spot_ros2, ouster-ros and free disk; any recording setup left from the LRN and VAMOS work; the topic and frame names; PTP on the Orin's port | not started |
| C2 | The Orin records in the split layout. `light/`: odometry, `/tf`, `/tf_static`, `/joint_states`, the Ouster IMU, `camera_info`, power and state. `lidar/`: the Ouster's raw packets, continuous (low-data if enough). `heavy/`: body-camera keyframes (≥0.5 m, ≥10° or 2 s) plus full-rate bursts around each mark (snapshot mode, ±20 s, `heavy/bursts/`). Reuses #634's `floor_mapping.py` (keyframe gate, burst relay and snapshot recorder, manifest writer), `capture_camera.yaml` and `mark.py`/`mark.sh`, run with a Spot robot profile | not started; waits on the Thor lane making them configurable per robot |
| C2a | The Ouster "helmet" sits on the gripper with the arm STOWED (no arm motion). From the mount CAD (Fusion, Spot > oster hat): `body` → `os_sensor` = (0.466, 0.000, 0.346) m, no rotation (sensor +x forward, +z up, connector aft). One static TF, valid while the arm is stowed | done (from CAD) |
| C3 | After a walk: capture.yaml listing every part with its sha256, a `topics:` block and the static TF; copy off by wire; `sitekit capture push` light, then lidar, then heavy. Before that, replay `lidar/` through ouster-ros into `derived/points/` (`/ouster/points`): HallPass's derived scan reads it, in `flat_body`, z_m [-0.30, -0.15] | not started |

## Off the robot

| # | Step | State |
|---|---|---|
| 0.1 | The driver against ENCORE's plan and a fake runner (`runner.py`, `start="countdown"`), with no SDK connection | not started |
| 0.2 | The player interface (`describe`, `load`, `ready`, `start(at)`, `stop`, `status`) | not started |

## At the robot, one GO per step

| # | Step | State |
|---|---|---|
| 1 | Spot joins the robot's Wi-Fi (the password typed in by a maintainer), as does the E-Stop tablet. Connect read-only, motors off: the release (update if needed), the limits, the E-Stop status, the lost-link behaviour | not started |
| 2 | Power on, stand, stop, sit | not started |
| 3 | One body-only track (Duet's BODY) | not started |
| 4 | The arm and jaw (`spot.howl`) | not started |
| 5 | The base (`spot.turn`) | not started |
| 6 | Two robots on one clock; measure the driver's stop latency | needs `t0_wall` |
| 7 | Live behaviours (policy, teleop), with the guard in the driver | last |
