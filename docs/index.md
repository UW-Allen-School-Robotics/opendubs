# OpenDubs

A **low-cost, low-fabrication, open-source mobile robot** built at the University of Washington: an
NVIDIA Jetson AGX Thor autonomy brain, an M5Stack StampPLC base controller, mecanum drive on a
goBILDA/REV chassis, and a Tab5 animated face — designed so a small team can replicate it from
mostly off-the-shelf parts.

!!! tip "Building the second unit? Start here"
    **[Flash a StampPLC →](flashing-stamplc.md)** — the base-controller firmware bring-up (over USB).
    Then the [coordinate frames & naming](naming-frames.md) convention everything else references.

!!! info "Current baseline (2026-08)"
    RC comes from the **StampPLC via SBUS** — the StampPLC is the control authority + telemetry hub.
    Power is a **52 V LiFePO₄ pack (JBD BMS,
    RS485)** split into an always-on **logic-24 V rail** (upstream of the e-stop contactor) and a
    **drive-24 V rail** (behind the contactor); a connected charger ⇒ drive inhibit. Localization is
    a **ROS 2 EKF on the Thor**.

## Where things are

- **Firmware** — `firmware/rc_serial_probe/` (StampPLC) and `firmware/tab5_face/` (Tab5). Flashing:
  [Flash a StampPLC](flashing-stamplc.md); transport design: [StampPLC ↔ Thor over Ethernet](stamplc-ethernet-transport.md).
- **Software** — `software/kiosk-dashboard/` (dashboard), `software/ros/` (ROS 2 bringup),
  `software/teleop/`. Bring-up: [Thor setup](thor-setup.md), [Deployment & portability](portability-audit.md).
- **Hardware** — `hardware/` (CAD/BOM/renders, being populated). Parts: [Bill of materials](hardware-bom.md).

## Reference

- [System overview](system-overview.md) · [Coordinate frames & naming](naming-frames.md)
- [Control authority & modes](control-authority.md) · [State machines](state-machines.md)
- [Localization & sensor fusion](localization-fusion.md) · [Network architecture](network-architecture.md)
- Safety & power: [ESC / PWM safety](pwm-esc-safety.md) · [Regen clamp sizing](regen-clamp-sizing.md) · [REV PDH / CAN](rev-pdh-can-feasibility.md)

## Status

Work in progress — actively building the second unit. Firmware flashing and host bring-up are the
most complete; the physical assembly guide and CAD/BOM are being filled in as the build proceeds.
Autonomous drive ownership is in progress; the modular mast/scissor payload is deferred.
