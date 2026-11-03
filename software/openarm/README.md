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
| `opendubs-arm@.service` | `/etc/systemd/system/` | the arm service on gafro for one arm (`right` / `left`); not enabled at boot. See below |
| `opendubs-arm` | `/usr/local/lib/opendubs/` | what that unit runs: the gafro environment, the old-stack check, then `gafro/arm_service.py` |
| `arm` | `/usr/local/bin/` | the operator's command for the service: `arm start`, `arm status`, `arm activate`, `arm follow`, `arm viewer`, `arm doctor` (below) |
| `opendubs-arm.env.example` | `/etc/opendubs/arm-service.env` | this robot's settings for the unit (the checkout, the driver's config); per robot, never overwritten |
| `opendubs-arm.sudoers` | `/etc/sudoers.d/opendubs-arm` | lets `kiosk` start, stop and restart the arm units, and nothing else of systemd |
| `provision-arm-service.sh` | `/opt/opendubs/ws` | installs the four files above and builds `interfaces/` into the workspace the wrapper sources; run by `provision-openarm.sh`, or alone |

Deployed to `/opt/opendubs/openarm` by `deploy-all.sh`. Not called from `provision-all.sh`,
because the arms are an optional payload.


## The arm service as a unit

`gafro/arm_service.py` is one process per arm that does what ros2_control and `openarm-up` did (the lifecycle by command, `FollowJointTrajectory`, `GripperCommand`,
pose targets). `opendubs-arm@right.service` / `opendubs-arm@left.service` run it as the user `kiosk`.

> **Not buildable from public sources yet:** the gafro stack installs three repositories that are not public (gafro,
> gafro-robot-interface and openarm-driver; see [`gafro/README.md`](gafro/README.md)). Until they are, you cannot build it yourself. The
> other stack for the same arms is the ROS 2 one, `openarm-up` ([docs/openarm-setup.md](../../docs/openarm-setup.md)), whose image also
> clones one repository that is not public, the teleop bridge (`image/repos.lock`).

Day to day, with the settings file in place, one command does all of it (the side is `right` unless given):

```sh
arm start right            # the service; enables the arm's motors and holds it where it is
arm status                 # what it is doing; likewise activate, park, follow, hold, supervise, unsupervise, down
arm viewer 8090            # the viser viewer on that port (8080 is the dashboard), with the model the service runs
arm doctor                 # the viewer shows nothing: is the service up and answering, is /joint_states publishing, is a viewer on the graph
```

`arm` adds no gate: every command goes to the service over its socket and is gated there exactly as before. `ARM_DRY_RUN=1 arm <command>` prints what it would run.

```sh
sudo /opt/opendubs/openarm/provision-arm-service.sh     # installs the unit, the wrapper, the settings file (if absent) and the sudoers rule; provision-openarm.sh runs it too
sudoedit /etc/opendubs/arm-service.env                  # the checkout (OPENDUBS_REPO) and the driver's config

sudo systemctl start opendubs-arm@right                 # enables the arm's motors and holds it where it is
journalctl -u opendubs-arm@right -f
sudo -u kiosk OPENDUBS_ARM_SOCKET_DIR=/run/opendubs-arm python software/openarm/gafro/arm_service.py ctl --side right status
```

`provision-arm-service.sh` installs nothing it cannot check first: the sudoers rule goes through `visudo -cf` before any file is written, and `ROOT=<dir>` installs under `<dir>` without
root and without touching the machine. The rule lets `kiosk` run exactly `systemctl start|stop|restart opendubs-arm@left|right.service` (with the `.service`, as written) and nothing else.

What the unit decides, and why:

- **Not enabled at boot.** Starting the service enables the motors, and `openarm-up` kept the arms inactive until asked. A start at boot would also meet an arm that fell when
  the power dropped. A person, or the dashboard's button, starts it.
- **It starts unsupervised.** A moving command is refused on a real bus until `ctl supervise` (a person is at the e-stop, head tracking off, neck centred), and CH5, the e-stop
  and CH6 are checked for every command as before.
- **A crash is restarted, a few times** (`Restart=on-failure`, 4 starts in 120 s). A dead process leaves the motors enabled and holding the last command; the next start reads the
  position (two agreeing replies) and holds there. It does not retry when the old stack is running (the wrapper exits 2): `openarm-up down` first.
- **`systemctl stop` does not release the arm.** The driver has no disable: the motors stay enabled and stiff until the e-stop power cycle, and after one they are off and the
  service has to be restarted. The unit stops it with SIGINT, the only signal the service ends cleanly on (it holds, removes its socket, shuts the robot interface down).
- **It runs from a checkout** (`OPENDUBS_REPO`). `deploy-all.sh` copies `software/openarm` to `/opt/opendubs/openarm` without `description/openarm/` or `sim/`, and the arm's model
  and the clearance twin are found relative to the file's place in the repository. Until a deploy ships them, the checkout has to be on the branch the robot runs and be
  readable by `kiosk`.

Checked so far, on a laptop with the simulator backend (`--backend mujoco --no-ros`): `systemd-analyze verify`, the wrapper's refusals (exit 64 a bad side, 78 no checkout, 2 the old
stack running, 1 `OPENDUBS_REPO` unset), a start under a transient user unit with the same stop and restart settings, `ctl status` on its socket, a restart about 6 s after `kill -9`, a clean stop
on SIGINT. **Not run:** on the Thor, as `kiosk`, with a real bus.