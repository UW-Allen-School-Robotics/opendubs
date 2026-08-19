# Capability Backlog

New capabilities to design + implement (distinct from robustness work in
[hardening-roadmap.md](history/hardening-roadmap.md)). Each is a TODO to be scoped before
implementation — captured here so nothing is lost.

Status legend: ☐ backlog · ◐ in progress · ☑ done

Last updated: 2026-06-08

---

## 1. Servo control — odometry pods & pan/tilt head  ☐

Two distinct servo subsystems (likely both via the M5 8Servo unit at I²C `0x25`
on Port A, already detected):

- **Deadwheel odometry pods (raise/lower).** Servo-actuated lift so the tracking
  wheels can be dropped for odometry on good surfaces and **retracted to handle
  uneven terrain and gap crossings (e.g. elevator thresholds)**. Needs: per-pod
  up/down command, position/limit awareness, and a policy for when to retract
  (tie into terrain/IMU + the robot state machine).
- **Pan/tilt "head."** 2-DOF servo mount for the head/sensor payload. Needs:
  pan/tilt command, soft limits, home/center, and eventual coordination with
  camera/perception (gaze control) and teleop.
- **Considerations:** servo power budget (PDH channel), limit/stall handling,
  whether to drive from StampLC firmware now vs. a ROS node later, and how
  raise/lower interacts with subsystem FSM states (mast/base).
- **Fusion tie-in:** the deadwheel pods feed the Pinpoint odometry computer
  (StampLC I²C) — a first-class localization input. The raise/lower state IS the
  covariance gate for that source. See [localization-fusion.md](localization-fusion.md).

## 2. Camera control & viewing  ☐

- Camera selection, live view in the dashboard, and recording/snapshots.
- Low-latency path should use **Thor NVENC** (see hardening track 5) — design the
  pipeline around hardware encode from the start, especially for teleop.
- Dashboard: a CAMERA tab; consider WebRTC/MJPEG for the kiosk vs. a separate
  high-rate path for VR teleop.
- Ties into pan/tilt head (gaze) and perception (Isaac ROS camera pipelines).

## 3. Onboard WiFi (router)  ☐

- Onboard router so the robot is its own network island (no dependence on venue
  WiFi). Affects: Thor/StampLC/teleop connectivity, the dashboard's
  network-status logic (`status._net`), VNC reachability, and ROS DDS discovery
  domains.
- **Design captured in [network-architecture.md](network-architecture.md)** — UW
  deployment (CSE home net + UWMPSK/eduroam egress), a **dedicated OpenWrt router
  (not the Thor as AP)**, fleet subnet plan, student access gating, and the
  security debt to fix before exposure (dashboard authN; rosbridge/foxglove are
  `0.0.0.0` today). Remaining: router SKU, cellular?, central fleet mgmt, dock.

## 4. Power usage & runtime prediction  ☐

- Use existing telemetry (PDH per-channel current/power, StampLC bus voltage,
  Jetson rails via tegrastats) to estimate **state of charge and time-to-empty**.
- Needs a battery model (chemistry, capacity, voltage→SoC curve, sag under load),
  coulomb counting from PDH current, and a runtime estimate surfaced on the POWER
  / MASTER STATUS pages.
- Ties into the LED scheme (low-battery warning) and the state machine (DEGRADED
  on low power).

## 5. LIDAR implementation  ◐

- **Sensor: Slamtec RPLIDAR A1** on USB (CP2102 → `/dev/ttyUSB0`). ☑ detected +
  health-good (model 0x18, fw 1.29).
- **Driver: ☑ DONE** — ROS 2 **Jazzy** installed on the Thor; `ros-jazzy-rplidar-ros`
  runs as boot-persistent **`opendubs-rplidar.service`**, publishing
  `sensor_msgs/LaserScan` on **`/scan`** (~7 Hz, 0.15–12 m). Bringup in
  [../ros/](../ros/). This is the robot's first ROS 2 node — the start of the ROS
  migration ([hardening-roadmap.md](history/hardening-roadmap.md) track 4).
- **Remaining ☐:** TF tree (`base_link`→`laser`) + physical mounting; LIDAR
  dashboard tab (subscribe `/scan` via rosbridge); SLAM/Nav (cuVSLAM/nvblox) and
  obstacle avoidance — land with the rest of the perception stack.
- **Fusion role:** LIDAR SLAM provides the global `map→odom` tier; deadwheel + IMU
  provide the continuous `odom→base_link` tier — see
  [localization-fusion.md](localization-fusion.md).

## 6. Failure mode testing  ☐

- Systematic fault injection + observation: e-stop (each source), power loss
  (battery, shore, total), serial/USB drop (Pixhawk + StampLC), RC link loss,
  PDH channel faults, subsystem faults, Thor thermal/throttle.
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
  correct. Repo artifacts in [`../teleop/`](../teleop/) (scripts, `sim_quest_client.py`,
  `verify-quest.sh`, `UNITY_SETUP.md`). **Remaining = APK build to the Quest (or Quest
  Link) for real pose data** — needs `runInBackground=true` (set) + the Android build
  (watch the Meta-XR namespace build issue).
- **Next (post-PoL):** `TELEOP_CONTROL` FSM state (state-machine-review.md R7) with
  CH5→RC_MANUAL preemption; teleop deadman (grip trigger + heartbeat); pose→setpoint
  mapping; then video to headset (NVENC + WebRTC — deferred until a camera is wired).
- Safety: deadman + e-stop must apply to teleop the same as RC (StampPLC gate
  doesn't care *who* fails to assert enable).

---

## Open design questions

### Ideal-diode / shore-power wiring  ☑ (resolved 2026-06)

Resolved in the Figma schematic + [power-architecture.md](history/power-architecture.md):
- Source-OR = **Victron Argo Diode Battery Combiner (BCD 802)** — passive
  highest-voltage-wins + inherent reverse-block; no custom ideal-diode board.
- E-stop = two-channel: dual-pole NC buttons (one pole → contactor coil in series
  with StampLC R0; other pole → DI0 active-low sense) + DI6 weld detect.
- The contactor sits between the OR junction (DBC → regen clamp) and the PDH;
  **logic (Thor/StampLC/Pixhawk) is on a separate mini-PDH upstream of the
  contactor**, so an e-stop cuts actuators not the brain.

Remaining bench items live in [power-architecture.md](history/power-architecture.md)
"Open items" (autobrake current → regen-clamp sizing, shore PSU wattage, etc.).
