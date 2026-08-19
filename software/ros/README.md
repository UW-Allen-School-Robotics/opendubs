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

## StampLC → ROS bridge

The kiosk dashboard is the **single owner** of the StampLC serial link. The bridge
gets StampLC data into ROS/Foxglove **without a serial conflict**: it polls the
dashboard's lightweight **`/api/stamplc`** endpoint (io/safety/encoder/PDH) and
republishes it, and advertises `std_srvs` services that **POST back** to the
dashboard's existing command endpoints (which keep enforcing the safety policy —
only relays 2/3 are exposed; relay 0/1 are not). Runs as
**`opendubs-stamplc-bridge.service`** → `stamplc-bridge.sh` → `stamplc_bridge.py`.

- **Topics:** `/stamplc/diagnostics` (`DiagnosticArray`: Safety / Digital Inputs /
  Relays / REV PDH / Mast Encoder, labeled), plus plain plottables
  `/stamplc/{bus_voltage,temperature,mast_encoder,estopped,motion_enabled}` and
  `/stamplc/pdh/{voltage,current,power}`.
- **Services:** `/stamplc/estop` · `/stamplc/estop_clear` · `/stamplc/enc_reset` ·
  `/stamplc/i2c_scan` (`Trigger`); `/stamplc/relay2` · `/stamplc/relay3` ·
  `/stamplc/pdh_switch` (`SetBool`). Each POSTs the matching `/api/*` endpoint.
- Poll rate `OPENDUBS_STAMPLC_HZ` (default 5 Hz), dashboard URL `OPENDUBS_DASH_URL`.

Interim bridge — the long-term clean path is **micro-ROS on the ESP32-S3** (see
`../docs/history/hardening-roadmap.md`); this needs no firmware change and no dashboard rework.

Verify: `ros2 topic echo /stamplc/diagnostics --once` · `ros2 service call
/stamplc/i2c_scan std_srvs/srv/Trigger`.

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
- Next perception steps: TF tree (`base_link`→`laser`) + real second (rear)
  LIDAR, SLAM/Nav (cuVSLAM/nvblox).
