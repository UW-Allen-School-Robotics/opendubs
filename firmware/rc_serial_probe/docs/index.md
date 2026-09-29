# RC Telemetry Wiki

This wiki captures the telemetry contract for the M5StamPLC controller and the plan for publishing telemetry using ROS topic concepts, independent of transport.

For robot-level architecture, safety, modes, and BOM documentation, use the top-level docs hub:

- [OpenDubs Robot Documentation Hub](../../docs/README.md)

## Pages

- [Telemetry Specification Plan — ARCHIVED (superseded; see the firmware README for current `[TAG]` serial telemetry)](../../docs/history/telemetry-spec.md)
- [System Overview](../../docs/system-overview.md)
- [Control Authority and Modes](../../docs/control-authority.md)
- [Robot and Subsystem State Machines](../../docs/state-machines.md)
- [PWM and ESC Safety](../../docs/pwm-esc-safety.md)
- [REV PDH CAN Feasibility](../../docs/rev-pdh-can-feasibility.md)

## Scope

- RC receiver telemetry (8 channels and derived control intent)
- StamPLC telemetry (RTC, temperature, power, IO current, relay state, RGB LED state)
- Output architecture for ROS-native and serial-first deployments
