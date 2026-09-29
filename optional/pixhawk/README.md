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
| `opendubs-mavros.service` + `mavros.sh` | `software/ros/` | MAVROS launch — owns the Pixhawk serial link, republishes `/mavros/*`, forwards raw MAVLink to the dashboard |
| `requirements.txt` | `pymavlink` (was in the backend deps) | Python MAVLink lib, needed only by `mavlink_rc.py` |

## To re-enable (future Pixhawk build)

1. Copy `mavlink_rc.py` back to `software/kiosk-dashboard/backend/` and wire its `reader` into the
   dashboard status where a Pixhawk panel is wanted.
2. Add `pymavlink` back to `software/kiosk-dashboard/backend/requirements.txt`.
3. Copy `opendubs-mavros.service` + `mavros.sh` back to `software/ros/`, add `opendubs-mavros` to the
   `SERVICES` lists in `deploy-all.sh` and `software/ros/provision-ros.sh`, and re-add the MAVROS apt
   packages (`ros-<distro>-mavros`, `-mavros-extras`) + the geographiclib datasets step to
   `provision-ros.sh`.
4. Decide the control-authority split (a Pixhawk changes who may command actuators — see the safety
   docs) before letting it touch drive.
