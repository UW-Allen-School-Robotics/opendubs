> ⚠️ **ARCHIVED / SUPERSEDED (2026-08).** Kept for decision history — this does NOT describe the current robot. RC is now StampPLC-SBUS (Pixhawk/MAVLink eliminated); power is a 52V LiFePO4 + JBD BMS + three-rail split (no DeWalt/shore). See [history/README.md](README.md) and [../system-overview.md](../system-overview.md).

# Telemetry Specification Plan

## Related Architecture Documentation

This telemetry plan is intentionally paired with the top-level robot architecture and safety docs:

- [OpenDubs Robot Documentation Hub](../../docs/README.md)
- [System Overview](../../docs/system-overview.md)
- [Control Authority and Modes](../../docs/control-authority.md)
- [Robot and Subsystem State Machines](../../docs/state-machines.md)
- [PWM and ESC Safety](../../docs/pwm-esc-safety.md)
- [REV PDH CAN Feasibility](../../docs/rev-pdh-can-feasibility.md)

## Purpose

Define a stable telemetry interface for the controller firmware so downstream systems (ROS or non-ROS) can consume consistent topic-grouped data.

## Design Rules

1. Use ROS topic concepts as the canonical data model, even when transport is serial.
2. Keep transport-independent field names, units, and semantics.
3. Publish both raw and interpreted RC telemetry.
4. Include explicit timestamps and source metadata in every message.

## Topic Namespace Convention

Proposed root namespace:

- `/telemetry/rc/*`
- `/telemetry/stamplc/*`
- `/status/*`

Message metadata fields to include in all topics:

- `stamp_ms` (uint64): controller monotonic timestamp in milliseconds
- `seq` (uint32): message sequence number per topic
- `source` (string): producer identifier, for example `stamplc-main`

## RC Receiver Telemetry

### Channel Mapping (Current Assignment)

| Channel | Meaning | Control Type | Notes |
|---|---|---|---|
| CH1 | Rotate L/R | Analog | Yaw command |
| CH2 | Mast raise/lower command | Analog | Active only when CH5 is RC mode and CH7 enables mast RC authority |
| CH3 | FWD/RVS | Analog | Longitudinal command |
| CH4 | Strafe L/R | Analog | Lateral command |
| CH5 | Control authority mode | 3-state discrete | OFF / RC manual / autonomous |
| CH6 | Deadman Switch | Binary | Safety-critical enable |
| CH7 | Mast RC authority | 3-state discrete | Disable / enable-mast-RC / reserved |
| CH8 | Speed control | Analog | Global speed scaling |

### RC Control Authority and Startup Gate

Control authority states (from CH5):

- `LOW` -> `OFF`
- `MID` -> `RC_MANUAL`
- `HIGH` -> `AUTONOMOUS`

Mast RC authority (from CH7):

- `LOW` -> mast RC disabled
- `MID` -> mast RC enabled
- `HIGH` -> reserved (default behavior: mast RC disabled until assigned)

CH2 mast behavior:

- CH2 is interpreted as mast raise/lower only when `CH5 == RC_MANUAL` and `CH7 == MID`.
- In all other combinations, mast command is forced to neutral/hold output.

Startup safety gate:

- On boot, the robot starts in `STARTUP_LOCKED`.
- The system must observe CH5 in `OFF` for a continuous hold time (recommended 1.0 s) before accepting any RC motion commands.
- RC link loss, failsafe, or frame timeout forces a return to `OFF` behavior and requires an OFF re-latch.

### Topics

### Control Intent to Mecanum Mixer Mapping

The mecanum mixer uses three PWM control channels. In this document, those are mixer channel indices, not RC receiver channel numbers.

| Mecanum Mixer Channel | Function | Source from `/telemetry/rc/normalized` |
|---|---|---|
| CH1 | Rotate | `rotate` |
| CH2 | FWD/RVS | `forward` |
| CH3 | Strafe L/R | `strafe` |

Current RC source mapping remains:

- `rotate` from receiver CH1
- `forward` from receiver CH3
- `strafe` from receiver CH4

#### `/telemetry/rc/raw`

Raw RC channel values from SBUS decode.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `protocol` (string): currently `SBUS`
- `channels[8]` (uint16): raw values, expected range approximately 200..1800
- `failsafe` (bool)
- `frame_lost` (bool)
- `valid_frame_count` (uint32)
- `invalid_frame_count` (uint32)

Rate target:

- Publish at 50 Hz (every 20 ms) or on each valid frame if frame rate is lower.

#### `/telemetry/rc/normalized`

Normalized axis/switch representation for control stack consumers.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `rotate` (float32): CH1 mapped to -1.0..1.0
- `forward` (float32): CH3 mapped to -1.0..1.0
- `strafe` (float32): CH4 mapped to -1.0..1.0
- `speed_scale` (float32): CH8 mapped to 0.0..1.0
- `deadman` (bool): CH6 interpreted with configurable threshold
- `ch5_mode` (int8): {-1, 0, 1} mapped to {OFF, RC_MANUAL, AUTONOMOUS}
- `ch7_mode` (int8): {-1, 0, 1} mapped to {mast_rc_disabled, mast_rc_enabled, reserved}
- `mast_cmd` (float32): CH2 normalized -1.0..1.0 after mode gating; 0.0 when not authorized
- `rc_startup_latched` (bool): true once OFF startup gate has been satisfied

Rate target:

- 20 to 50 Hz, synchronized with `/telemetry/rc/raw`.

#### `/status/safety`

Safety-relevant state for supervisors.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `deadman` (bool)
- `rc_link_ok` (bool): true when fresh valid frames are received
- `failsafe` (bool)
- `frame_lost` (bool)
- `mast_limit_lower` (bool): lower/home limit switch asserted
- `mast_limit_upper` (bool): upper travel limit switch asserted
- `mast_homed` (bool): true after successful homing/reset sequence

Rate target:

- 10 Hz periodic plus immediate publish on state changes.

## StampPLC Telemetry

## Power Architecture and Procedure

Power source architecture:

- Two physical power switches feed two goBILDA Smart Docks (DeWalt 20V MAX compatible):
  - <https://www.servocity.com/gobilda-smart-dock-for-20v-battery-dewalt-20v-max-compatible/>
- Smart Dock outputs are combined via an automatic transfer switch.
- The transfer switch sources from the higher-voltage battery, enabling hot-swapping.
- Transfer switch output powers the REV PDM.
- REV PDM powers StampPLC and RC receiver.

Power-on sequence requirements:

1. Operator turns on one or both battery-side power switches.
2. Controller boots into `STARTUP_LOCKED` with outputs safe.
3. RC startup OFF latch must complete before RC commands are accepted.
4. System transitions to `READY_IDLE` after startup checks pass.

Power-off sequence requirements:

1. Set CH5 to `OFF` and verify motion commands are neutralized.
2. De-energize motion outputs in a deterministic order (base, mast, arms).
3. Persist fault/status telemetry snapshot.
4. Remove battery-side power via switches.

Shore-power option (planned):

- Add `shore_power_present` state input.
- When `shore_power_present == true`, default policy disables drive motion unless explicitly in service mode.
- Publish charging/power-source status in telemetry for operator visibility.

## Universal Robot and Subsystem State Colors

Use a single color semantic map for global status and each subsystem LED array (base, mast, arms). Subsystem indicators may show local state while a global indicator shows highest-priority state.

Recommended palette and patterns:

- `BOOT_INIT`: white pulse
- `SAFE_OFF`: blue solid
- `READY_IDLE`: green breathe
- `RC_MANUAL_ACTIVE`: green fast pulse
- `AUTONOMOUS_ACTIVE`: cyan fast pulse
- `PAUSED_HOLD`: yellow solid
- `WARNING_DEGRADED`: amber slow blink
- `FAULT_ESTOP`: red fast blink
- `COMMS_LOSS`: red/amber alternating
- `SHORE_POWER`: violet solid
- `CHARGING`: violet breathe

Priority rule:

- Highest severity wins (`FAULT_ESTOP` > `WARNING_DEGRADED` > active modes > idle/off states).

### Required Signals

- RTC time
- Temperature
- Power voltage (robot bus voltage)
- IO current
- RGB LED status
- Relay state

### Topics

#### `/telemetry/stamplc/sensors`

Primary device telemetry.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `rtc_unix_s` (uint32): RTC unix timestamp if available
- `rtc_iso8601` (string): optional human-readable time
- `temperature_c` (float32)
- `bus_voltage_v` (float32): from `getPowerVoltage()`
- `io_current_a` (float32)

Rate target:

- 2 to 10 Hz depending on bandwidth constraints.

#### `/telemetry/stamplc/actuators`

Controller-side actuator and indicator state.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `relay_on` (bool)
- `status_led_r` (uint8)
- `status_led_g` (uint8)
- `status_led_b` (uint8)
- `status_led_pattern` (string): optional mode identifier

Rate target:

- 2 Hz periodic plus immediate publish on changes.

## Intended I2C Configuration

Primary external I2C topology:

- `STAMPLC -> external I2C bus`
- `8Servo Unit` at `0x25`
- `ExtEncoder Unit` at `0x59`
- Planned integration target: `goBILDA Pinpoint` at `0x31`

Additional mast I/O:

- Two digital limit switch inputs on StampPLC GPIO:
  - lower/home limit switch (used for homing and encoder reset)
  - upper limit switch (used for travel protection)
- One PWM output from `8Servo Unit` to mast motor ESC

Reference links:

- 8Servo Unit repo: <https://github.com/m5stack/M5Unit-8Servo>
- 8Servo docs: <https://docs.m5stack.com/en/unit/8Servos%20Unit>
- ExtEncoder docs: <https://docs.m5stack.com/en/unit/ExtEncoder%20Unit>
- goBILDA Pinpoint product page: <https://www.gobilda.com/pinpoint-v2-odometry-computer-imu-sensor-fusion-for-2-wheel-odometry/>
- goBILDA Pinpoint Arduino library: <https://github.com/goBILDA-Official/goBILDA-Pinpoint-Arduino-Library>

### I2C Device Topics

#### `/telemetry/i2c/bus`

Periodic bus-level diagnostics and discovery snapshot.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `bus_name` (string): for example `stamplc_external`
- `sda_pin` (int8)
- `scl_pin` (int8)
- `detected_addrs[]` (uint8)
- `bus_voltage_v` (float32): duplicated from power telemetry for correlation

Rate target:

- 1 Hz periodic, plus on-demand scan events.

#### `/telemetry/actuation/servo8/state`

Observed and commanded state for 8Servo outputs.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `i2c_addr` (uint8): expected `0x25`
- `online` (bool)
- `pwm_us[8]` (uint16): per-servo pulse width command in microseconds
- `mixer_rotate_us` (uint16): mixer CH1 command signal
- `mixer_forward_us` (uint16): mixer CH2 command signal
- `mixer_strafe_us` (uint16): mixer CH3 command signal
- `deadman_applied` (bool): true when command output is safety-gated

Rate target:

- 20 to 50 Hz, synchronized to control loop.

#### `/telemetry/actuation/servo8/health`

Device health and communication counters for 8Servo.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `i2c_addr` (uint8)
- `online` (bool)
- `i2c_write_ok_count` (uint32)
- `i2c_write_err_count` (uint32)
- `last_ok_ms` (uint64)

Rate target:

- 1 to 5 Hz periodic.

#### `/telemetry/encoder/mast`

Mast/arm lift encoder telemetry from ExtEncoder at `0x59`.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `i2c_addr` (uint8): expected `0x59`
- `online` (bool)
- `position_ticks` (int32)
- `delta_ticks` (int32)
- `velocity_ticks_s` (float32)
- `home_offset_ticks` (int32)
- `homed` (bool)
- `limit_lower` (bool)
- `limit_upper` (bool)

Rate target:

- 20 to 100 Hz depending on lift control needs.

#### `/telemetry/mast/limits`

Digital limit switch telemetry for mast homing and safety.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `limit_lower` (bool)
- `limit_upper` (bool)
- `limit_lower_raw` (bool): pre-debounce electrical level
- `limit_upper_raw` (bool): pre-debounce electrical level
- `debounced` (bool): true when software debounce is active and valid
- `homed` (bool)

Rate target:

- 20 to 100 Hz, aligned with mast control loop.

#### `/telemetry/actuation/mast_esc`

Commanded and safety-gated PWM output state for mast motor ESC.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `servo8_channel` (uint8): mapped output channel on 8Servo
- `esc_pwm_us_cmd` (uint16): requested command pulse width
- `esc_pwm_us_applied` (uint16): post-safety applied pulse width
- `direction` (int8): {-1, 0, 1}
- `limit_clamped` (bool): true when output is reduced/blocked by limit logic
- `deadman_applied` (bool)
- `online` (bool): 8Servo communication status

Rate target:

- 20 to 100 Hz, aligned with mast control loop.

#### `/telemetry/odometry/pinpoint` (Planned)

Reserved topic for goBILDA Pinpoint data once integration details are finalized.

Initial planned fields (subject to library and bus mode confirmation):

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `online` (bool)
- `x_m` (float32)
- `y_m` (float32)
- `heading_rad` (float32)
- `vx_m_s` (float32)
- `vy_m_s` (float32)
- `yaw_rate_rad_s` (float32)

### I2C Command Topics

#### `/command/actuation/servo8`

Command topic for 8Servo output updates.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `pwm_us[8]` (uint16)
- `apply_deadman` (bool)

#### `/command/encoder/mast/reset`

Control topic for ExtEncoder reset and re-zero operations.

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `reset_counter` (bool)
- `set_home_offset_ticks` (int32)

#### `/command/actuation/mast_esc`

Control topic for mast motor ESC command output (via 8Servo PWM channel).

Fields:

- `stamp_ms` (uint64)
- `seq` (uint32)
- `source` (string)
- `servo8_channel` (uint8)
- `esc_pwm_us` (uint16)
- `allow_homing_override` (bool): permit controlled motion toward lower limit during homing

## Output Architecture (ROS and Serial)

## Canonical Model

Implement a single internal topic bus in firmware/application logic.

- Producers: SBUS decoder, StampPLC sensor poller, state manager
- Topic objects: strongly typed structs matching this spec
- Transport adapters: ROS2 publisher adapter and serial adapter

This keeps message semantics identical regardless of transport.

## Option A: ROS2 Native

- Firmware or host bridge publishes topics directly to ROS2.
- Suggested ROS2 message families:
  - custom `opendubs_msgs/msg/RcRaw`
  - custom `opendubs_msgs/msg/RcNormalized`
  - custom `opendubs_msgs/msg/StampLcSensors`
  - custom `opendubs_msgs/msg/StampLcActuators`
  - custom `opendubs_msgs/msg/SafetyStatus`

Pros:

- Native ROS tooling (rviz, rosbag, rqt, diagnostics)
- Strong typing and schema governance

Cons:

- More runtime and integration overhead on embedded path

## Option B: Serial Framed Telemetry (ROS-Ready)

- Controller emits line-delimited JSON or CBOR frames over USB/UART.
- Each frame includes:
  - `topic` (string), for example `/telemetry/rc/raw`
  - `stamp_ms`, `seq`, `source`
  - `payload` object with topic-specific fields
- Host bridge process reads serial and republishes to ROS2 topics.

Pros:

- Simple controller-side implementation
- ROS is optional at runtime

Cons:

- Requires reliable framing and host bridge maintenance

## Recommended Near-Term Plan

1. Keep controller output serial-first with ROS topic-shaped payloads.
2. Define the serial frame schema as the source of truth for early testing.
3. Build a host bridge that maps `topic` strings to ROS2 publishers.
4. Promote stable topics into formal `opendubs_msgs` message definitions.

## Open Decisions

1. Raw channel range calibration values per channel (min, center, max).
2. Deadman threshold and debounce behavior.
3. Final publish rates per topic under bandwidth constraints.
4. JSON vs CBOR framing and CRC policy.
5. ROS2 message package naming and versioning strategy.
6. 8Servo command range limits and safe defaults for deadman drop.
7. ExtEncoder zeroing strategy and persistence across reboots.
8. Pinpoint integration mode (I2C/UART), address, and topic field finalization.
9. Limit switch electrical polarity (active-high vs active-low) and pull-up/down policy.
10. Homing sequence details (speed, timeout, fault behavior).

## Implementation Checklist

1. Add channel calibration and normalization config.
2. Emit `/telemetry/rc/raw` and `/telemetry/rc/normalized` in firmware.
3. Poll and emit `/telemetry/stamplc/sensors` including bus voltage.
4. Track relay and LED state and emit `/telemetry/stamplc/actuators`.
5. Add serial bridge prototype to republish into ROS2.
6. Add periodic `/telemetry/i2c/bus` scan publisher.
7. Integrate 8Servo output control and publish `/telemetry/actuation/servo8/state`.
8. Integrate ExtEncoder reads and publish `/telemetry/encoder/mast`.
9. Add deadman behavior that forces safe 8Servo output state.
10. Add two mast limit switch GPIO inputs with debounce and publish `/telemetry/mast/limits`.
11. Add mast ESC PWM output mapping on 8Servo and publish `/telemetry/actuation/mast_esc`.
12. Implement homing flow: drive to lower limit, reset/set encoder offset, assert `mast_homed`.
