# OpenArm setup

How to run two [OpenArm](https://github.com/enactic/OpenArm) 7-DOF arms from the Thor. It covers
the host side (the CAN adapter driver, stable interface names, and bringing the links up at boot),
the container the arm software runs in, and the `openarm-up` command that starts, powers and
stops the arms.

!!! warning "Arms move when they are activated"
    When an arm is activated, the arm software **moves it to its zero pose over about 2 s**. Clear
    the arms' workspace before every activation. When an arm is deactivated or loses power, its
    motors go limp and the arm **drops under gravity**. The arms are on the drive bus, which is
    switched by the [e-stop](e-stop.md) contactor, so an e-stop drops them too — stopping all driven
    motion with the e-stop is foundational and is not configurable.

## Prerequisites

- A Thor set up per [Thor setup](thor-setup.md) (JetPack 7, kernel `6.8.12-tegra`), with internet
  access for the first provisioning run.
- Two OpenArm arms. Each arm has 8 DAMIAO motors (7 joints plus the gripper) on its own CAN-FD bus.
- A dual-channel USB CAN-FD adapter, one channel per arm, plugged into the Thor's USB. This robot
  uses a [Pibiger SavvyCAN-FD-X2](https://www.amazon.com/dp/B0CGPRPZN8). Over USB it identifies
  itself as a PEAK PCAN-USB Pro FD (USB ID `0c72:0011`, manufacturer "PEAK-System Technik GmbH"),
  which is why the stock Linux `peak_usb` driver runs it and everything below applies unchanged.
  The udev rule and provisioning match it by that ID.
- The arm software: [openarm_robot-code](https://github.com/EricJGoossen/openarm_robot-code), a
  fork of [personalrobotics/robot-code](https://github.com/personalrobotics/robot-code). It provides
  the ROS 2 impedance controller (package `openarm_impedance_controller`) and a dev-container
  Dockerfile.

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

## 1 · Provision the host

`deploy-all.sh` copies `software/openarm/` to `/opt/opendubs/openarm`. Then, on the Thor:

```bash
sudo /opt/opendubs/openarm/provision-openarm.sh
```

The script is idempotent. It:

1. Builds and installs `peak_usb` with DKMS. The source is the unmodified upstream driver for your
   kernel version (`drivers/net/can/usb/peak_usb` from Linux `v<version>`). The Jetson kernel ships
   without USB-CAN drivers. If your kernel already provides `peak_usb`, this step is skipped.
2. Installs the link service, and the default channel names **only if the robot doesn't already
   have a `70-openarm-can.rules`**.
3. Installs `openarm-up` and `openarm-motors`.

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

## 2 · Build the arm container

Clone the arm software on the Thor and run its `setup.sh`, which clones the sibling repos it
builds from (the controllers, robot description, planners and assets). The dev container also
mounts the [ssik](https://github.com/personalrobotics/ssik) inverse-kinematics library from the
folder next to the repo, so clone that too:

```bash
mkdir -p ~/openarm && cd ~/openarm
git clone https://github.com/EricJGoossen/openarm_robot-code
git clone https://github.com/personalrobotics/ssik
cd openarm_robot-code && ./setup.sh
```

Build the image from the repo's dev-container Dockerfile:

```bash
sudo docker build --network=host -t openarm-ros2:base .devcontainer
```

Start a temporary container, then install the workspace dependencies and build. ROS must be
sourced before the repo's `post-create.sh` runs, or colcon can't find `ament_cmake`:

```bash
sudo docker run -d --name openarm-setup --privileged --network=host \
  -v ~/openarm/openarm_robot-code:/openarm_ws/src -v ~/openarm/ssik:/openarm_ws/ssik \
  openarm-ros2:base sleep infinity
sudo docker exec -it openarm-setup bash -c \
  "apt-get update && source /opt/ros/jazzy/setup.bash && bash /openarm_ws/src/.devcontainer/post-create.sh"
```

`post-create.sh` installs the workspace's ROS dependencies (`rosdep`) into the container. To keep
them, commit the container as the robot's image. Copy the build out to the host too, so a rebuild
survives the container being recreated:

```bash
sudo docker stop openarm-setup
sudo docker commit openarm-setup openarm-ros2:$(hostname)
mkdir -p ~/openarm/ws_state
for d in build install log; do sudo docker cp openarm-setup:/openarm_ws/$d ~/openarm/ws_state/; done
sudo chown -R $USER: ~/openarm/ws_state
sudo docker rm openarm-setup
```

Create the long-running container. `openarm-up` expects the name `openarm` and these mount points:

```bash
sudo docker run -d --name openarm --restart unless-stopped --privileged --network=host \
  -e DISPLAY -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v ~/openarm/openarm_robot-code:/openarm_ws/src \
  -v ~/openarm/ssik:/openarm_ws/ssik \
  -v ~/openarm/ws_state/build:/openarm_ws/build \
  -v ~/openarm/ws_state/install:/openarm_ws/install \
  -v ~/openarm/ws_state/log:/openarm_ws/log \
  -w /openarm_ws/src openarm-ros2:$(hostname) sleep infinity
```

`--network=host` gives the container the host's `arm_left` / `arm_right` interfaces. With
`--restart unless-stopped`, the container comes back after every reboot.

### Start the arms inactive

By default, ros2_control activates hardware as soon as the stack launches, so the arms would power
up and move to zero with no operator action. Make activation a deliberate step: in
`openarm_impedance_control/config/openarm_impedance_controllers.yaml`, under
`controller_manager: ros__parameters:`, add:

```yaml
hardware_components_initial_state:
  inactive:
    - openarm_left_hardware_interface
    - openarm_right_hardware_interface
```

`openarm-up` checks this setting. If an arm comes up active on its own, it stops the stack
straight away.

## 3 · Operate

| Command | What it does |
|---|---|
| `openarm-up` | Checks both links and the container, launches the stack, and waits until both arms report **inactive**: motors off, arms limp |
| `openarm-up activate left` (or `right`, `both`) | Powers the arm, checks all 8 of its motors report enabled, then starts its impedance controller **and its gripper controller**. **The arm moves to zero over ~2 s.** |
| `openarm-up park left` (or `right`, `both`) | Moves the arm to its configured **stow pose**, so that de-energizing it is harmless. Explicit, like activation, and for the same reason: it moves the arm |
| `openarm-up hold left` (or `right`, `both`) | Drives the arm to **where it already is**. That reads like a no-op and is not: sending a trajectory preempts whatever trajectory is running and leaves the arm commanded to its present pose — *stop what you are doing without dropping anything* |
| `openarm-up down` | Stops the controllers, makes the arms inactive (they go limp), stops the stack, and confirms every motor is disabled. If any motor stays enabled, it sends DISABLE frames directly. **Refuses while an arm is active** — see below |
| `openarm-up status` | Links, container, stack, hardware and controller states, and each motor's status and temperature |
| `openarm-up logs` | Follows the current launch log |
| `openarm-motors` | Reads all 16 motors directly over CAN, whether or not the stack is running. It can only send the motors' read-only status query and the DISABLE command. |

With both controllers active, the arms accept `control_msgs/FollowJointTrajectory` goals on
`/left_controller/follow_joint_trajectory` and `/right_controller/follow_joint_trajectory`.

### Why the gripper controller is started too

The WebXR teleop opens a `GripperCommand` action client **per arm** and will not start without one,
failing ~30 s in with `Timed out waiting for left gripper action server` — a message that names the
wrong thing and says nothing about controllers.

Two distinct states had to be handled, because they need different commands:

- `left_gripper_controller` is spawned but **inactive** → `switch_controllers --activate`.
- `right_gripper_controller` is declared in the controller YAML but **never spawned** →
  `load_controller --set-state active`.

!!! warning "`switch_controllers` is atomic"
    A single call naming several controllers fails **entirely** if any one of them is unknown, and
    leaves the others untouched. Asking for both grippers at once therefore failed on the right and
    left the **left** one inactive as well — which reads as the left gripper being broken rather
    than the right being absent. `openarm-up` now brings up one controller per call, always.

A gripper that will not activate is a **warning, not a fatal error**: the arm is usable without it.
But the warning says what it costs, so the teleop's timeout is never the first anyone hears of it.

## The arm stack and the host must share an RMW

The container image bakes `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` into its environment. The host
runs ROS 2 Jazzy's **default FastDDS** and does not have CycloneDDS installed. Cross-vendor DDS
discovery is not a supported ROS 2 configuration, so with the two mismatched **the host sees no arm
topics at all** — no `/joint_states`, no `/controller_manager`, nothing for `rosbridge` or Foxglove
to subscribe to.

`openarm-up` therefore runs the stack under **`rmw_fastrtps_cpp`**, passed per-`docker exec` rather
than by recreating the container, so the image stays as upstream built it and the choice is one
variable:

```sh
OPENARM_RMW=rmw_cyclonedds_cpp openarm-up      # go back, if arm control misbehaves
```

FastDDS is chosen for the container rather than CycloneDDS for the host because it is already
installed there, while changing the host would alter the transport under a live dashboard, LIDAR and
StamPLC bridge. The arm stack's `hw_validation` stages were run under CycloneDDS, so that override
is the first thing to try if the arms behave differently.

!!! warning "A mismatch silently defeats the bring-down checks"
    `openarm-up` reads hardware and controller state over ROS. Under a mismatched RMW those reads
    return **empty**, not an error — so `openarm-up status` shows a blank state, and `down` sees
    nothing active, skips deactivating the controllers and hardware, and stops the stack with the
    arms still energized. The guard cannot see what it is guarding. If `status` shows a running
    stack but blank hardware states, suspect the RMW before anything else.

## Running teleop from the dashboard

Setting up and operating a teleop session — the headset, certificates, controls and what working
looks like — is on [WebXR teleop](teleop-setup.md). Every tablet arm button and message is on
[Arm controls on the tablet](arm-dashboard.md). This section covers how the bridge is run.

The WebXR bridge runs as **`opendubs-teleop.service`**, and the dashboard's TELEOP page starts and
stops it. A unit rather than something the dashboard spawns directly, because the face tracker was
spawned directly once and it went badly: a second copy fought the service, failures were silent,
and anything started at boot was invisible to the button. Under systemd there is one instance,
`systemctl status` is the truth, and the journal carries the loop-overrun warnings — which the
page reads and displays, since that number is the difference between *"teleop feels vague"* and
*"the loop is missing 23% of its deadlines"*.

The unit sets `OPENARM_TELEOP_VIEWER=0` and the RMW/FastDDS variables, matching what `openarm-up`
does. The viewer is off because it took about two thirds of the control loop's time, and with it on
the loop missed around a quarter of its deadlines; the robot runs headless.

It is **`Restart=no`** on purpose: a teleop bridge that resurrects itself after the operator
stopped it is a session they did not ask for, with an arm attached.

!!! warning "It does not start the arms"
    The bridge opens a `GripperCommand` action client per arm and hard-fails in about 30 s without
    them. `openarm-up activate` comes first and stays a separate, explicit step.

The dashboard's arm buttons all shell out to `openarm-up` rather than talking to ros2_control
themselves, so its guards stay in one place. That needs sudoers rules — see
`kiosk-dashboard/deploy/opendubs-sudoers`; without them the buttons report
`sudo: a password is required` on screen rather than failing quietly.

## Bringing the arms down without dropping them

`inactive` removes torque, so an arm that is brought down while extended **falls from wherever it
is**. `openarm-up down` therefore refuses while either arm is active, prints the arm's current motor
positions, and points at the two ways forward:

```
openarm-up park both        # move somewhere it can safely be de-energized, then
openarm-up down
openarm-up down --drop      # or state plainly that it is already safe to drop
```

Parking is a separate, explicit step rather than part of `down`, because **moving an arm on the way
down is exactly as hazardous as moving it on the way up**. Clear the workspace first, the same as
for activation.

### Configuring the stow pose

```ini
# /etc/opendubs/openarm-stow.conf — joint angles in DEGREES
LEFT_STOW=0,-20,0,95,0,15,0
RIGHT_STOW=0,-20,0,-95,0,15,0
```

Three things about this file:

- **It is in `/etc/opendubs/`, not `/opt/opendubs/`.** The deploy rsyncs `--delete` over
  `/opt/opendubs/openarm`, which would delete it on the next deploy. Override the path with
  `$OPENARM_STOW_CONF`.
- **It ships unset, and `park` refuses rather than guessing.** A wrong stow pose drives the arm into
  the robot's own head. Jog the arm to a pose it can hang limp in, read the angles off
  `openarm-motors status`, and write them down. Degrees, so the two can be compared by eye.
- **The pose is per robot**, since it depends on how the arms are mounted and what is around them.

`park` reads the joint names from the controller rather than assuming them, and refuses if the
number of angles does not match the number of joints. Travel time defaults to 4 s
(`$OPENARM_PARK_SECS`).

### Park before leaving the robot, on the gafro path too

The arm layer that runs on gafro with no ROS (`software/openarm/gafro/`, `notes/design/arm-gafro.md`) has a **ready** pose, with the elbow bent, that it works from. It is not a
resting pose: an e-stop or a power drop there lets the forearm fall, as with any extended arm. **Park before leaving the robot** (`engage.py --park`, which goes to the same stow
pose as above), and never leave the arms at ready. It runs only with the existing stack down, CH5 in MANUAL or AUTO, and `--supervised`.

The arm service that runs it as a systemd unit (`opendubs-arm@left|right`), how to rebuild the stack under it and what is known to be wrong are in [openarm-arm-service.md](openarm-arm-service.md).

To work inside the container: `sudo docker exec -it openarm bash`, then
`source /opt/ros/jazzy/setup.bash && source /openarm_ws/install/setup.bash`.

## Troubleshooting

- **On the gafro path** (`opendubs-arm@…`): see [openarm-arm-service.md](openarm-arm-service.md); everything below is the old ROS stack.
- **`arm_left` / `arm_right` missing.** Check `lsusb -d 0c72:0011` (is the adapter plugged in and
  reporting the ID the udev rule matches? If `lsusb` shows it under another ID, the rule needs that
  ID) and
  `modinfo -n peak_usb` (is the driver installed?). After a kernel update, run
  `dkms status peak_usb`. If the build failed against the new kernel, re-run
  `provision-openarm.sh`, which fetches driver source matching the new version.
- **A link is not `ERROR-ACTIVE`.** Restart it with `sudo systemctl restart openarm-can@arm_left`.
  `BUS-OFF` usually means a wiring or termination fault, or an unpowered arm.
- **A motor shows `no reply`.** The arm is unpowered, or its CAN wiring is broken at or before
  that motor.
- **Never send the DAMIAO set-zero command (`0xFE`).** It redefines that joint's zero as wherever
  the joint happens to be, and the zero pose is what activation moves the arm to.
