# OpenArm host setup

Host-side pieces for running two OpenArm arms from the Thor: the CAN adapter driver,
stable channel names, link bring-up at boot, and the `openarm-up` / `openarm-motors`
commands. Full walkthrough: [docs/openarm-setup.md](../../docs/openarm-setup.md).

| File | Installed to | What |
|---|---|---|
| `provision-openarm.sh` | — | idempotent installer; run `sudo /opt/opendubs/openarm/provision-openarm.sh` |
| `70-openarm-can.rules` | `/etc/udev/rules.d/` | **default** channel names (`arm_left` / `arm_right`); installed only if absent — the robot's copy is per-robot |
| `openarm-can@.service` | `/etc/systemd/system/` | raises a channel at 1M/5M CAN-FD when it appears |
| `openarm-up` | `/usr/local/bin/` | launch / activate / stop the arm stack |
| `openarm-motors` | `/usr/local/lib/openarm/` (+ `/usr/local/bin` link) | direct CAN check of all 16 motors; can only query or disable |

Deployed to `/opt/opendubs/openarm` by `deploy-all.sh`. Not called from `provision-all.sh`,
because the arms are an optional payload.
