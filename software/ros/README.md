# OpenDubs ROS 2 bringup

First ROS 2 nodes on the robot (the start of the ROS migration in
[../docs/history/hardening-roadmap.md](../docs/history/hardening-roadmap.md) track 4).

**Platform:** Jetson AGX Thor, Ubuntu 24.04 (noble), **ROS 2 Jazzy** (the noble
match; also what Isaac ROS on JetPack 7 uses). Installed via the ROS 2 apt repo:
`ros-jazzy-ros-base` + `ros-jazzy-rplidar-ros`. Source with
`source /opt/ros/jazzy/setup.bash`.

## RPLIDAR A1

Slamtec RPLIDAR A1 on USB (CP2102 bridge → `/dev/ttyUSB0`, stable by-id path in
`rplidar.sh`). Driver: the prebuilt `rplidar_ros` (`rplidar_composition`).

- **`rplidar.sh`** — launch wrapper (sources ROS, runs `rplidar.launch.py` on the
  by-id port). Env override: `OPENDUBS_LIDAR_DEV`.
- **`opendubs-rplidar.service`** — systemd unit (runs as the robot's service user, Restart=always).

Publishes `sensor_msgs/LaserScan` on **`/scan`** (frame `laser`, ~7 Hz, 720
pts/rev, 0.5°, 0.15–12 m).

### Deploy

`./deploy-all.sh <host>` ships this folder and runs `provision-ros.sh`, which installs every unit.
Don't copy a `.service` file into `/etc/systemd/system/` by hand: the units name their account as
`@OPENDUBS_USER@` / `@OPENDUBS_HOME@`, which `provision-ros.sh` fills in from the robot
(`OPENDUBS_SERVICE_USER` in `/etc/opendubs/robot.env`, or else the owner of `/opt/opendubs`). An
unrendered unit refuses to start.

```bash
ssh thor 'sudo /opt/opendubs/ros/provision-ros.sh'
```

### Verify

```bash
ssh thor 'source /opt/ros/jazzy/setup.bash && timeout 4 ros2 topic hz /scan'
```

## rosbridge (dashboard ↔ ROS)

`ros-jazzy-rosbridge-suite` runs a websocket on **`127.0.0.1:9090` — the Thor only**. Runs as
**`opendubs-rosbridge.service`** → `rosbridge.sh`.

**To reach ROS from the LAN — the tablet, a laptop, a student's tool, a headset — use the
dashboard's `/rosbridge`:**

| from | URL |
|---|---|
| a plain-HTTP page or a script | `ws://<robot>:8080/rosbridge` |
| an HTTPS page (the tablet) | `wss://<robot>:8443/rosbridge` |

It speaks the ordinary rosbridge protocol, relayed unchanged, so a rosbridge client (roslibjs,
roslibpy, ros-sharp) only needs its URL changed. rosbridge itself has no authentication and gives
full access to the ROS graph, so the raw port is not on the LAN. Instead, the dashboard refuses a
connection opened by a web page from another site, which is the dashboard's Origin check. Tools and
scripts that send no `Origin` are let through: the robot's network is the first protection, not a
login. On the Thor itself, `ws://127.0.0.1:9090` still works. Foxglove has its own bridge on `:8765`
and is unaffected.

`OPENDUBS_ROSBRIDGE_ADDRESS` overrides the bind address, for an isolated bench network only, as a
per-robot drop-in.

- The dashboard **LIDAR tab** has a minimal vanilla-JS rosbridge client (no
  roslibjs) that subscribes `/scan` and renders the **front** scan live (left
  panel), with a **mock rear** (right panel). **START/STOP SPIN** buttons call the
  `/start_motor` · `/stop_motor` services (`std_srvs/srv/Empty`).
- Out-of-range `/scan` points arrive as JSON `null` (not `Infinity`), so parsing
  is safe; the renderer skips null/0/inf.

## Foxglove bridge (engineering observability)

`ros-jazzy-foxglove-bridge` exposes the ROS graph on **`0.0.0.0:8765`** for the
**Foxglove app** (desktop/web, run on a **dev laptop — not the Thor**). Runs as
**`opendubs-foxglove.service`** → `foxglove.sh`. This is the engineering/debug
console (3D, TF, point clouds, plots, diagnostics, MCAP recording) — it
*complements* the kiosk operator HMI, doesn't replace it.

- Connect the Foxglove app to **`ws://opendubs2.local:8765`** (Foxglove WebSocket).
  - Verified reachable: mDNS → the Thor's LAN IP, TCP open, WS upgrade returns 101.
  - **Bridge 3.x requires subprotocol `foxglove.sdk.v1`** (NOT the legacy
    `foxglove.websocket.v1`). Use a current Foxglove app (2.0+); an old *Foxglove
    Studio* build will 400. Relevant only if writing a custom client.
- Keep on the onboard network / behind the router — it exposes the full graph.
  (Foxglove-over-Tailscale is the documented remote pattern.)

> An optional Pixhawk/MAVROS variant (not part of the baseline) is preserved under
> [`../../optional/pixhawk/`](../../optional/pixhawk/).

## Topic and service naming

**Named by function, not by device.** `/robot/`, `/power/`, `/sensors/`, `/zaxis/` — what the data
*is*, not which board sent it. A topic then survives a function moving between controllers, which
has already happened once when RC moved off the Pixhawk onto the StamPLC's SBUS. `/diagnostics`
keeps its standard ROS name so aggregators find it.

**Nothing writes the lift's relays.** `/zaxis/{up,down,stop}` goes through `MAST CMD` and the same
driver the RC sticks feed, so the pinch guard, the limit detection and the authority/deadman gate
all apply. The backend refuses direct relay writes on channels 1 and 2 for the same reason.

## StamPLC → ROS bridge

The kiosk dashboard is the **single owner** of the StamPLC serial link. The bridge
gets StamPLC data into ROS/Foxglove **without a serial conflict**: it polls the
dashboard's lightweight **`/api/stamplc`** endpoint (io/safety/encoder/PDH) and
republishes it, and advertises `std_srvs` services that **POST back** to the
dashboard's existing command endpoints (which keep enforcing the safety policy —
only relays 2/3 are exposed; relay 0/1 are not). Runs as
**`opendubs-stamplc-bridge.service`** → `stamplc-bridge.sh` → `stamplc_bridge.py`.

- **Topics:** `/diagnostics` (`DiagnosticArray`: Safety / Digital Inputs /
  Relays / PDH / Encoder), plus `/robot/{estopped,motion_enabled}`,
  `/power/{bus_voltage,temperature}`, `/power/pdh/{voltage,current,power}` and
  `/zaxis/encoder`.
- **Services:** `/robot/estop` · `/robot/estop_clear` · `/robot/i2c_scan` ·
  `/zaxis/{up,down,stop}` · `/zaxis/encoder_reset` (`Trigger`); `/robot/aux_relay` ·
  `/power/pdh_switch` (`SetBool`). Each POSTs the matching `/api/*` endpoint.
- Poll rate `OPENDUBS_STAMPLC_HZ` (default 5 Hz), dashboard URL `OPENDUBS_DASH_URL`.

Interim bridge — the long-term clean path is **micro-ROS on the ESP32-S3** (see
`../docs/history/hardening-roadmap.md`); this needs no firmware change and no dashboard rework.

Verify: `ros2 topic echo /diagnostics --once` · `ros2 service call
/robot/i2c_scan std_srvs/srv/Trigger`.

## Drive bridge (base commands → DRIVE, [ODO] → /odom)

`drive_bridge.py` (`opendubs-drive-bridge.service`) speaks the StamPLC's UDP 5005 directly. It
sends motion commands, so it is strict about **who the StamPLC is** (`plc_peer.py`, tests in
`tests/`):

- never one of the Thor's own addresses, and only from packets that look like telemetry (a
  `[TAG]` line). These are the same two checks the dashboard makes;
- a change of peer is logged (`StamPLC peer MOVED to …`), not silent;
- optionally **pinned per robot**, after which nothing else is accepted:

```sh
sudo mkdir -p /etc/systemd/system/opendubs-drive-bridge.service.d
printf '[Service]\nEnvironment="OPENDUBS_STAMP_IP=<the StamPLC address>"\n' |
  sudo tee /etc/systemd/system/opendubs-drive-bridge.service.d/stamp-ip.conf
sudo systemctl daemon-reload && sudo systemctl restart opendubs-drive-bridge
```

The pin is per-robot identity: it lives in the drop-in on the robot, never in the repo.

### Holding the bridge off on a robot

Until a robot's Pinpoint is commissioned (`PPCAL SHOW` reports it, see
[the Pinpoint build page](../../docs/build/pinpoint.md)), its `/odom` is not trustworthy, so the bridge
can be held off. **Do not `systemctl mask` it:** the unit file lives in `/etc/systemd/system/`, so the
mask is refused. Even if it worked, `provision-ros.sh` re-installs the unit and its `systemctl enable`
would fail under `set -e`, which aborts the rest of provisioning. Use a start condition instead, which
survives every deploy:

```sh
sudo mkdir -p /etc/systemd/system/opendubs-drive-bridge.service.d
printf '[Unit]\nConditionPathExists=/etc/opendubs/drive-bridge.enabled\n' |
  sudo tee /etc/systemd/system/opendubs-drive-bridge.service.d/hold.conf
sudo systemctl daemon-reload
```

`systemctl status opendubs-drive-bridge` then reads *start condition unmet*, and provisioning still
enables and "restarts" it cleanly. To turn it on: `sudo touch /etc/opendubs/drive-bridge.enabled &&
sudo systemctl restart opendubs-drive-bridge`. The dashboard's odometry card reads `[ODO]`
directly, so holding the bridge off costs nothing but `/odom` and ROS motion.

`OPENDUBS_ODOM_TF=false` in a drop-in stops the bridge publishing TF `odom → base_link` (for a
stack that fuses its own); `/odom` keeps publishing.

## Base ownership (who drives in AUTO)

The base has one software owner at a time. `base_selector.py` (`opendubs-base-selector.service`)
holds the lease: an owner claims on `/base/claim`, renews within its `ttl_s` (at most 5 s), sends
`Twist` on `/base/<owner>/cmd_vel`, and releases on `/base/release`; `/base/owner` says who holds it.
Only the holder's commands are forwarded, to `/opendubs/drive/cmd_vel`, and `drive_bridge` refuses all
motion if anything but the selector publishes there. A claim never takes the base from a live holder;
a dead one frees it when its lease runs out, and `{"owner": "*"}` on `/base/release` is the operator's
release. The dance tracker (`dance_tracker.py`) is the owner `show`.

Before any command reaches the StamPLC, `drive_bridge`'s people guard (`people_guard.py`) refuses motion
toward anything within 0.5 m of the robot's outline, from both LIDARs, and refuses everything when a
scan is stale. The firmware gates (AUTO, a held deadman, the 0.35 ceiling) still apply on top.

**The software speed cap** (`drive_scale.py`): AUTO motion is limited to `OPENDUBS_AUTO_CAP` of full
command (0.25 to start) on every **wheel**, so no mix of forward, strafe and turn exceeds it. It's per
robot, in `/etc/opendubs/base.env`, which both `opendubs-drive-bridge` and `opendubs-dance-tracker` read,
together with the speeds at the cap (`OPENDUBS_MAX_LIN`, `_STRAFE`, `_ANG`) and the optional strafe trim
(`OPENDUBS_STRAFE_YAW_BLEED_LEFT`/`_RIGHT` and `OPENDUBS_STRAFE_FWD_BLEED_LEFT`/`_RIGHT`, per direction,
because a strafe can bleed differently each way). Changing any of them is a one-line edit and
`sudo systemctl restart opendubs-drive-bridge opendubs-dance-tracker`. MANUAL (RC) driving isn't affected.

The public contract for students and other stacks is the docs page
[Navigation interface](../../docs/interfaces/navigation.md); keep the two in step.

## /scan watchdog

The A1 can hit a serial `RESULT_OPERATION_TIMEOUT` (USB glitch / unplug) and get
**stuck publishing nothing while the driver process stays alive**, so `Restart=always`
never fires. **`opendubs-scan-watchdog.service`** (`scan-watchdog.sh` → `scan_watchdog.py`)
subscribes to `/scan` and runs `systemctl restart opendubs-rplidar` if it goes stale
> 5 s (then mutes 20 s). Restart privilege via `/etc/sudoers.d/opendubs-ros`.

**It also OWNS the motor enable** via a `/lidar/enable` (`std_srvs/SetBool`) service, so
it knows the *desired* state and won't "rescue" an intentional STOP back to spinning —
the dashboard's START/STOP SPIN call `/lidar/enable` (not the driver's `/start_motor`//
`/stop_motor`). `enable=false` → `/stop_motor` + watchdog stands down (no restart while
disabled). `enable=true` → **driver restart** (because `/start_motor` doesn't reliably
resume scanning after `/stop_motor` on this driver), so scan returns in ~5–8 s. Validated:
STOP stays stopped (driver not restarted), START brings scan back; a genuine wedge while
enabled still triggers a restart.

## Tablet camera demand

The head tablet's cameras reach ROS through the dashboard (`backend/tablet_relay.py`) as
`/tablet/camera/{front,rear}/image_raw/compressed`, and the tablet streams a camera only while that
topic has a subscriber. **`opendubs-tablet-camera-demand.service`** (`tablet-camera-demand.sh` →
`tablet_camera_demand.py`) counts the subscribers once a second with rclpy's `count_subscribers()`,
which sees every subscription, command-line tools included. It POSTs
`{"front": bool, "rear": bool, "counts": {...}}` to the dashboard's `/api/tablet/camera/demand`
whenever either answer changes, and every 2 s otherwise. The dashboard treats demand it has not heard
for a few seconds as false, so a camera never keeps streaming because this node stopped.

To make the rear camera stream from a shell, subscribe to it:
`ros2 topic hz /tablet/camera/rear/image_raw/compressed`.

## Skeleton feed relay

**`opendubs-skeleton-relay.service`** (`skeleton-relay.sh` → `skeleton_relay.py`) carries the live
skeleton feed from the Femto Bolt tracker (issue #450) to the stage page. It subscribes to the
tracker's topic, turns each message into the stage page's frame with the choreography lane's adapter
(`software/choreo/skeleton_feed.py`, deployed to `/opt/opendubs/choreo`), and sends it as JSON to the
dashboard's localhost socket `/ws/skeleton/in`, which fans it out to the page.

- **Only skeleton points pass.** The relay subscribes to exactly the adapter's `MSG_TYPE` and refuses
  to start if that is an image, depth or point-cloud type. Nothing is recorded.
- **Newest frame only.** A slow socket drops older frames rather than queueing them. An empty frame
  goes out after 1 s with no message, marked `"status": "stale"`, so the page can tell "nobody there"
  from a stopped feed.
- While the adapter's `MSG_TYPE` is `None` (the tracker's type not known yet) it idles and says so once.
- It idles until the adapter is deployed (`ConditionPathExists`), runs on UDP-only DDS, and reconnects
  across dashboard restarts.

## SLAM and the building floor plan

`opendubs-robot-state-publisher` publishes the robot's frames from `description/opendubs/opendubs.urdf`
(in the `/opendubs` namespace, so it never touches the arms' `/robot_description`). `opendubs-slam` runs
slam_toolbox on the front scan, after `scan_body_filter.py` drops the robot's own returns. It is off
unless `/etc/opendubs/slam.enabled` exists.

Everything site-specific lives on the robot in `/var/lib/opendubs/maps/`, never in the repo:

| file | written by | holds |
|---|---|---|
| `<name>.posegraph`, `<name>.data` | slam_toolbox's `serialize_map` service | a saved map, for localization |
| `<name>.pgm`, `<name>.yaml` | a map_server-format save of `/map` | the same map as an image, for the fit |
| `<floor>.plan.yaml` | `floorplan.py fit` | the floor frame's rooms: `{label: [x, y]}` in metres |
| `site.yaml` | `floorplan.py fit` | which map to localize on, its start pose, and `map_in_floor` |

With no `site.yaml` (or one naming a map that is not there), SLAM **maps**: a fresh map from wherever
the robot starts. With one, SLAM **localizes** on the named map from its `start_pose`, which is where
the mapping drive began, and `opendubs-floor-frames` publishes `<floor> -> map` and the room labels on
`/floor/rooms`. A robot that boots elsewhere is corrected with `/initialpose`.

To fit a map to a floor plan (a vector PDF with a scale bar), on a laptop:

```bash
uv run --with pymupdf --with numpy --with scipy --with pyyaml python3 software/ros/floorplan.py fit \
    PLAN.pdf MAP.yaml --frame bldg_f2 --seed-room 204 --start X Y YAW -o OUT_DIR
```

Check `OUT_DIR/<map>.overlay.png` (the plan in grey, the map's walls in red), then copy
`<frame>.plan.yaml` and `site.yaml` to the robot's `/var/lib/opendubs/maps/` and restart
`opendubs-slam` and `opendubs-floor-frames`.

### Mapping a floor, attended

`floor-mapping.sh` (→ `floor_mapping.py`) maps one floor while a person drives the robot by RC. Run it
on the robot, in a terminal you can see while driving:

```bash
/opt/opendubs/ros/floor-mapping.sh check            # disk, live topics, SLAM's mode, a 6 s trial recording
/opt/opendubs/ros/floor-mapping.sh floor bldg_f1    # robot still at the start; Ctrl-C ONCE when done
```

`floor` restarts `opendubs-slam` so the floor starts from an empty map, records the drive as a ros2 bag
in `~/bags/<map>` (both LIDARs, `/odom` and the raw `[ODO]` line on `/odom/raw`, TF, the neck's joint
states, the rear body filter and the depth scan when they run, and the depth camera at 15 Hz if its
driver is running, compressed here when it publishes only raw images), and on
Ctrl-C saves the map as `bldg_f1-<date>`: the pose graph, a `.pgm`/`.yaml`, and an inactive
`site.<map>.yaml` holding the start pose that `floorplan.py fit --start` needs. It refuses to start
while `site.yaml` exists (SLAM would localize, not map) or with too little disk, and stops the
recording itself before the disk gets low. `check`'s trial measures the GB an hour (about 30 with the
camera) and the camera rate it actually achieved, and warns below 12 Hz. `floor-mapping.sh save bldg_f1` saves the current map alone.
At the end it writes `capture.yaml` in the bag's directory (the `opendubs.capture/1` manifest: building
and floor, times, robot, deployed code, operator, start landmark, sensors, and every map and bag file by
sha256) and copies the map's small files beside it. It asks for the operator and the start landmark
unless `--operator` and `--start` give them, and needs the building's FMS number in
`/var/lib/opendubs/maps/buildings.yaml` (`bldg: "1234"`); it checks all that before restarting SLAM.
Bags and maps are site data: they stay on the robot and the project's machines, never in the repo. The
camera's colour frames are pictures of whoever is in the corridor; `--no-color` leaves them out.

When someone else drives and the person running the tools is on a laptop, the same steps run as
systemd units, so a dropped ssh session doesn't stop them (all in this directory, run on the robot):

| script | does |
|---|---|
| `pinpoint-reset.sh` | `PPCAL RESET` with the robot still, then 30 s of `[ODO]` and the heading drift. Before each floor |
| `floor-start.sh FRAME "START" [ROOM]` | `floor-mapping.sh floor` as the unit `opendubs-floor-map`, plus a text log of the StamPLC's `[ODO]`, `[DRIVE]`, `[SAFE]`, `[8SERVO]`, `[BMS]`, `[PDH]`, `[WDT]` and `[SYS]` lines beside the bag |
| `floor-stop.sh` | one SIGINT to the unit, which saves the map and `capture.yaml`; waits for the save |
| `exterior-start.sh NAME` / `exterior-stop.sh` | `floor-mapping.sh segment`: a bag-only segment (outdoors, ramps) with the same streams as a floor (colour as JPEG) plus the tablet's GPS and IMU. SLAM is left as it is |
| `mark.sh "label"` | one line in the current capture's `marks.txt`: time, label, `/odom` and SLAM pose, the head's joints |
| `plc_listen.py` | prints the StamPLC's UDP 5005 lines by tag, optionally after one host command; root (raw socket) |

`OPENDUBS_COLOR_FROM_RAW=1` (set by `floor-start.sh`) records colour from the raw image when the
driver's compressed colour stream is listed but silent, as the Femto's was on 2026-11-11.

## Notes / next

- DDS is default (localhost, `ROS_DOMAIN_ID=0`, FastDDS). When more nodes/hosts
  join, standardize the domain ID + RMW and document here.
- The tab renders each LIDAR as a **half-disc** (outward 180°; the robot body
  occludes the inward half) flanking a center robot/controls column. Orientation
  is a first guess — `FRONT_FACING`/`REAR_FACING` + the `cos(sensorAngle) > 0`
  crop in app.js; calibrate (flip facing by π, or invert the crop) once mounted.
  **Do not calibrate those facings against pre-2026-10-02 behaviour** — until then
  the front service was driving the rear unit, so anything tuned to look right was
  compensating for a swap.
- Next perception steps: TF tree (`base_link`→`laser`, `base_link`→`laser_rear`),
  then SLAM/Nav (cuVSLAM/nvblox). Both LIDARs now publish; the design is in
  `notes/design/lidar-frames-and-fusion.md`.

## Pinning each LIDAR to a physical unit (per robot)

**Both A1 adapters report CP2102 serial `0001`.** So `/dev/serial/by-id` holds a single ambiguous
entry, only one unit wins it, and `rplidar.sh`'s defaults (`/dev/ttyUSB0`, topic `scan`, frame
`laser`) resolve to whichever unit enumerated first. Two unpinned drivers fight for one device and
publish over one topic.

Each unit is therefore pinned by **USB port path** in a systemd drop-in. This is **per robot** — it
depends on physical hub wiring — so it lives on the machine and never in this repo, and
`provision-ros.sh` installs and enables the drivers but **refuses to start** one that has no
`OPENDUBS_LIDAR_DEV`, printing the recipe instead.

```sh
ls -l /dev/serial/by-path/        # one entry per connected A1
```

**Identify which physical unit is which before writing anything.** Unplug one, or hold a panel in
front of one and watch which topic changes — counting beams in a 0.3–0.8 m band discriminates
cleanly, where minimum range does not, because the robot's own structure already sits ~0.10 m from
both heads. Do **not** infer it from plug order.

```sh
sudo mkdir -p /etc/systemd/system/opendubs-rplidar.service.d
sudo tee /etc/systemd/system/opendubs-rplidar.service.d/device.conf <<'EOF'
[Service]
Environment="OPENDUBS_LIDAR_DEV=/dev/serial/by-path/<front unit's path>"
Environment="OPENDUBS_LIDAR_TOPIC=scan"
Environment="OPENDUBS_LIDAR_FRAME=laser"
EOF
sudo systemctl daemon-reload && sudo systemctl restart opendubs-rplidar
```

The rear unit takes `scan_rear` / `laser_rear` in `opendubs-rplidar-rear.service.d/`. The rear
driver also carries a 15 s `ExecStartPre` stagger, because the two A1s cannot both handshake at once
on the shared hub.
