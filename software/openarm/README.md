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

Day to day, with the settings file in place, one command does all of it (the side is `right` unless given):

```sh
arm up right               # THE bringup (left, right or both): the unit's preflight heals the CAN link, a failed start is retried, it waits until the service answers. Holds the arm, moves nothing
arm up both --supervise    # ... and says a person is at the e-stop (what `arm supervise` does), which a moving command needs
arm start right            # only the systemd start, without the retry and the wait
arm status                 # what it is doing; likewise activate, park, follow, hold, supervise, unsupervise, down
arm viewer 8090            # the viser viewer on that port (8080 is the dashboard), with the model the service runs
arm doctor                 # the viewer shows nothing: is the service up and answering, is /joint_states publishing, is a viewer on the graph
```

`arm` adds no gate: every command goes to the service over its socket and is gated there exactly as before. `ARM_DRY_RUN=1 arm <command>` prints what it would run.

```sh
sudo /opt/opendubs/openarm/provision-arm-service.sh     # installs the unit, the wrapper, the settings file (if absent) and the sudoers rule; provision-openarm.sh runs it too
sudoedit /etc/opendubs/arm-service.env                  # only if this robot needs more than the defaults (the arm configs are /etc/opendubs/openarm-gafro-<side>.yaml)

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
- **`/joint_states` is one message with both arms**, from `opendubs-joint-states.service` (`gafro/joint_states_merge.py`), which the arm units pull in. Each service publishes only its own arm on `/<side>/joint_states` (a fake bus for the other arm would overwrite real joints in a consumer that keeps a dict by name); the merge keeps the newest value of every joint and drops one silent for 1 s. When both arms share a process, that process publishes `/joint_states` and the unit goes.
- **It runs from a bundle, not a checkout.** `deploy-all.sh` ships `software/openarm` (linked in), `description/openarm` and `sim` to `/opt/opendubs/arm` in this repository's own layout, so
  the arm's model and the clearance twin are found by the same relative paths as in a checkout. `OPENDUBS_REPO` names a checkout only on a development robot. A robot whose
  `arm-service.env` still has `OPENDUBS_REPO=/home/...` should lose that line after the first bundle deploy.
- **Every start is preceded by a preflight, as root** (`opendubs-arm-preflight`, `ExecStartPre=+`): the old stack is not running (exit 2, not retried), the bundle and the DDS profile
  are there (78), and this arm's CAN link is `ERROR-ACTIVE`; a link in `ERROR-WARNING`, `ERROR-PASSIVE` or `BUS-OFF` is restarted, which moves nothing (what `openarm-up` did, and the unit
  did not), and one that will not come back ends the start with the cause: no power (e-stop, contactor) or a loose connector. `arm up` retries a start that failed for a reason a retry can mend.
- **The DDS profile is not a setting.** The wrapper and `arm` always use `/etc/opendubs/fastdds-robot.xml` (`provision-ros.sh` installs it) and the service refuses to start without it:
  shared memory does not cross to another Unix user, so a client in a person's shell would see the action server and never get an answer.

Checked so far, on a laptop with the simulator backend (`--backend mujoco --no-ros`): `systemd-analyze verify`, the wrapper's refusals (exit 64 a bad side, 78 no bundle or DDS profile, 2 the old
stack running), a start under a transient user unit with the same stop and restart settings, `ctl status` on its socket, a restart about 6 s after `kill -9`, a clean stop
on SIGINT. **Not run:** on the Thor, as `kiosk`, with a real bus.