# System Overview

## Core Architecture

The core robot as buses and interfaces. **Click the diagram to pan and zoom the live board** — the
labels are small at page scale:

<div class="embed-facade" markdown="1" data-embed="https://embed.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=21-995&embed-host=opendubs-docs" data-title="OpenDubs bus map (Figma)">
![OpenDubs bus map — click to pan and zoom the interactive board](assets/diagrams/opendubs-bus-map.png)
<span class="embed-facade__play">▶ Pan &amp; zoom the live board</span>
</div>

Power + e-stop, position/spatial, teleop, control, safety/state, drive, comms, and compute —
converging on the StampPLC and Thor, and exposed to a payload through the standard interfaces
(24 V logic, I²C, CAN, RS485, USB, Ethernet, Wi-Fi, and 24 V safety/e-stopped).

- **Open in Figma:** [OpenDubs board — bus map](https://www.figma.com/board/LVbgJwMKO65TARF3m6v7I6/OpenDubs?node-id=21-995) — the live board also includes the **payload-connected** system bus map (the poster above shows the core version). Clicking the diagram loads the full interactive board.

- Primary robot brain: **NVIDIA Thor AGX Dev Kit**
- Mobile-base low-level controller: **StampPLC** — one of several low-level controllers on the
  robot; each payload may bring its own
- Mobile base: 4 motors + 2 dual-channel ESCs, optimised for **mecanum** drive and switchable to
  **4WD traction** drive in software
- Z-axis: the interchangeable payload that raises and lowers the arms — a **scissor lift**
  (fitted today) or a **mast** (DC motor driving a lead screw)
- Manipulators: dual OpenArm 7-DOF arms

OpenArm reference:
- https://github.com/enactic/OpenArm

## Responsibility Split

### Thor AGX (robot-level)

- High-level behavior, planning, and autonomy orchestration
- Robot mode supervision
- Subsystem enable/disable coordination
- System-level fault handling policy decisions

### StampPLC (edge controller)

- RC/SBUS ingest and normalization
- PWM output generation and safety gating
- I2C/CAN edge interactions
- Fast safety fallback behaviors
- Hardware telemetry publishing

## Subsystems

- Mobile base subsystem
- Z-axis subsystem
- Left arm subsystem
- Right arm subsystem

Each subsystem has local state, fault state, and enable state.

## Related Docs

- [Control Authority and Modes](control-authority.md)
- [Robot and Subsystem State Machines](state-machines.md)
- [PWM and ESC Safety](pwm-esc-safety.md)
