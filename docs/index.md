# OpenDubs

A **low-cost, low-fabrication, open-source mobile robot** built at the University of Washington: an
NVIDIA Jetson AGX Thor autonomy brain, an M5Stack StampPLC base controller, mecanum drive on a
goBILDA/REV chassis, and a Tab5 animated face — designed so a small team can replicate it from
mostly off-the-shelf parts.

!!! danger "Safety & disclaimer — read before you build or operate"
    **This is a heavy (~25 kg), high-power (~52 V) motorized robot — not a toy.** Building and
    operating it safely requires competence with mechanical assembly, high-current DC wiring,
    lithium batteries, and mobile-robot operation. If you are not comfortable with all of these,
    get qualified help.

    **Provided "as is."** These designs, files, and instructions are provided **"AS IS", without
    warranty of any kind**, express or implied — including any warranty of correctness, safety,
    merchantability, or fitness for a particular purpose. **You build and operate entirely at your
    own risk.** The authors and contributors accept **no liability** for any injury, death, or damage
    to persons or property arising from building or using this robot or these files.

    **Build hazards**

    - The **battery is the heaviest single part.** Lift with help, and **never put hands or feet
      under the robot** while lifting, mounting, or working beneath it — block and support it.
    - **Electrical: shock, arc, fire, stored energy.** ~52 V DC at high current can arc, burn, and
      ignite. Disconnect the battery before wiring; bus capacitors stay charged after power-off —
      treat the drive bus as live.
    - Wear **PPE** — safety glasses and gloves (both in the tools list).

    **Operating hazards**

    - **Uncommanded motion.** Faults, RC/RF dropout, software bugs, or wiring errors can move the
      robot unexpectedly. Keep clear, keep the **e-stop within reach**, and first test with the
      **wheels off the ground**.
    - **Roll-away.** On **mecanum wheels the robot does not hold position** — it can roll freely on a
      slope, powered or not. Chock or restrain it; never leave it on an incline.
    - **Collisions.** It has real mass and drives under power — a collision can **injure people or
      damage property.** Operate in a clear, controlled area, away from bystanders and pets.

    **Battery safety.** Follow the manufacturer's safety and PPE guidance for the LiFePO4 pack —
    [ECO-WORTHY battery manual (PDF)](https://cdn.shopify.com/s/files/1/0253/9752/6580/files/48V_50AH_Rack_Battery_Instruction_Manual.pdf?v=1752224364).
    Never short, over-discharge, puncture, submerge, or charge a damaged pack; isolate and stop
    using any pack that is hot, swollen, or smoking.

!!! tip "Start here"
    New here? Read the **[System overview →](system-overview.md)** first — the whole robot as buses
    and interfaces, in one diagram. Then **[flash a StampPLC](flashing-stamplc.md)** to bring up the
    base controller, and skim the [coordinate frames & naming](naming-frames.md) convention
    everything else references.

!!! info "Current baseline (2026-08)"
    RC comes from the **StampPLC via SBUS** — the StampPLC is the control authority + telemetry hub.
    Power is a **52 V LiFePO₄ pack (JBD BMS,
    RS485)** split into an always-on **logic-24 V rail** (upstream of the e-stop contactor) and a
    **drive-24 V rail** (behind the contactor); a connected charger ⇒ drive inhibit. Localization is
    a **ROS 2 EKF on the Thor**.

## Firmware, software, hardware

- **Firmware** — `firmware/rc_serial_probe/` (StampPLC) and `firmware/tab5_face/` (Tab5). Flashing:
  [Flash a StampPLC](flashing-stamplc.md); transport design: [StampPLC ↔ Thor over Ethernet](stamplc-ethernet-transport.md).
- **Software** — `software/kiosk-dashboard/` (dashboard), `software/ros/` (ROS 2 bringup),
  `software/teleop/`. Bring-up: [Thor setup](thor-setup.md), [Deployment & portability](portability-audit.md).
- **Hardware** — `hardware/` (CAD/BOM/renders, being populated). Parts: [Bill of materials](hardware-bom.md).

## Reference

- [System overview](system-overview.md) · [Coordinate frames & naming](naming-frames.md)
- [Control authority & modes](control-authority.md) · [State machines](state-machines.md)
- [Localization & sensor fusion](localization-fusion.md) · [Network architecture](network-architecture.md)
- Safety & power: [ESC / PWM safety](pwm-esc-safety.md) · [Regen clamp sizing](regen-clamp-sizing.md)
- Teaching with this? [Educators & academic access](educators.md) — free/discounted CAD, compute, and parts.

## Status

Work in progress. Firmware flashing and host bring-up are the most complete; the physical assembly
guide and CAD/BOM are being filled in as the build proceeds. Autonomous drive ownership is in
progress; the modular mast/scissor payload is deferred.
