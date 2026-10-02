# LIDAR frames and how the stack consumes them

Goal 6. The robot carries **two** Slamtec RPLIDAR A1s, front and rear
([Lidar build](../../docs/build/lidar.md)). One of the two is running today.

## The drivers are fine; the frames are the gap

Everything on the driver side already exists and is thought through:

| | |
|---|---|
| `rplidar.sh` | one env-driven script for both units — `OPENDUBS_LIDAR_DEV/TOPIC/FRAME`, because the stock launch file hardcodes `/dev/ttyUSB0` and cannot drive two |
| device pinning | **by USB port path**, not by-id: both CP2102 adapters report serial `0001`, so by-id is ambiguous |
| `opendubs-rplidar.service` | front → `/scan`, frame `laser` |
| `opendubs-rplidar-rear.service` | rear → `/scan_rear`, frame `laser_rear`, `After=` the front plus a 15 s `ExecStartPre` sleep, because the two A1s cannot both handshake at once on the marginal USB hub |
| `scan_watchdog.py` | a watchdog per unit |

**What does not exist is any transform between those frames and the robot.** Nothing publishes
`base_link → laser` or `base_link → laser_rear`. `software/ros/README.md:142` lists it as the next
perception step, and `docs/localization-fusion.md:86` carries the same TODO.

Without it: the two scans cannot be combined, neither can be used with `/odom`, and no standard
tool can place either on the robot. The scans are published into a vacuum.

## The dashboard is a second answer to "where is the LIDAR"

Because the TF does not exist, the dashboard draws the scans with geometry hardcoded in
JavaScript — `FRONT_FACING`/`REAR_FACING` and a `cos(sensorAngle) > 0` crop in `app.js`, rendering
each unit as a half-disc on the assumption that the robot body occludes the inward half. The ROS
README says outright that the orientation is **"a first guess"** needing calibration once mounted.

That is the same failure shape as a second arbiter: two places that answer where a sensor is, so
they can disagree, and the one a human looks at is the one with the guess in it. The extrinsics
want **one** source — measured once, published as TF, and consumed by the dashboard rather than
re-asserted there.

## Do not merge the two scans

The reflex is a scan merger (`ira_laser_tools` and friends) producing one synthetic `/scan`.
**Default to not doing that.**

- Nav2 costmaps take **multiple `observation_sources`** natively. Two scans at two correct frames
  is strictly more information than one merged scan at one frame.
- Merging fabricates a single origin for returns that had two. Where the robot body occludes each
  unit, the merged product has to invent something, and the artifacts land exactly in the near
  field that matters for collision.

Merge **only** where an algorithm demands exactly one scan — 2D SLAM (`slam_toolbox`) is the real
case. Then the merged topic is an input to that one consumer, not the robot's scan topic.

So: keep `/scan` and `/scan_rear` as they are, give them correct frames, and let each consumer take
both.

## Order of work

1. **Measure the extrinsics** — each unit's x/y/z and yaw relative to `base_link`. The rear is
   presumably yawed π, but that is an assumption and assumptions are what put a guess in `app.js`.
   Needs the robot or the CAD.
2. **Publish them**, from a URDF via `robot_state_publisher` rather than loose
   `static_transform_publisher` calls. One file holds every sensor's mounting, and Foxglove and
   RViz get a robot model from the same source. `base_link → {laser, laser_rear, pinpoint pods,
   cameras}` all belong there, which closes the broader TF TODO in
   [localization fusion](../../docs/localization-fusion.md), not just the LIDAR half.
3. **Point the dashboard at the published extrinsics** and delete the hardcoded facings.
4. **Get the rear unit running.** The staggered start exists because of the marginal hub; whether
   that is still the cause is a robot-side diagnostic.
5. **Then** costmaps, with both scans as separate observation sources.

Steps 1 and 4 need the robot. Steps 2, 3 and 5 are desk work once 1 is measured — but 2 cannot be
written honestly before 1, because a URDF with guessed offsets is the `app.js` problem with more
ceremony.

## Open

- **`base_link`'s own placement.** [Frames & naming](../../docs/naming-frames.md) flags the
  CAD datum (rear-centre hole) against where ROS wants `base_link`. Every extrinsic measured here
  depends on that answer, so settle it before measuring, not after.
- **Which unit is authoritative where they overlap.** Probably moot once both are costmap sources,
  but it is a real question for any scan-matching consumer.
