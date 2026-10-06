> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this does NOT describe the current robot. RC is now StampPLC-SBUS (Pixhawk/MAVLink eliminated); power is a 52V LiFePO4 + JBD BMS + three-rail split (no DeWalt/shore). See [history/README.md](README.md) and [../system-overview.md](../../docs/system-overview.md).

# Hardening Roadmap

Prioritized tracks for making the existing OpenDubs stack robust enough for an
assembled, operating robot. This is about hardening *what exists* — new
capabilities live in [capability-backlog.md](../capability-backlog.md).

Status legend: ☐ todo · ◐ in progress · ☑ done

Last updated: 2026-06-10

---

## 1. Safety wiring & logic  ☐

The e-stop indication exists in software, but the safety *function* must be in
hardware (see [control-authority.md](../../docs/control-authority.md)).

- **NC, dual-pole e-stops in series.** Normally-closed so a press *or* a broken
  wire opens the loop (fail-safe). Multiple buttons in series → any one trips.
- **Pole 1 → hardwired contactor-coil loop.** Pressing any e-stop physically
  drops motor power regardless of whether the ESP32/dashboard is running. This
  is the authority.
- **Pole 2 → DI0 sense.** For software awareness only (state machine → LEDs red,
  command controllers to stop). NOT the safety interrupt.
- **Loop source:** the post-ideal-diode ~20–24 V bus, upstream of the motor
  contactor. In DI spec (StampLC DI = 5–36 V). Alive on battery *or* shore;
  reads tripped on total power loss; noise-immune over long runs. Tie DI COM to
  bus ground (high-level input config).
  - ⚠ Gated by the unresolved ideal-diode/shore-power design — see
    [capability-backlog.md](../capability-backlog.md) open questions.
- **Code changes (do at wiring time):**
  - `status.py ESTOP_BTN_ACTIVE_HIGH = False` (NC: pressed = LOW). Currently
    `True` for bench injection.
  - Treat **stale/missing DI telemetry as e-stopped** (fail-safe), not "clear".
- **Upgrade path:** dual-channel monitored loop + rated safety relay (PNOZ-class)
  if a higher safety category is ever required. Single-channel series-NC is
  acceptable for this robot's scale.

## 2. Telemetry refresh rates  ☑ (2026-06-08)

- Split `status.collect()` into `collect_fast()` (in-memory: safety, robot_state,
  io, encoder, power — pushed ~10 Hz) and `collect_slow()` (subprocess: net, vnc
  — ~1 Hz). MASTER STATUS card went 1 Hz → ~10 Hz.
- Firmware `[IO]`/`[ENC]` publish 250 ms → 100 ms (10 Hz).
- System metrics (tegrastats/nvidia-smi) remain ~1 Hz (expensive, no need faster).

## 3. Serial / comms robustness  ☐

Bandwidth is not the problem (a few lines at ≤10 Hz); **integrity, port-sharing,
and ROS-integration** are.

- **Pixhawk link** — protocol is fine (binary MAVLink: framed/checksummed). The
  issue is **exclusive port ownership**: the dashboard opens `/dev/ttyACM0`
  directly, so mavros/a GCS can't share. Target: a **MAVLink router or mavros
  owns the port**, everyone subscribes downstream.
- **StampLC link** — today ASCII text lines, regex-parsed, **no framing/CRC/seq/
  ACK**; a corrupted byte is silently dropped or mis-parsed. Target: **micro-ROS
  on the ESP32-S3** (see track 4) — replaces the text protocol AND the custom
  command endpoints with ROS topics/services. Decided (2026-06-08) to go
  straight to micro-ROS rather than build a throwaway binary protocol.
  - *Contingency only:* if reliability bites before ROS lands, add a per-line
    CRC + sequence number to the text protocol (keeps human-readability, ~½ day).

## 4. ROS 2 migration  ☐

The durable architecture; dissolves several problems above at once.

- **One driver node per port** (mavros / micro-ROS / serial bridge) publishes
  topics → kills the port-exclusivity problem.
- **Robot supervisor = a managed ROS node** (the FSM from
  [state-machines.md](../../docs/state-machines.md)) subscribing to RC/safety/subsystem
  topics, publishing `/robot_state` at a fixed rate with reliable QoS. Moves the
  state logic out of the dashboard backend so autonomy can consume it too.
- **Dashboard becomes a thin subscriber** via `rosbridge_suite` (websocket → the
  existing frontend pattern) or a small ROS→WS shim.
- **Safety stays OUT of ROS** — not real-time/safety-rated. Hardware remains the
  authority; the supervisor coordinates + indicates only.
- Refresh rates become per-topic **publish rate + QoS**, not hand-tuned push loops.

## 5. Thor utilization  ☐

The dashboard is deliberately Thor-agnostic. "Maximizing Thor" is an
autonomy-stack concern, addressed when perception/ROS stands up:

- **Isaac ROS + NITROS zero-copy** — keep camera frames in GPU memory across the
  perception graph (cuVSLAM, nvblox, TensorRT DNN) instead of serializing
  between nodes. This zero-copy graph *is* the Thor advantage.
- **Inference offload** — TensorRT + dedicated accelerators (DLA/PVA where
  exposed) for vision, reserving the main GPU for a VLA/foundation model.
- **NVENC** — hardware video encode for low-latency camera streaming / VR teleop.
- **nvpmodel power policy** — MAXN when autonomy is active, capped on battery
  (we read the mode but don't manage it).
- *(minor)* Chromium runs `--disable-gpu` (EGL init failed on this panel). Fine
  for a 2D kiosk; revisit if/when the GL stack is configured.

## 6. Deploy / ops robustness  ◐

- **Reproducible provisioning — DONE (2026-06-09).** Full host setup is now
  captured: `deploy-all.sh` → `provision-all.sh` chains kiosk + ROS + firmware
  provisioners; a fresh Thor is rebuildable from the repo. See
  [portability-audit.md](../../docs/portability-audit.md) for the inventory + runbook.
- **`rsync -a` strips the execute bit** if local scripts aren't +x → autostart
  silently fails on next boot (cost us a debugging session 2026-06-08). Fixed by
  keeping `deploy/*.sh` +x in-repo; consider `rsync --chmod=ug=rwx,Fa+r` or a
  `deploy.sh` that re-asserts perms.
- **Autostart resilience** — touch bridge now self-heals on USB drop; fullscreen
  watchdog re-asserts. Consider promoting the kiosk wrappers from GNOME-autostart
  `.desktop` entries to user systemd units (restart policy, logging, ordering).
- **Watchdogs** — backend health watchdog; serial-reader liveness; consider a
  hardware watchdog tickle once the supervisor exists.
- **Touch panel USB stability** — multiple `No such device` drops observed; a
  cable/hub/connector reseat is warranted (hardware, not software).

## 7. ROS graph hygiene — prune dead & misleading topics  ☐

MAVROS advertises ~100+ topics; most never publish on this airframe and a few
carry **meaningless-but-plausible** data (e.g. `/mavros/wind`, airspeed, baro
`altitude` on the ground, ESC telemetry with no DShot ESC). The burden:

- **Never-published topics** cost **DDS discovery + endpoint bookkeeping**, not
  throughput — memory per endpoint, a bigger discovery DB, slower startup
  convergence, O(participants × endpoints) matching across foxglove_bridge /
  rosbridge / watchdog / stamplc_bridge. Modest on one localhost Thor; **scales
  badly the moment a second host joins** (teleop laptop, second compute). Idle
  topics move ~0 bytes at steady state.
- **Foxglove** advertises the full list + parses each schema once, but only
  forwards data for topics a panel subscribes to → idle = picker clutter, not
  wire cost. **rosbridge** is subscribe-on-demand → idle ≈ free.
- **Meaningless-data topics** are the worse case: they serialize, cross loopback
  DDS at their stream rate, land in every MCAP, and — the real hazard — a
  plausibly-named topic carrying garbage is a **correctness/trust trap**, not
  just wasted cycles.

**Attempted 2026-06-11 — source-level `plugin_denylist` does NOT work on this
build (deferred).** Tried the textbook fix: a curated `plugin_denylist`
(`wind_estimation`, `adsb`, `terrain`, `esc_status`/`esc_telemetry`, `rangefinder`,
`gimbal_control`/`mount_control`, `mocap_pose_estimate`, `fake_gps`, `trajectory`,
`tunnel`, `gps_rtk`, …) fed via a custom launch (stock `apm.launch` hard-wires
`pluginlists_yaml` with `value=`, so it needs a mirror launch that exposes the arg).
Result: the **`/mavros/mavros` UAS node loaded the full 27-entry denylist as a param,
but the per-plugin component nodes still spawned — `/mavros` topic count unchanged,
136 → 136.** This Jazzy **mavros2** is a router+UAS+component-node architecture where
`plugin_denylist` on the UAS does not gate the component plugins the way classic
single-node mavros did. The default-denied entries (`altitude`, `vibration`,
`wheel_odometry`) read as absent only because ArduPilot-rover doesn't source those
messages — not because the denylist worked. **Reverted to stock `apm.launch`.**

**Reframe:** the *burden* of the dead topics is low (established above — discovery
bookkeeping, not throughput), so this is now a clutter/trust item, not a perf one.
Options when revisited:
- **Consumer-side filter (effective, no mavros internals):** foxglove_bridge
  `topic_whitelist` is an allowlist of regexes (no native denylist) — hide the
  misleading topics from the *console* with an exclusion regex. Doesn't reduce the
  DDS graph, but directly addresses the "/mavros/wind looks real but is garbage"
  trap, which is the actual concern.
- **Source-side (correct but needs work):** figure out the mavros2 component
  plugin-load gating (param precedence / which node actually constructs the
  components) — more than a quick change; only worth it if a second host makes
  discovery churn real.

## 8. Time synchronization across data streams  ☐

Fusion (TF, message_filters, SLAM, EKF) needs a **shared, low-jitter, monotonic
clock domain** — NOT an accurate wall clock. Everything on the Thor that stamps
with the Thor system/ROS clock (rplidar driver, `stamplc_bridge`) is **already in
one domain**; the intra-Thor problem is largely solved. Open items:

- **Cross-domain offsets.** *MAVROS/Pixhawk:* the FCU has its own clock; the
  `time` plugin runs TIMESYNC to stamp `/mavros/*` on the system clock — verify
  `timesync_rate`/`system_time_rate` and that we stamp with companion time, not
  raw FCU boot-ms. *Cameras (future ZED/RealSense):* frames carry the sensor HW
  clock → map to host time in the driver (librealsense `global_time_enabled`, ZED
  SDK equivalent). Driver-config jobs, not an RTC job.
- **Absolute wall time** (correlate bags across sessions, sane log stamps):
  discipline the Thor clock with **chrony** — NTP when networked **+ GPS/PPS from
  the Pixhawk** (gpsd+PPS, or feed `/mavros/time_reference` into chrony's SHM
  refclock). GPS time is the canonical reference; PPS gets sub-µs. **This is the
  high-value sync work.**

**StampLC RX8130 RTC — NOT a stream-sync source.** It's a calendar clock (~1 s
resolution, ±few ppm, drifts s/day) read over slow I²C *through the serial
bridge* (tens of ms latency/jitter) — stamping streams from it makes alignment
*worse*. Its battery-backed-boot-wall-clock job is already covered by the **Jetson
AGX's own onboard RTC** (confirm it's battery-backed). Canonical absolute source
is GPS via the Pixhawk. Legit RTC roles only: timestamp **StampLC-local events**
(ESP32 fault/safety logs when standalone), and a **last-resort boot wall-clock
fallback** (set Thor date at boot *only if* Thor RTC dead AND no network AND no
GPS) — RTC→Thor one-way, never in the streaming path.

> Note: `stamplc_bridge` diagnostics carry *publish*-time stamps (5 Hz poll +
> serial age, ~200 ms+ lag) — fine for dashboards, **not** fusion-grade. This
> matters because the StampLC **does** carry a fusion input: the **Pinpoint
> deadwheel odometry** transits its I²C. That pose needs stamp-at-source (a faster
> StampLC→Thor link or micro-ROS), not the ASCII-serial poll path — see
> [localization-fusion.md](../../docs/localization-fusion.md).
