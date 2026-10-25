# OpenDubs

**A low-cost, low-fabrication, open-source mobile robot** — designed at the University of
Washington so a small team can build a capable autonomy platform from mostly off-the-shelf parts
and a handful of 3D-printed pieces. All plans, firmware, and software are open so others can
replicate it.

- **Compute / autonomy:** NVIDIA Jetson AGX Thor (ROS 2 Jazzy)
- **Base controller:** M5Stack StamPLC (ESP32-S3) — SBUS RC ingest, mecanum drive mixer, safety
  arming + e-stop, power/BMS/CAN telemetry
- **Head:** a Samsung Galaxy Tab A11 tablet — animated face, speaker, and the operator's dashboard
  when lifted off — on a pan/tilt neck driven by an M5Stack M5Dial (ESP32-S3)
- **Chassis:** goBILDA / REV structure, mecanum drive, PRISM LED indicators
- **Manipulation:** bimanual [OpenArm](https://github.com/enactic/openarm), 7 joints per side over
  CAN, riding a 275 mm scissor lift
- **Sensing:** RPLIDAR A1, an Intel RealSense D415 on the head, and two odometry modules —
  a goBILDA Pinpoint with dead-wheel pods, and a SparkFun OTOS optical sensor

> **Status: MVP / work in progress.** Documentation is filling in as we go — the firmware-flashing
> and bring-up paths are the most complete.

## Documentation

📖 **Read the docs → <https://uw-allen-school-robotics.github.io/opendubs/>** — the full guide,
rendered with navigation and search. Good entry points:

- **[Assembly guide](https://uw-allen-school-robotics.github.io/opendubs/build/base-frame/)** — build the robot module by module
- **[Design & interfaces](https://uw-allen-school-robotics.github.io/opendubs/design-philosophy/)** — the modularity philosophy + the mount / power / command contracts
- **[Power architecture](https://uw-allen-school-robotics.github.io/opendubs/power-architecture/)** · **[E-stop system](https://uw-allen-school-robotics.github.io/opendubs/e-stop/)** · **[Flash a StamPLC](https://uw-allen-school-robotics.github.io/opendubs/flashing-stamplc/)**

*Contributing to the docs?* See **[CLAUDE.md](CLAUDE.md)** for the workflow. Preview locally with
`pip install mkdocs-material && mkdocs serve`.

## Simulation

`sim/` holds a MuJoCo model of the robot in the **CAD frame** — chassis, mecanum wheels with
their rollers, the Z-axis and the pan/tilt head. The arms are not redrawn: the upstream OpenArm
model is attached at runtime, so it stays in step with that project.

```bash
cd sim
./view.sh                                                  # interactive viewer
python3 scene.py --render out.png --lift 275               # offscreen render
python3 clearance.py --joints "right:2=182,right:4=100"    # distance to self-collision
```

It is a **description of the robot, not a planner**, so several planners can consume it and
disagree with each other without any of them owning it. Every length, axis and pivot traces to
CAD or to a measurement; **masses, stiffnesses and speeds are placeholders**, so it answers
questions about clearance and reach and not about force, torque or timing.
[`sim/README.md`](sim/README.md) says which is which, and
[`description/opendubs/dimensions.yaml`](description/opendubs/dimensions.yaml) carries the provenance of every number.

## Repository layout

| Path | What |
|---|---|
| `docs/` | The documentation site (MkDocs Material) — build/flash guides, reference, safety |
| `firmware/rc_serial_probe/` | **StamPLC** ESP32-S3 firmware (PlatformIO) — RC, drive mixer, safety I/O, BMS/PDH, `[TAG]` telemetry over USB **or** Ethernet |
| `firmware/neck_dial/` | **M5Dial** ESP32-S3 neck controller — pan/tilt through its own 8Servo, the head protocol over UDP, updated over the network |
| `firmware/servo_mode_programmer/` | Reprograms the pan/tilt servos' mode — a bench tool, not flashed in normal operation |
| `firmware/tab5_idf/` · `tab5_face/` · `tab5_pantilt/` · `tab5_roller/` | Firmware for an M5Stack Tab5 head, which the current robot does not carry |
| `software/face-web/` | The robot's face as a web page, shown on the head tablet |
| `software/tablet-kiosk/` | The Android app that runs the tablet as part of the robot: full screen and locked in front |
| `software/kiosk-dashboard/` | Touchscreen dashboard — FastAPI backend + vanilla-JS frontend + provisioning. Runs on the Thor |
| `software/ros/` | ROS 2 (Jazzy) bringup — RPLIDAR drivers, rosbridge, foxglove, watchdogs |
| `software/openarm/` | Arm permanence — CAN driver packaging, udev naming, boot services, `openarm-up` |
| `software/choreo/` | Choreography — a show is a validated cue sheet, checked before anything moves |
| `software/teleop/` | Unity VR teleop pipeline |
| `sim/` | **MuJoCo model of the robot** (geometry, not a planner) — see [Simulation](#simulation) |
| `hardware/` | `parts.csv` — **the BOM source of truth**, from which every table in the docs is generated — plus `cad/` (Fusion 360 exports) and `renders/` |
| `access/` | How SSH accounts on the robots are managed (`tools/access.py`); the roster itself lives in the git-ignored `ops/access/` |
| `tools/` | Repo utilities |
| `notes/` | Internal planning / design history — in-repo but not part of the published site |
| `optional/` | Non-baseline variants kept for possible future use (e.g. a Pixhawk build) — in-repo, off the baseline |
| `deploy-all.sh` · `provision-all.sh` | Provision a Thor from this repo (see below) |

## Build a copy

1. **Firmware:** flash the StamPLC — see [Flash a StamPLC](docs/flashing-stamplc.md). The neck
   controller has its own notes in [`firmware/neck_dial/README.md`](firmware/neck_dial/README.md).
   The StamPLC **must be flashed over USB** (esptool needs USB-CDC; Ethernet carries only runtime
   data). It builds/flashes from a Mac (local PlatformIO) or the Thor (`/opt/opendubs/piovenv`).
2. **Thor:** flash the pinned base (JetPack 7 / L4T R38.4 / Ubuntu 24.04), then from a dev machine
   `./deploy-all.sh <host>` rsyncs the subsystems to `/opt/opendubs` and runs `provision-all.sh`
   (kiosk → ROS → firmware). Runbook: [`docs/thor-setup.md`](docs/thor-setup.md).

## Licensing

Multi-licensed, standard for open hardware:

- **Software / firmware** — Apache-2.0 ([`LICENSE-CODE`](LICENSE-CODE))
- **Hardware** (CAD, mechanical, electrical designs) — CERN-OHL-W-2.0 ([`LICENSE-HARDWARE`](LICENSE-HARDWARE))
- **Documentation & media** — CC-BY-4.0 ([`LICENSE-DOCS`](LICENSE-DOCS))

Contributions are accepted under these licenses via the [DCO](CONTRIBUTING.md) (sign-off). See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## Acknowledgments

Initial robots built under a hardware grant from **NVIDIA**. Developed at the **University of
Washington, Paul G. Allen School of Computer Science & Engineering**.
