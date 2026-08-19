# Localization & Sensor Fusion Strategy

**Status: design — not yet implemented.** Companion to
[state-machines.md](state-machines.md), capability-backlog.md
(#1 deadwheel raise/lower, #5 LIDAR/SLAM, #2 cameras),
hardening-roadmap.md (tracks 4 ROS migration, 8 timesync).

## Why this exists

The robot has several partially-redundant motion/pose sources, each with a
different failure mode. The point of the **deadwheel odometry** is a
ground-truth-ish *relative* movement source that survives when **global** sources
(GPS, visual/LIDAR SLAM) drop out. The fusion strategy is how we combine them and
**degrade gracefully** rather than losing localization when any one channel dies.

## Sources (current + planned)

| Source | Via | Gives | Strong | Fails when |
|---|---|---|---|---|
| **Pinpoint deadwheel odo** | StampLC I²C | 2D pose + twist (x, y, θ) | smooth, high-rate, short-term | pods **retracted by design** (rough terrain / gap crossings), wheel slip, lift |
| **RPLIDAR A1 SLAM** | `/scan` | 2D scan-match pose + map | structured indoor | open/featureless/glass, outdoors > 12 m, ~7 Hz |
| **Visual SLAM / VIO** (future ZED/RealSense, cuVSLAM) | USB3 | 6-DOF VIO + map | textured, lit | low-texture, low-light, motion blur |

## Principle: two-tier localization (REP-105)

`map → odom → base_link`.

- **`odom → base_link`** — *continuous, smooth, high-rate, never jumps.* Local
  control + obstacle avoidance depend on this being unbroken. Fuse the **relative**
  sources here (deadwheel twist, IMU, visual odometry). Tool:
  `robot_localization` `ekf_node` (local instance).
- **`map → odom`** — *global, discrete, allowed to jump* (loop closure, GPS step).
  Provided by a global source: a SLAM map, or GPS via `navsat_transform` + a second
  (global) EKF.

**Why this answers "fallback when a channel drops":** as long as *any* relative
source is alive, `odom→base_link` stays continuous and the robot keeps moving
safely. If the global source drops, the `map→odom` correction just goes **stale**
(bounded drift) — not catastrophic. The deadwheel is the floor under the whole
stack whenever the pods are down.

## Where to fuse — and where not to

- **On the Thor, in ROS.** Autonomy brain, the compute, where all topics land.
- **Not on the StampLC** — IO/safety controller on an ASCII-serial bridge, wrong
  altitude, no FP muscle. Its only fusion-relevant job: relay the Pinpoint I²C pose
  out as a proper `nav_msgs/Odometry` topic.

## How sources come and go — the real engineering

`robot_localization` fuses asynchronous, intermittent inputs natively (predict on
IMU, correct as measurements arrive; a silent source simply stops correcting). The
work is **honest, dynamic covariance / validity gating** per source, so the filter
distrusts a source *before* it lies:

- **Deadwheel:** the StampLC commands the raise/lower servos → it *knows* when the
  pods are up. Publish that state; the odom node inflates covariance / mutes the
  topic when retracted or slip-detected. Same mechanism as the "retract on rough
  terrain / gap crossings" policy, now expressed as a fusion input (capability-backlog
  #1 + FSM).
- **GPS:** covariance from fix quality / HDOP (mirror of the outdoor deadwheel-retract
  policy).
- **LIDAR / VIO:** scan-match / tracking confidence → covariance.

Two rules: fuse wheel/deadwheel as **velocity (twist)** (or `_differential:true`)
so two pose sources don't fight over absolute position — reserve absolute fusion for
the global tier; and pick **one primary IMU** for the local filter (don't
double-count the same physical motion across two IMU sources).

## Transport caveat (the deadwheel is fusion-grade but on a slow path)

The Pinpoint pose IS a fusion input, but it currently transits the **StampLC ASCII
serial → dashboard 5 Hz poll** path, stamped at *publish* time, not sample time
(see hardening track 8) — too slow/jittery for clean odom fusion. Implications:

1. Republish Pinpoint as **`nav_msgs/Odometry` with covariance**, NOT a
   `DiagnosticArray` field.
2. Either accept it as a low-rate backup with inflated covariance, give it a faster
   dedicated StampLC→Thor link, or — endgame — **micro-ROS on the ESP32** publishing
   stamped `Odometry` *at source* (hardening tracks 3/4). Fusion-grade data wants
   stamp-at-source.

## Prerequisite: TF / extrinsics (REP-103/105)

None of this works without correct static transforms
`base_link → {laser, pinpoint pod, cameras}`. The LIDAR TF-tree TODO
(capability-backlog #5) is the first domino. Pinpoint reports at the pod location →
needs its `base_link` extrinsic + heading sign.

## Open decisions

- **Global-tier arbitration:** indoor LIDAR/visual SLAM vs outdoor GPS — one global
  EKF that fuses both, or a supervisor that switches which source owns `map→odom`
  (don't run two publishers of `map→odom` fighting).
- **Primary IMU** choice (Pinpoint vs camera).
- **Local fuser:** `robot_localization` (off-the-shelf, proven) vs a custom node —
  start with `robot_localization`.
- **Deadwheel slip detection** method (compare deadwheel twist vs IMU / other odom).
