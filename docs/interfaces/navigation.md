# Navigation interface

This page is the contract for anyone building their own navigation, mapping or localization on the
robot: what the robot publishes, what it guarantees, and how a program asks to move the base. It
describes the interface, not the robot's internals. Everything here is ROS 2 Jazzy.

The robot ships a small navigation stack of its own: odometry, mapping and localization, a guard
that keeps the base off people, and a tracker that moves the base during shows. Your stack can use
any of its outputs, replace the mapping and localization outright, and move the base through the
same door every other program uses.

!!! warning "Work on the bench first"
    Develop and test on a bench computer (a spare Thor or a laptop with ROS 2 Jazzy), against recorded
    data or the simulator. **The robot runs only what is merged to the repository's `main` branch**:
    nothing is deployed to it from a personal branch, and changes to its configuration are made by the
    maintainers.

## The rules of motion

1. **Who may drive is a switch on the transmitter (CH5), not a software setting.** In MANUAL the RC
   transmitter drives and every software command is ignored. In AUTO, software drives and the sticks
   do not. RC and software never compete, so there is no priority to design between them. See
   [Control authority](../control-authority.md).
2. **In AUTO, exactly one program owns the base at a time.** It claims the base, keeps the claim
   alive, and releases it. Other programs are refused until then. A show and your navigation stack
   cannot both be driving. [How to claim it](#moving-the-base) is below.
3. **Nobody writes to the wheels directly.** Commands go to your own topic, and only the current
   owner's topic is forwarded. There is no `/cmd_vel` that drives the robot.
4. **The same gates apply to every owner**, and none of them can be turned off from ROS:

    | gate | where | what it does |
    |---|---|---|
    | AUTO + deadman | robot firmware | software moves the base only in AUTO **while a person holds the deadman switch (CH6)**. Releasing it stops the base |
    | speed ceiling | robot firmware | in AUTO a full command is 0.35 of the base's full speed, scaled further by the CH8 knob |
    | command timeout | firmware and the drive bridge | a command older than 0.25–0.5 s stops the base. Keep publishing while you move |
    | people guard | the drive bridge | refuses motion toward anything within 0.5 m of the robot's outline, seen by either LIDAR (any side while turning). If a LIDAR stops publishing, **all** software motion is refused |
    | one writer | the drive bridge | if anything but the base selector writes to the drive bridge's input, all motion is refused |

    The people guard sees only the LIDARs' plane, about 0.33 m above the floor. It does not see glass,
    table tops, overhangs or drops. **It is not a safety system**: the person on the deadman is.

## What the robot publishes

| name | type | rate | guarantee | owner |
|---|---|---|---|---|
| `/odom` | `nav_msgs/Odometry` (best effort) | ~8.7 Hz | the dead-wheel odometry pod's pose and velocity. Pose in `odom`; **twist in `base_link`** (the body frame). Published only while the pod's last read succeeded: a stale pose is dropped, not repeated. Stamped on arrival at the robot computer. Covariances are placeholders, not measured | the drive bridge |
| TF `odom → base_link` | `tf2` | with `/odom` | continuous, never jumps. Can be switched off for a stack that fuses its own (below) | the drive bridge |
| TF `base_link → laser`, `base_link → laser_rear`, the wheels and the rest of the chassis | `/tf_static` | static | from the robot description, `description/opendubs/dimensions.yaml`. The moving joints (lift, neck) have no joint states yet, so frames below them are absent | `robot_state_publisher`, in `/opendubs` |
| `/scan` | `sensor_msgs/LaserScan` | ~7 Hz | the front LIDAR, frame `laser`. Includes returns from the robot's own frame within about 0.3 m | LIDAR driver |
| `/scan_rear` | `sensor_msgs/LaserScan` | ~7 Hz | the rear LIDAR, frame `laser_rear`. Same caveats | LIDAR driver |
| `/scan_filtered` | `sensor_msgs/LaserScan` | ~7 Hz | `/scan` with the robot's own returns removed (anything inside the chassis outline plus 10 cm). Only while our mapping runs | our mapping |
| `/map` | `nav_msgs/OccupancyGrid` | on change | 5 cm cells, from the front LIDAR. **Ours: off when you turn our mapping off** | our mapping |
| TF `map → odom` | `tf2` | ~50 Hz | allowed to jump (localization corrections). **Ours: off with our mapping** | our mapping |
| TF `<floor> → map`, `/floor/rooms` | `tf2`, `visualization_msgs/MarkerArray` | static, latched | where our map sits on a building floor plan, and the plan's room numbers as labels. Only on a robot with a fitted plan. **Off with our mapping** | our mapping |
| `/base/owner` | `std_msgs/String`, JSON | 2 Hz, latched | who owns the base (below) | the base selector |

Frames follow REP 103 and REP 105: `base_link` is the centre of the rectangle formed by the four
wheels, at axle height, with x forward, y left and z up. `base_footprint` is the same point on the
floor. See [Coordinate frames & naming](../naming-frames.md).

Both LIDARs are mounted with their 0° facing **the robot**: the front unit is turned 180° and the
rear one is not. Read their pose from TF, never from this page.

## Moving the base

The base selector holds a **lease** on the base. A program claims it under a name of its own, keeps
the claim alive, sends commands on its own topic, and releases it when done.

| topic | type | you | meaning |
|---|---|---|---|
| `/base/claim` | `std_msgs/String`, JSON `{"owner": "nav", "ttl_s": 2.0}` | publish, at least every `ttl_s` | claim the base, or renew your claim. Granted when the base is free, already yours, or the holder's lease has run out. **Never taken from a live holder.** `ttl_s` is capped at 5 s |
| `/base/release` | `std_msgs/String`, JSON `{"owner": "nav"}` | publish when done | give the base back at once |
| `/base/owner` | `std_msgs/String`, JSON `{"owner": "nav", "expires_in_s": 1.6, "refused": null}` | subscribe (transient local) | the current owner (`null` when free), and the last refused claim with its reason |
| `/base/<owner>/cmd_vel` | `geometry_msgs/Twist` | publish while you move | your commands, in `base_link`: `linear.x` forward and `linear.y` left in m/s, `angular.z` in rad/s. Only the owner's topic is forwarded |

Owner names are lower case letters, digits and underscores, starting with a letter. The robot's
own show tracker uses the name `show`. Pick a name of your own.

**A program that dies frees the base** when its lease runs out, within `ttl_s`. The base itself
stops much sooner, because its commands stop. A program that is alive but stuck holds the base until
it releases it. The robot's operator can free it with the release message `{"owner": "*"}`.

**Handover is release, then claim.** When the base changes hands or is freed, the selector sends
one zero command first, so the base stops before the next owner drives it.

Scaling: a Twist is converted to the firmware's command range against the speed the base reaches at
a full software command, so request real m/s and rad/s. Anything above the ceiling is clipped.
**That speed has not been measured yet.** Until it is, the conversion uses a conservative placeholder
(0.5 m/s, 1.0 rad/s at full command), so the base may move slower than you ask.

A navigation stack that publishes `/cmd_vel` (Nav2's default) needs one remap,
`/cmd_vel:=/base/<owner>/cmd_vel`, plus a small node that claims the base on start, renews it, and
releases it on shutdown. Nothing consumes `/cmd_vel`, and the selector logs a warning when something
publishes there.

```bash
# claim, drive forward slowly for a moment, release (AUTO, deadman held, clear space)
ros2 topic pub --once -w 1 /base/claim std_msgs/msg/String '{data: "{\"owner\": \"nav\", \"ttl_s\": 5}"}'
ros2 topic pub -w 1 -r 20 -t 20 /base/nav/cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.1}}'
ros2 topic pub --once -w 1 /base/release std_msgs/msg/String '{data: "{\"owner\": \"nav\"}"}'
```

## Replacing our mapping and localization

Our mapping and localization (`/map`, `map → odom`, the floor-plan frame) run only where the file
`/etc/opendubs/slam.enabled` exists. To run your own SLAM or localization instead, ask the maintainers
to remove that file. The robot's operating account has no administrator rights, so you can't do it
yourself. That stops all three, and your stack can then publish `map → odom` and its own `/map`.

Odometry stays. If your stack fuses its own `odom → base_link` (for example a `robot_localization`
EKF over `/odom` and an IMU), ask for the drive bridge's transform to be switched off
(`OPENDUBS_ODOM_TF=false`). `/odom` itself keeps publishing. Two publishers of one transform make it
jump between them, so exactly one of the two must publish it.

| you want to | ask for | the robot then |
|---|---|---|
| use our map and localization | nothing | publishes `/map`, `map → odom` and `odom → base_link` |
| run your own SLAM or localization | `slam.enabled` removed | publishes `odom → base_link` and `/odom`. `map → odom` is yours |
| fuse your own odometry too | that, plus `OPENDUBS_ODOM_TF=false` | publishes `/odom` and the static frames only |

## Namespaces

The robot's own nodes and internal topics live under `/opendubs`. Treat anything there as private:
it can change without notice. The names on this page are the public interface. Put your nodes and
topics under a namespace of your own (for example `/student_nav`), use your own owner name, and don't
publish to any name on this page except `/base/claim`, `/base/release` and `/base/<your owner>/cmd_vel`.

## Limits worth knowing

- The odometry is good over short distances and drifts over long ones. A drive that returns to its
  start ends a few percent of the distance driven away from it.
- Our map uses the front LIDAR only. The LIDARs reach about 8 m usefully indoors, and they see through
  glass.
- The base is holonomic (mecanum wheels): it can strafe, but strafing is the least accurate motion.
