# OpenDubs

**A low-cost, low-fabrication, open-source mobile robot** — designed at the University of
Washington so a small team can build a capable autonomy platform from mostly off-the-shelf parts
and a handful of 3D-printed pieces. All plans, firmware, and software are open so others can
replicate it.

- **Compute / autonomy:** NVIDIA Jetson AGX Thor (ROS 2 Jazzy)
- **Base controller:** M5Stack StampPLC (ESP32-S3) — SBUS RC ingest, mecanum drive mixer, safety
  arming + e-stop, power/BMS/CAN telemetry
- **Front panel:** M5Stack Tab5 (ESP32-P4) animated face + operator controls
- **Chassis:** goBILDA / REV structure, mecanum drive, PRISM LED indicators

> **Status: MVP / work in progress.** We're actively building the second unit. Documentation is
> filling in as we go — the firmware-flashing and bring-up paths are the most complete.

## Documentation

📖 **Read the docs → <https://uw-allen-school-robotics.github.io/opendubs/>** — the full guide,
rendered with navigation and search. Good entry points:

- **[Assembly guide](https://uw-allen-school-robotics.github.io/opendubs/build/base-frame/)** — build the robot module by module
- **[Design & interfaces](https://uw-allen-school-robotics.github.io/opendubs/design-philosophy/)** — the modularity philosophy + the mount / power / command contracts
- **[Power architecture](https://uw-allen-school-robotics.github.io/opendubs/power-architecture/)** · **[E-stop system](https://uw-allen-school-robotics.github.io/opendubs/e-stop/)** · **[Flash a StampPLC](https://uw-allen-school-robotics.github.io/opendubs/flashing-stamplc/)**

*Contributing to the docs?* See **[CLAUDE.md](CLAUDE.md)** for the workflow. Preview locally with
`pip install mkdocs-material && mkdocs serve`.

## Repository layout

| Path | What |
|---|---|
| `docs/` | The documentation site (MkDocs Material) — build/flash guides, reference, safety |
| `firmware/rc_serial_probe/` | **StampPLC** ESP32-S3 firmware (PlatformIO) — RC, drive mixer, safety I/O, BMS/PDH, `[TAG]` telemetry over USB **or** Ethernet |
| `firmware/tab5_face/` | **Tab5** ESP32-P4 face + operator UI (pioarduino) |
| `software/kiosk-dashboard/` | Touchscreen dashboard — FastAPI backend + vanilla-JS frontend + provisioning. Runs on the Thor |
| `software/ros/` | ROS 2 (Jazzy) bringup — RPLIDAR drivers, rosbridge, foxglove, watchdogs |
| `software/teleop/` | Unity VR teleop pipeline |
| `hardware/` | `cad/` (Fusion 360 exports), `bom/`, `renders/` — *being populated as the 2nd unit is built* |
| `tools/` | Repo utilities |
| `notes/` | Internal planning / design history — in-repo but not part of the published site |
| `optional/` | Non-baseline variants kept for possible future use (e.g. a Pixhawk build) — in-repo, off the baseline |
| `deploy-all.sh` · `provision-all.sh` | Provision a Thor from this repo (see below) |

## Build a copy

1. **Firmware:** flash the StampPLC (and Tab5) — see [Flash a StampPLC](docs/flashing-stamplc.md).
   The StampPLC **must be flashed over USB** (esptool needs USB-CDC; Ethernet carries only runtime
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
