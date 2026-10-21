# Capability Backlog

New capabilities to design + implement (distinct from robustness work in
[hardening-roadmap.md](history/hardening-roadmap.md), archived). Each is a TODO to be scoped before
implementation — captured here so nothing is lost.

Status legend: ☐ backlog · ◐ in progress · ☑ done

Last updated: 2026-09-29

---

## 1. Servo control — odometry pods & pan/tilt head  ◐

Two distinct servo subsystems (likely both via the M5 8Servo unit at I²C `0x25`
on Port A, already detected):

- **Deadwheel odometry pods (raise/lower).** Servo-actuated lift so the tracking
  wheels can be dropped for odometry on good surfaces and **retracted to handle
  uneven terrain and gap crossings (e.g. elevator thresholds)**. Needs: per-pod
  up/down command, position/limit awareness, and a policy for when to retract
  (tie into terrain/IMU + the robot state machine).
- **Pan/tilt "head." ☑** Built and driven from the Tab5 over I²C, with soft limits and
  home/center. Gaze control is live: the Thor's YOLO tracker sends pan/tilt commands over UDP
  and the Tab5 reports its actual angles back, so handover between tracking, the RC and the
  on-screen d-pad is visible off-board. Remaining: teleop as a third command source.
- **Considerations:** servo power budget (PDH channel), limit/stall handling,
  whether to drive from StamPLC firmware now vs. a ROS node later, and how
  raise/lower interacts with subsystem FSM states (mast/base).
- **Fusion tie-in:** the deadwheel pods feed the Pinpoint odometry computer
  (StamPLC I²C) — a first-class localization input. The raise/lower state IS the
  covariance gate for that source. See [localization-fusion.md](../docs/localization-fusion.md).

## 2. Camera control & viewing  ◐

**A RealSense D415 is fitted to the head**, and the YOLO face tracker reads it directly through
`pyrealsense2`; no ROS driver publishes it. (`opendubs-camera.service` ran the stopgap Orbbec Astra,
retired 2026-10-20.) The dashboard has a
camera pane but it still reads *"no feed configured"* — nothing is wired into it yet.

- Camera selection is a separate question — see [camera-selection.md](../docs/camera-selection.md);
  the D415 is a stopgap chosen for availability. Still open: live view in the dashboard, and
  recording/snapshots.
- Low-latency path should use **Thor NVENC** (see hardening track 5) — design the
  pipeline around hardware encode from the start, especially for teleop.
- Dashboard: a CAMERA tab; consider WebRTC/MJPEG for the kiosk vs. a separate
  high-rate path for VR teleop.
- Ties into pan/tilt head (gaze) and perception (Isaac ROS camera pipelines).

## 3. Onboard WiFi (router)  ☑ (fielded 2026-09)

- Onboard router so the robot is its own network island (no dependence on venue
  WiFi). Affects: Thor/StamPLC/teleop connectivity, the dashboard's
  network-status logic (`status._net`), VNC reachability, and ROS DDS discovery
  domains.
- **Fielded**: a MikroTik hAP ac3 running RouterOS at `192.168.88.1`, with UW-MPSK egress on
  its 5 GHz radio.
- Design in [network-architecture.md](../docs/network-architecture.md): a dedicated router
  rather than the Thor as AP, fleet subnet plan, student access gating.
- **The security debt is not closed** and is tracked in [punch-list.md](punch-list.md) §0:
  dashboard authN, and rosbridge/Foxglove still bound to `0.0.0.0`. Fix before any untrusted
  exposure. Still open beyond that: cellular, central fleet management, a dock.

## 4. Power usage & runtime prediction  ☐

- **The pack reports its own state of charge.** Its RS-485 BMS already sends SOC and pack
  voltage/current to the StamPLC, so the battery model, the voltage→SoC curve and coulomb
  counting from PDH current that this section originally called for are not needed.
- What is left is the derived figure and its presentation: **time-to-empty** from SOC plus
  measured draw, surfaced on the POWER / MASTER STATUS pages.
- Ties into the LED scheme (low-battery warning) and the state machine (DEGRADED
  on low power).

## 5. LIDAR implementation  ◐

- **Sensor: Slamtec RPLIDAR A1** on USB (CP2102 → `/dev/ttyUSB0`). ☑ detected +
  health-good (model 0x18, fw 1.29).
- **Driver: ☑ DONE** — ROS 2 **Jazzy** installed on the Thor; `ros-jazzy-rplidar-ros`
  runs as boot-persistent **`opendubs-rplidar.service`**, publishing
  `sensor_msgs/LaserScan` on **`/scan`** (~7 Hz, 0.15–12 m). Bringup in
  [../software/ros/](../software/ros/). This is the robot's first ROS 2 node — the start of the ROS
  migration ([hardening-roadmap.md](history/hardening-roadmap.md) track 4).
- **Remaining ☐:** TF tree (`base_link`→`laser`) + physical mounting; LIDAR
  dashboard tab (subscribe `/scan` via rosbridge); SLAM/Nav (cuVSLAM/nvblox) and
  obstacle avoidance — land with the rest of the perception stack.
- **Fusion role:** LIDAR SLAM provides the global `map→odom` tier; deadwheel + IMU
  provide the continuous `odom→base_link` tier — see
  [localization-fusion.md](../docs/localization-fusion.md).

## 6. Failure mode testing  ☐

- Systematic fault injection + observation: e-stop (each source), power loss, serial/USB drop
  (StamPLC, Tab5), RC link loss, PDH channel faults, subsystem faults, Thor thermal/throttle.
- Verify the state machine transitions correctly (→ DEGRADED / E_STOP_LATCH) and
  the robot reaches a safe state for each. Builds on the bench tests already done
  (software e-stop source discrimination; DI0 high). Produce a test matrix +
  expected-vs-actual.

## 7. VR teleop (Meta Quest)  ◐

- **Proof of life — PIPELINE VALIDATED end-to-end (2026-06-12):** native Unity app
  (Meta XR Core) → **ros-sharp → existing rosbridge :9090** (no new robot-side service)
  publishing the **input pipeline**: HMD + both controllers' 6-DoF pose (`PoseStamped`)
  + buttons/axes (`Joy`) on `/quest/*`. The whole Unity project (`Meta_Quest_Teleop`)
  was built **via the Unity MCP bridge** (see memory `unity-mcp-setup`): Meta XR Core +
  ros-sharp + the 2 publisher scripts + OVRCameraRig + RosConnector + 5 wired publishers.
  In **editor Play mode** all 5 `/quest/*` topics publish to the Thor at **~50 Hz**
  (verified with `ros2 topic hz`/`echo`). Poses are zero (no headset) but path/types are
  correct. Repo artifacts in [`software/teleop/`](../software/teleop/) (scripts, `sim_quest_client.py`,
  `verify-quest.sh`, `UNITY_SETUP.md`). **Remaining = APK build to the Quest (or Quest
  Link) for real pose data** — needs `runInBackground=true` (set) + the Android build
  (watch the Meta-XR namespace build issue).
- **Next (post-PoL):** `TELEOP_CONTROL` FSM state (state-machine-review.md R7) with
  CH5→RC_MANUAL preemption; teleop deadman (grip trigger + heartbeat); pose→setpoint
  mapping; then video to headset (NVENC + WebRTC — a D415 is now fitted, so this
  is no longer blocked on hardware).
- Safety: deadman + e-stop must apply to teleop the same as RC (StamPLC gate
  doesn't care *who* fails to assert enable).

---
