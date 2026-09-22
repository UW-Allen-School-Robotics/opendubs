# OpenArm host setup

Host-side pieces for running two OpenArm arms from the Thor: the CAN adapter driver,
stable channel names, link bring-up at boot, one-shot payload setup, and the
`openarm-up` / `openarm-motors` operator commands. Full walkthrough:
[docs/openarm-setup.md](../../docs/openarm-setup.md).

## Cold start (once per robot)

1. From a dev machine: `deploy-all.sh` syncs this folder — including the pinned
   `openarm_robot-code` and `ssik` submodules — to `/opt/opendubs/openarm`.
2. On the Thor: `sudo /opt/opendubs/openarm/openarm-bringup.sh`

That installs the CAN driver, bakes the workspace image, and creates the `openarm`
container. It's safe to re-run — each step skips itself if already done — but it's
setup, not something you run to start a session.

Every session after that:

```bash
openarm-up                             # launch the stack; both arms come up INACTIVE
openarm-up activate left|right|both    # power an arm — it moves to zero over ~2s
openarm-up status                      # links, container, hardware, motors
openarm-up down                        # controllers off, arms inactive, motors verified off
```

## Files

| File | Installed to | What |
|---|---|---|
| `openarm_robot-code/` | — | pinned submodule of [EricJGoossen/openarm_robot-code](https://github.com/EricJGoossen/openarm_robot-code) — the ROS 2 impedance controller, dev-container Dockerfile, and `scripts/{build,bake}-image.sh` |
| `ssik/` | — | pinned submodule of [personalrobotics/ssik](https://github.com/personalrobotics/ssik) — the IK library the dev container mounts in |
| `openarm-bringup.sh` | — | **setup only** — driver, image bake, container creation (see Cold start above) |
| `provision-openarm.sh` | — | called by `openarm-bringup.sh`; re-run standalone after a kernel update |
| `70-openarm-can.rules` | `/etc/udev/rules.d/` | **default** channel names (`arm_left` / `arm_right`); installed only if absent — the robot's copy is per-robot |
| `openarm-can@.service` | `/etc/systemd/system/` | raises a channel at 1M/5M CAN-FD when it appears |
| `openarm-up` | `/usr/local/bin/` | every-session: launch / activate / stop the arm stack |
| `openarm-motors` | `/usr/local/lib/openarm/` (+ `/usr/local/bin` link) | direct CAN check of all 16 motors; can only query or disable |

`openarm-bringup.sh` also creates `ws_state/` and an `openarm-ros2:$(hostname)` image
alongside these — generated at setup time, not tracked in the repo.

Deployed to `/opt/opendubs/openarm` by `deploy-all.sh`. Not called from `provision-all.sh`,
because the arms are an optional payload.