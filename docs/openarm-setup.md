# OpenArm setup

How to run two [OpenArm](https://github.com/enactic/OpenArm) 7-DOF arms from the Thor. It covers
the host side (the CAN adapter driver, stable interface names, and bringing the links up at boot),
the container the arm software runs in, and the `openarm-up` command that starts, powers and
stops the arms.

!!! warning "Arms move when they are activated"
    When an arm is activated, the arm software **moves it to its zero pose over about 2 s**. Clear
    the arms' workspace before every activation. When an arm is deactivated or loses power, its
    motors go limp and the arm **drops under gravity**. If the arms draw power from the drive bus,
    which is switched by the [e-stop](e-stop.md) contactor, an e-stop drops them too.

## Prerequisites

- A Thor set up per [Thor setup](thor-setup.md) (JetPack 7, kernel `6.8.12-tegra`).
- Two OpenArm arms. Each arm has 8 DAMIAO motors (7 joints plus the gripper) on its own CAN-FD bus.
- A **PEAK PCAN-USB Pro FD** adapter (USB ID `0c72:0011`), with one channel per arm, plugged into
  the Thor's USB.
- The arm software and IK library are pinned submodules of this repo — see
  `software/openarm/{openarm_robot-code,ssik}/` — so there's nothing to clone separately. They come
  in with a normal checkout of `opendubs`:
    - [openarm_robot-code](https://github.com/EricJGoossen/openarm_robot-code), a fork of
      [personalrobotics/robot-code](https://github.com/personalrobotics/robot-code). Provides the
      ROS 2 impedance controller (package `openarm_impedance_controller`), the dev-container
      Dockerfile, and the `scripts/{build,bake}-image.sh` used below.
    - [ssik](https://github.com/personalrobotics/ssik), the inverse-kinematics library the dev
      container mounts in.

## How it fits together

| Layer | What | Where |
|---|---|---|
| Driver | `peak_usb`, built by **DKMS** (it rebuilds the module automatically when the kernel changes) | `/usr/src/peak_usb-<ver>` |
| Interface names | a udev rule names the adapter's channel 0 `arm_left` and channel 1 `arm_right` | `/etc/udev/rules.d/70-openarm-can.rules` |
| Link bring-up | `openarm-can@<name>.service` sets 1 Mbit/s nominal / 5 Mbit/s data CAN-FD and raises the link whenever the channel appears | `/etc/systemd/system/` |
| Arm software | ROS 2 Jazzy + the impedance controller, in the `openarm` Docker container | image `openarm-ros2:<robot>` |
| Operator commands | `openarm-up`, `openarm-motors` | `/usr/local/bin` |

Why fixed names: the Thor's on-board CAN controllers (`mttcan`) are a loadable module, so the
kernel's `canN` numbering for the USB adapter can change between boots. Naming the channels by
their hardware index keeps "left" meaning left.

## Setup (once per robot)

`deploy-all.sh`, run from a dev machine, syncs `software/openarm/` — including the pinned
`openarm_robot-code` and `ssik` submodules — to `/opt/opendubs/openarm`. Then, on the Thor:

```bash
sudo /opt/opendubs/openarm/openarm-bringup.sh
```

This is setup, not something you run to start a session — it's idempotent (each step below skips
itself if already done) but installs drivers and bakes an image, which day-to-day operation
shouldn't repeat. It runs, in order:

1. **`provision-openarm.sh`** — builds and installs `peak_usb` with DKMS (source: the unmodified
   upstream driver for your kernel version, since the Jetson kernel ships without USB-CAN drivers;
   skipped if your kernel already provides it), installs the link service and default channel
   names (**only if the robot doesn't already have a `70-openarm-can.rules`** — see below),
   and installs `openarm-up` / `openarm-motors`.
2. **`openarm_robot-code/scripts/bake-image.sh`** — runs the workspace's own `setup.sh` (clones the
   sibling ROS/Python packages it builds from), builds the dev-container image, builds the
   workspace inside a throwaway container the same way a devcontainer's `post-create.sh` would,
   commits the result as `openarm-ros2:$(hostname)`, and copies `build`/`install`/`log` out to
   `/opt/opendubs/openarm/ws_state` so a rebuild survives the container being recreated.
3. Creates the long-running `openarm` container (`--restart unless-stopped --network=host`,
   mounting the workspace, `ssik`, and `ws_state`) that `openarm-up` expects — stopped; `openarm-up`
   starts it.

Check the result:

```bash
ip -details link show arm_left     # expect: UP, <FD>, state ERROR-ACTIVE, bitrate 1000000, dbitrate 5000000
openarm-motors                     # expect: all 16 motors answer, DISABLED
```

### Left and right are per-robot

Which adapter channel is wired to which arm depends on each robot's wiring, so the mapping lives
in the robot's own `/etc/udev/rules.d/70-openarm-can.rules`, not in the repo. The default is
channel 0 (`dev_id 0x0`) → `arm_left`. If your arms come up swapped, swap the two `NAME=` values
(and the matching `openarm-can@…` names) in the robot's copy, then unplug and replug the adapter or
reboot. Confirm with `openarm-motors`: moving the left arm by hand should change the positions
under `arm_left`.

### Arms start inactive by default

`openarm_impedance_control/config/openarm_impedance_controllers.yaml` ships with, under
`controller_manager: ros__parameters:`:

```yaml
hardware_components_initial_state:
  inactive:
    - openarm_left_hardware_interface
    - openarm_right_hardware_interface
```

Without this, `ros2_control` activates hardware the instant the stack launches, so the arms would
power up and move to zero with no operator action. `openarm-up` checks for this setting too and
stops the stack immediately if an arm comes up active on its own — that check is a backstop, not a
substitute for the config being right.

## Operate

| Command | What it does |
|---|---|
| `openarm-up` | Checks both links and the container, launches the stack, and waits until both arms report **inactive**: motors off, arms limp |
| `openarm-up activate left` (or `right`, `both`) | Powers the arm, checks all 8 of its motors report enabled, then starts its controller. **The arm moves to zero over ~2 s.** |
| `openarm-up down` | Stops the controllers, makes the arms inactive (they go limp), stops the stack, and confirms every motor is disabled. If any motor stays enabled, it sends DISABLE frames directly. |
| `openarm-up status` | Links, container, stack, hardware and controller states, and each motor's status and temperature |
| `openarm-up logs` | Follows the current launch log |
| `openarm-motors` | Reads all 16 motors directly over CAN, whether or not the stack is running. It can only send the motors' read-only status query and the DISABLE command. |

With both controllers active, the arms accept `control_msgs/FollowJointTrajectory` goals on
`/left_controller/follow_joint_trajectory` and `/right_controller/follow_joint_trajectory`.

To work inside the container: `sudo docker exec -it openarm bash`, then
`source /opt/ros/jazzy/setup.bash && source /openarm_ws/install/setup.bash`.

## Troubleshooting

- **`arm_left` / `arm_right` missing.** Check `lsusb -d 0c72:0011` (is the adapter plugged in?) and
  `modinfo -n peak_usb` (is the driver installed?). After a kernel update, run
  `dkms status peak_usb`. If the build failed against the new kernel, re-run
  `provision-openarm.sh`, which fetches driver source matching the new version.
- **A link is not `ERROR-ACTIVE`.** Restart it with `sudo systemctl restart openarm-can@arm_left`.
  `BUS-OFF` usually means a wiring or termination fault, or an unpowered arm.
- **A motor shows `no reply`.** The arm is unpowered, or its CAN wiring is broken at or before
  that motor.
- **Never send the DAMIAO set-zero command (`0xFE`).** It redefines that joint's zero as wherever
  the joint happens to be, and the zero pose is what activation moves the arm to.
- **Redeploying (`deploy-all.sh`) wipes `ws_state`.** It shouldn't — the `openarm` `deploy_sub` call
  excludes it — but if you're on a version of `deploy-all.sh` predating that exclude, a redeploy
  will delete your baked `build`/`install`/`log` and force a full rebake.