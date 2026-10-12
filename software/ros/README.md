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
- **`opendubs-rplidar.service`** — systemd unit (User=emmar, Restart=always).

Publishes `sensor_msgs/LaserScan` on **`/scan`** (frame `laser`, ~7 Hz, 720
pts/rev, 0.5°, 0.15–12 m).

### Deploy

```bash
rsync -az ros/ thor:/opt/opendubs/ros/
ssh thor 'sudo cp /opt/opendubs/ros/opendubs-rplidar.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now opendubs-rplidar'
```

### Verify

```bash
ssh thor 'source /opt/ros/jazzy/setup.bash && timeout 4 ros2 topic hz /scan'
```

## rosbridge (dashboard ↔ ROS)

`ros-jazzy-rosbridge-suite` exposes a websocket on **`0.0.0.0:9090`** so the
dashboard frontend (browser JS) talks to ROS directly — the "thin ROS subscriber"
path. Runs as **`opendubs-rosbridge.service`** → `rosbridge.sh`.

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

## Drive bridge (/cmd_vel → DRIVE, [ODO] → /odom)

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
sudo systemctl restart opendubs-drive-bridge`. Nothing on the robot consumes `/odom` or publishes
`/cmd_vel` yet, so holding it costs nothing; the dashboard's odometry card reads `[ODO]` directly.

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
