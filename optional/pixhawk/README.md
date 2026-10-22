# Optional: Pixhawk / MAVLink variant (NOT the baseline)

> Preserved but **not part of the baseline build** — treated like the research/planning material in
> [`../../notes/`](../../notes/). The baseline OpenDubs robot has **no flight controller**: RC comes
> from the StamPLC over SBUS, and telemetry/commands go StamPLC ↔ Thor over USB/Ethernet.

This directory keeps the code for a **possible future variant** that adds a Pixhawk (ArduPilot,
MAVLink) — e.g. for its GPS/EKF, optical flow, or position-hold modes. It is intentionally kept out
of provisioning, deploy, and the dependency set so it can't affect the baseline, and is here so the
work isn't lost if we build that variant.

## What's here

| File | Was | Role |
|---|---|---|
| `mavlink_rc.py` | `software/kiosk-dashboard/backend/` | dashboard-side MAVLink reader (RC/GPS/flow) |
| `requirements.txt` | `pymavlink` (was in the backend deps) | Python MAVLink lib, needed only by `mavlink_rc.py` |

The MAVROS launch (`opendubs-mavros.service` and its `mavros.sh` wrapper) was removed on 2026-10-22: the
Pixhawk is retired, and provisioning already removes that unit from any robot that still has it. Both
files are in the repository's history before that date.

## To re-enable (future Pixhawk build)

1. Copy `mavlink_rc.py` back to `software/kiosk-dashboard/backend/` and wire its `reader` into the
   dashboard status where a Pixhawk panel is wanted.
2. Add `pymavlink` back to `software/kiosk-dashboard/backend/requirements.txt`.
3. Write a MAVROS launch unit for `software/ros/` (the removed one is in history), add it to `SERVICES`
   in `software/ros/provision-ros.sh`, and re-add the MAVROS apt packages (`ros-<distro>-mavros`,
   `-mavros-extras`) and the geographiclib datasets step to `provision-ros.sh`. Name its account
   with `@OPENDUBS_USER@` / `@OPENDUBS_HOME@`, like every other ROS unit.
4. Decide the control-authority split (a Pixhawk changes who may command actuators — see the safety
   docs) before letting it touch drive.
