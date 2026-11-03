# The arm service on opendubs2

How the arms run on gafro (`opendubs-arm@left|right.service`, `software/openarm/gafro/`), how to rebuild what they run on, and what is known to be wrong. Written on
2026-10-29, the day the stack was rebuilt from current sources and the left arm first ran on it. The old ROS stack (`openarm-up`, the `openarm` container) is described in
[openarm-setup.md](openarm-setup.md); the two never run together. The command table, the gates and the pose-following design are in
`software/openarm/gafro/README.md`; the dashboard's messages are in [arm-dashboard.md](arm-dashboard.md).

## What runs

| Piece | Where | Notes |
|---|---|---|
| `opendubs-arm@left` / `@right` | systemd, user `kiosk` | `static`: never enabled at boot, because starting one enables the arm's motors and holds the arm where it is. Restarts on failure every 5 s, at most 4 in 120 s (`StartLimitBurst`); after that `systemctl reset-failed` |
| `openarm-can@arm_left` / `@arm_right` | systemd | the CAN links, 1 Mbit/s with 5 Mbit/s data. Nothing restarts one that has gone `ERROR-PASSIVE` or `BUS-OFF` |
| `/usr/local/lib/opendubs/opendubs-arm` | the unit's wrapper | sources `/opt/gafro/setup.bash` and the interface workspace `/opt/opendubs/ws`, and takes the arm's hardware config from `/etc/opendubs/openarm-gafro-<side>.yaml` unless `OPENDUBS_ARM_CONFIG` names one |
| `/etc/opendubs/arm-service.env` | the unit's settings | `OPENDUBS_REPO=<checkout>` (the checkout of this repository the service runs from), and the DDS profile (below) |
| `/etc/opendubs/openarm-gafro-left.yaml`, `-right.yaml` | per-arm hardware config | one file per arm, the other arm's group on `bus: fake`: a service starts every group's driver, so a file with both arms on `bus: can` would start both arms in each process |
| `/opt/gafro/venv`, `/opt/gafro-deps` | the stack | built by `~/gafro-deploy` from the remotes' `main` (`stack.toml`) |
| `~/gafro-ri-ros2-bridge` | the ROS 2 bridge | **not** in `stack.toml`: built by hand, see below |

The service refuses to start while the old stack runs (`ros2_control_node`), and the driver refuses a bus another sender is commanding. If the old stack is up: `openarm-up down`.

## Operating it

`arm` (`software/openarm/arm`) is the operator's command; it takes care of the socket belonging to `kiosk`.

```sh
arm start left            # sudo systemctl start opendubs-arm@left.service: enables the motors and holds the arm where it is. Nothing moves
arm status left           # held / supervised / joint angles
arm supervise left        # "a person is at the e-stop, head tracking off, neck centred": nothing moves without it
arm activate left         # to the model's ready pose; arm park left goes back to the stow pose; arm hold stops where it is
arm down left             # ends the service, only from stow. `arm stop` (systemctl stop) leaves the motors enabled and stiff: only the e-stop cycle releases them
```

Moves also need CH5 in MANUAL or AUTO, the e-stop clear and **CH6 (the deadman) held** for the whole move. The grippers and the named commands, from a shell with
`source /opt/gafro/setup.bash; source /opt/opendubs/ws/install/setup.bash`:

```sh
ros2 action send_goal /left_gripper_controller/gripper_cmd control_msgs/action/GripperCommand "{command: {position: 0.044, max_effort: 10.0}}"   # metres: 0.044 open, 0.0 shut
ros2 action send_goal --feedback /left_arm/command opendubs_arm_interfaces/action/ArmCommand '{name: close_gripper, params: "{}"}'              # open_gripper, ready, reach_pose, candy_grasp
```

A goal that ends at once with `stalled: false`, `position: 0.0` was refused, and the reason is in `journalctl -u opendubs-arm@<side> -n 5 -o cat` (not supervised, deadman released).
Park before leaving the robot: an e-stop at ready lets the forearm fall.

## What is configured on the robot, and why

These files live in `/etc/opendubs/` and are not in the repository. `provision-arm-service.sh` installs the unit and the wrapper, and creates `arm-service.env` from the template only if it is absent.

**DDS profile** (`arm-service.env`: `FASTRTPS_DEFAULT_PROFILES_FILE`, `FASTDDS_DEFAULT_PROFILES_FILE`, `ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET`, the same three lines as `ros-dds.env`). The service runs as
`kiosk`, and Fast DDS's shared memory does not cross to another Unix user. Without the profile a client in a person's shell sees the action server (`ros2 action list`) but `Action servers: 0` in
`ros2 action info`, goals are never answered and `ros2 topic echo` waits forever.

**Right arm** (`openarm-gafro-right.yaml`): the driver's defaults (`rate` 250, `start_attempts` 20, `verify_motors: true`). Gripper `gripper_open_position: -1.1256`, `gripper_closed_position: 0.0075`,
`gripper_torque: 0.3`, **measured** by hand at the end stops with the motors off (−64.49° and +0.43°).

**Left arm** (`openarm-gafro-left.yaml`): the same, except

| key | left | default | why |
|---|---|---|---|
| `rate` | 500 | 250 | the arm's joints 1 and 2 answer few of the status requests they are sent, so the replies per second scale with how often they are asked |
| `start_interval`, `start_attempts` | 0.001, 4000 | 0.01, 20 | the start check needs two agreeing status frames from every joint before it enables anything. At 100 Hz joint 1 gave about one frame a second; at about 750 Hz for 4 s it gives about 100 |
| `reply_timeout` | 0.5 | 0.1 | a joint that answers a few percent of the time is not "silent" after 0.1 s without a reply; 0.5 s is how late a real loss is noticed |
| `link_retry` | 0.05 | 0.5 | when an exchange fails ("bus not healthy") the driver sends **nothing** until it has retried the link, every `link_retry` seconds: at 0.5 s a failure mid-move froze the whole arm for half a second (below). Set on 2026-10-29 as an experiment; see Known problems |
| `verify_motors` | false | true | joints 1 and 2 do not answer register reads, so their control mode and ranges cannot be checked; they are **not** checked |
| gripper | −0.02 / +1.18 | | **derived**, not measured: the finger calibration in `grippers.yaml` (open +0.0008 m, closed −0.0496 m) at the old plugin's −23.8 rad/m. The right arm's derived values were 0.03 and 0.013 rad from its measured ones. Read the end stops (`openarm-motors status` with the jaws moved by hand) before trusting it |

A systemd drop-in, `/etc/systemd/system/opendubs-arm@left.service.d/tracking.conf`, gives the **left** unit `--max-error 0.5` (the service stops a move when a joint is that far from its
path; the default 0.2 stopped two moves at 0.20 and 0.21 rad), `--record-dir /var/tmp/arm-rec` (a CSV of every move: joint angles, velocities, torques, references and the error at about
450 samples a second) and `PYTHONFAULTHANDLER=1` (an abort prints every thread's Python stack into the journal). The drop-in file must end in `.conf` or systemd ignores it; check with
`systemctl show opendubs-arm@left.service -p Environment --value`, and `ps` for the options on the running process. Remove the recording when the stops are understood.

## What the shoulder motors do

Measured with `software/openarm/gafro/tools/can_reply_rates.py rates` (status requests to all eight ids, 47 to 750 rounds a second; services stopped, 2026-10-29):

| motor | answers, right arm | answers, left arm |
|---|---|---|
| joint 1 (DM8009) | 84–94 % | **1–3 %** |
| joint 2 (DM8009) | 56–79 % | **14–19 %** |
| joints 3–8 | about 100 % | about 96–100 % |

The fraction does not depend on how fast the motors are asked; the number of replies a second does. Joints 1 and 2 of the left arm are the weak spot: the left bus sits in `ERROR-WARNING` or
`ERROR-PASSIVE` while they are on it, which is what the driver's bus-health check refuses. Something is wrong at the left shoulder's connector, power or transceiver, and nobody has looked at it. The old stack
ignores bus health and polls at 400 Hz, which is how it got by. In four recorded moves the arm's feedback looked normal; a fifth caught the half-second freeze of the whole arm described under Known problems.

## After a gafro update

Stop both arm services first; the build replaces the venv. Never run two compilers at once on this machine (`-j 2`).

```sh
cd ~/gafro-deploy && git pull --ff-only && source /opt/gafro/setup.bash
python3 scripts/gafro_deploy.py build --profile openarm --release <tag>      # gafro, gafro_bridge, gafro_robot_interface, openarm_assets, openarm_driver, ssik, self_motion_charts
python -I -c "import ssik._ssik_native"                                      # must not fail: see below
cd ~/gafro-ri-ros2-bridge && cmake --build build --clean-first -j 2 && cmake --install build       # by hand: see below
bash ~/opendubs/software/openarm/gafro/tools/check_stack.sh                  # from a fresh login shell
```

- **The ROS 2 bridge is not built by `gafro-deploy`.** It links libgafro's headers, so after a new gafro the service aborts at `import gafro_ri_ros2_bridge` with `gafro ABI mismatch: this code was
  built against gafro 2.0.0 but loaded a libgafro built as 2.0.0 (headers: …)`. Its CMake cache already has the prefix `/opt/gafro`. (Adding it to `stack.toml` is the real fix.)
- **Never `pip install` into `/opt/gafro/venv` without `--no-deps`.** `ssik` must be the native build (the reach check asks at 50 Hz). A dependency-resolving install can replace it with PyPI's pure-Python
  one, and the deploy tool's own import check does not look at `ssik._ssik_native`. The tool's shallow clone also labels the native build `0.1.dev1` unless it is given the tag; `self_motion_charts` then
  asks pip for `ssik>=8.1` from PyPI. Restore with `pip install --no-deps --force-reinstall ~/gafro-deploy/dist/<tag>/ssik-*.whl`.
- **libgafro hides its own tinyxml2** (`--exclude-libs,libtinyxml2.a`, gafro PR #23) and **exports yaml-cpp on purpose**: `YAMLParser.hpp` and the robot interface's headers expose its types. Hiding both
  (PR #22) left `libgafro_robot_interface.so` with an undefined `YAML::BadConversion`. If tinyxml2 is exported again, a process that loads the system's copy too (Fast DDS reads its XML profile with it)
  dies with SIGBUS in `rclpy.create_node` whenever a DDS profile is set.
- A quick regression check for that: the arm service on the fake bus with the DDS profile in the environment should run until killed, not exit 135:
  `FASTRTPS_DEFAULT_PROFILES_FILE=/etc/opendubs/fastdds-robot.xml timeout 20 python software/openarm/gafro/arm_service.py run --side left --backend hardware --hardware-config <a file with both arms bus: fake>`.

## When something goes wrong

| What you see | What it is | What to do |
|---|---|---|
| start: `CAN bus 'arm_left' is not healthy` | the CAN controller is `ERROR-WARNING` or `ERROR-PASSIVE`; nothing recovers it | `sudo systemctl restart openarm-can@arm_left` (the old stack's `openarm-up` does this before it activates; it moves nothing, but motors that were enabled read DISABLED afterwards: park first), then start again |
| start: `joint N's position is not confirmed: … in K attempts` | the motor produced fewer than two status frames: the "differed by 0 rad" means it never sent a second one, not that readings disagreed | more polling (`start_interval`, `start_attempts`); check `can_reply_rates.py rates`; look at the motor's connector |
| start: `joint N … did not answer` / `does not answer` | the motor is silent | power (e-stop, contactor), CAN wiring at or before that motor |
| a move stops: `TRACKING: a joint is x rad from the path` | a joint was x rad off its path: on the left arm most likely a link outage mid-move (the driver sent nothing for `link_retry` seconds) | `python3 software/openarm/gafro/tools/move_summary.py /var/tmp/arm-rec`: a block of samples in which every joint's `q`, `qd` and `tau` are constant while `ref` moves is an outage |
| a goal ends at once, `position: 0.0` or `0.044`, no motion | refused: not supervised, deadman released, CH5 off | `journalctl -u opendubs-arm@<side> -n 5 -o cat` |
| `Action servers: 0`, goals never answered | DDS shared memory across users | the DDS lines in `arm-service.env`, then restart the unit |
| service exits at `import gafro_ri_ros2_bridge` | ABI mismatch after a gafro update | rebuild the bridge (above) |
| `terminate called without an active exception`, `ABRT` | see Known problems | with `PYTHONFAULTHANDLER=1` the journal has the Python stacks |
| the arm moved after a start, nothing commanded | a build before openarm-driver `c75b615` held the arm from a register-write acknowledgement (−12.468 rad) during link recovery | `check_stack.sh` says whether the driver has the validated recovery; `can_sniff.py` shows what is commanded |
| `arm viewer` shows nothing | the viewer is not on the service's ROS graph | `arm doctor <side>` |

## Known problems

- **Left joints 1 and 2** (above): a hardware fault nobody has looked at. The left arm runs on settings that tolerate it, with reduced monitoring (silence is noticed after 0.5 s; joint 1 answered 1-3 % of status requests when idle). The first start usually needs a second attempt because the controller is `ERROR-WARNING`; the unit has no link-heal step (the old stack's `openarm-up` has one), so a first press of START on
  the dashboard can fail, and four failures in 120 s leave the unit `failed`.
- **Tracking stops.** Two moves stopped at 0.20 and 0.21 rad, about 3 s into a 5 s move, where the speed peaks. A later recording (`park`, 13:22, run with `--max-error 0.5` so the move went on) shows what that was:
  for 0.5 s, from t=2.47 s, `q`, `qd` and `tau` of **every joint were constant** while the reference kept moving at 0.68 rad/s, the error grew to 0.35 rad, and when the readings came back joint 4 had
  moved only 0.03 rad in that half second: nothing was reaching the motors, and then the arm ramped back onto the path. That is the driver's link recovery: an exchange throws (the left controller counts 60 or
  more bus faults in 10 s and the driver treats an unhealthy bus as a failure), `link_down_` is set, and the loop sends nothing until the next retry, `link_retry` = 0.5 s later. The length of the freeze
  (526 ms) and the way it lines up with the stops fit; the driver's own counter was not read, so this is the best explanation, not a proven one. `link_retry: 0.05` is set on the left as the experiment: the outage should
  shrink to under 0.1 s and the error to a few hundredths of a rad. If it does, that confirms it; the cause underneath is still the left bus's faults. The right arm's bus is `ERROR-ACTIVE` and has not shown this.
- **An abort** (`terminate called without an active exception`, signal 6) ended the left service twice, about 30 s after a tracking stop. Unexplained and not reproduced since; the left unit runs with
  `PYTHONFAULTHANDLER=1`. One lead: a link recovery rebuilds the CAN socket (`CanBus::recover`), and that message is what a C++ thread destroyed while still running prints, so the abort may be in the
  library's socket teardown. Check the journal's Python stacks the next time, and whether a link outage came just before it.
- **Stopping the service does not release the arm.** The motors stay enabled and stiff until the e-stop cycle, and a restart reads the pose and holds it.
- **Never verified end to end:** the dashboard's START / SUPERVISE / PARK for either arm as `kiosk`; a show through the runner on either arm; `follow` on the left; the left gripper holding an object.

## Tools

In `software/openarm/gafro/tools/` (see its README): `check_stack.sh` (is everything current), `can_reply_rates.py` (how often each motor answers, and its registers: read-only), `can_sniff.py` (what is commanded to
the motors and what they answer). They were written to find the faults above and each is read-only.
