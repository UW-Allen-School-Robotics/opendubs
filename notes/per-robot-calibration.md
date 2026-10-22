# Per-robot calibration — recorded values

**This file is a BACKUP, not a source.** These values belong to one physical robot and must never
be copied into another, nor flashed at a robot without verifying them on that robot.

The drive values live in **`rc_serial_probe/src/robot_config.h` on the robot**: the per-robot header
`main.cpp` includes, made from the committed `src/robot_config.h.example`. It is never committed and
never deployed (`tools/deploy-audit.py` lists it as `PER_ROBOT`, which the deploy reads), and
`main.cpp` is an ordinary deployed file. Until 2026-10 the values sat inline in each robot's own
`main.cpp`, and `tools/make_robot_config.py` moves them from such a file. The header can still be lost
by reflashing the Thor, so this file is the copy to restore from.

Further still, the values could move into NVS, as the RC map has (`rccfg`, set by `RCCAL`).

---

## opendubs2

Read from `/opt/opendubs/rc_serial_probe/src/main.cpp` on 2026-10-01, after recovering the drive
signs and verifying them by driving.

### Drive — `src/robot_config.h` (per robot; deploys skip it)

| Constant | Value | Notes |
|---|---|---|
| `kDriveCh[4]` | `{1, 3, 0, 2}` | 8Servo channel per wheel, `[FL, FR, RL, RR]` |
| `kDriveInvert[4]` | **`{-1, -1, 1, -1}`** | **Verified by driving.** The repo default `{1, 1, 1, -1}` is the *worked example* in `drive-bringup.md`, not this robot — three of four signs differ |
| `kStrafeInvert` | **`-1`** | **Verified both directions.** This robot's mecanum roller handedness needs strafe negated; the repo's mixer signs alone give inverted strafe |
| `kEscNeutralUs` | `1500` | 2×80A neutral / arm-zone centre |
| `kEscSpanUs` | **`400`** | Raised from the repo's gentle `150` on 2026-10-01, which is what the code's "widen toward 400 once ESCs hold" was waiting for. ±400 → 1100–1900 µs, inside the ESC's ~1050–1950 proportional band. A 2026-09-03 log implies ~500 was once in use, which is *above* this — so "what it used to be" is not reliably known; 400 is the number with reasoning attached |
| `kEscSlewUs` | `12` | max µs step per 20 ms tick — the accel/decel ramp |
| `kEscRevDwellTicks` | `10` | on a direction flip: brake to neutral, then hold |

**How the inverts were recovered**, since the values were lost and had to be reconstructed:
`notes/history/8servo-wedge/README.md:92` recorded `kDriveInvert = {-1,-1,1,-1}` on 2026-09-03, and
the telemetry it annotates confirms it arithmetically — `fwd=-0.87` produced
`out=1935,1935,1071,1942`, i.e. wheels 0/1/3 above neutral and wheel 2 below. Since all four take
the same mix sign for straight travel, 0/1/3 carry `-1` and wheel 2 carries `+1`. That also rules
out the global flip `{1,1,-1,1}`.

### RC map — in NVS, survives a flash

From `RCCAL SHOW`. Columns are min/centre/max.

| Function | Channel | | Endpoints |
|---|---|---|---|
| FWD | ch3 | | 200 / 1000 / 1820 |
| ROT | ch1 | | 200 / 1000 / 1820 |
| STR | ch4 | | 200 / 1000 / 1820 |
| MAST | ch2 | **REV** | 200 / 993 / 1800 |
| AUTH | ch5 | | 200 / 1000 / 1820 |
| DEAD | ch6 | | 200 / 200 / 1800 |
| SPEED | ch8 | | 200 / 1000 / 1820 |
| PMODE | ch7 | | 200 / 1000 / 1820 |

Note `MAST` is reversed and `DEAD` reads 200 at rest — both per-transmitter, both already safe in
NVS rather than in source.

### Pinpoint odometry — in the StamPLC's NVS (`ppcfg`), survives a flash

Commissioned 2026-10-21 by goBILDA's own setup steps (3110-0002-0001 User Guide), at the robot.
The Pinpoint forgets its configuration at every power cycle; the firmware re-applies these from NVS
(`ppcfg.cpp`). They are lost if the StamPLC is **replaced**, or its NVS erased; restore them with
the commands below, then `PPCAL SAVE`.

| Setting | Value | Restore with | How it was established |
|---|---|---|---|
| Pod type | **4-bar, 19.894 ticks/mm** | `PPCAL POD 4BAR` | The pods are goBILDA 4-bar pods (seen at the robot). It had been set to swing-arm (13.263), which read 39% long |
| X pod direction | **REVERSED** | `PPCAL DIRX REV` | Driving forward made X fall (−968 mm for about 1 m) |
| Y pod direction | forward | `PPCAL DIRY FWD` | Driving left made Y rise |
| X pod offset | **48.0 mm** (left +) | `PPCAL OFFSETS 48 -396` | X pod sits left of the centre line (seen at the robot). The value was already set; a one-turn spin confirmed its sign |
| Y pod offset | **−396.0 mm** (fwd +) | (same command) | Y pod sits about 40 cm behind the centre (seen at the robot). A flipped sign would have shown as a ~1.6 m loop in the spin; it did not |

Both offsets are measured from `base_link`, the centre of the wheel rectangle (workplan-thor-stack L.1).

**Results, for comparison after a restore:**

- Stationary heading drift after `PPCAL RESET` (robot still): about 0.25°/min. Before it, 150°/min.
  If heading turns while the robot is still, `PPCAL RESET` with the robot untouched.
- 48 in (1219 mm) on the tape read 1173 mm and, in another run, 1126 mm: 4-8% short, varying run to run.
- Out-and-back loops closed 286 mm short over 4.7 m and 377 mm over 6.6 m (about 6%). No setting
  closes both (gyro scale, either pod's scale, forward-only or reverse-only scale were all fitted),
  so the pods lose counts while driving. Accepted for use as-is on 2026-10-21; check pod contact if it worsens.

### Base speed and the software cap — `/etc/opendubs/base.env`

Read by `opendubs-drive-bridge` and `opendubs-dance-tracker` (`software/ros/drive_scale.py`). Software
(AUTO) motion only; RC driving in MANUAL is not capped by it. To change a value: edit the line, then
`sudo systemctl restart opendubs-drive-bridge opendubs-dance-tracker`.

| Setting | Value | How it was set |
|---|---|---|
| `OPENDUBS_AUTO_CAP` | **0.25** (of full command, on any wheel) | The maintainers' first choice, 2026-10-22; expected to change |
| `OPENDUBS_MAX_LIN` | **0.27** m/s | forward speed at the cap: a 1.5 s burst at the cap, from `/odom`, 2026-10-22 |
| `OPENDUBS_MAX_STRAFE` | **0.18** m/s | strafe speed at the cap; the same left and right |
| `OPENDUBS_MAX_ANG` | **0.37** rad/s | turn rate at the cap (0.36 CCW, 0.38 CW) |
| `OPENDUBS_STRAFE_YAW_BLEED_RIGHT` | **1.46** | strafing RIGHT at 0.18 m/s yawed **−0.262 rad/s** (clockwise, 16.5° in one burst): k = −0.262 / −0.18. Probably mechanical on one side; worth a look |
| other strafe trims (`_YAW_BLEED_LEFT`, `_FWD_BLEED_*`) | 0 (unset) | strafing left yawed under 2°/s; the forward creep was 4% left, 9% right. All under the point where the loop's lag matters (about 2 cm or 3° over a 2 s sway) |

### Robot-side state that turns features on (per robot, not in the repo)

| Path | What it does |
|---|---|
| `/etc/opendubs/drive-bridge.enabled` | Lets `opendubs-drive-bridge` start (`/odom`, and `/cmd_vel` → `DRIVE`). Created 2026-10-21 once the Pinpoint was commissioned |
| `/etc/opendubs/slam.enabled` | Lets `opendubs-slam` start |
| `/var/lib/opendubs/maps/` | Saved SLAM maps, the building plan's rooms and `site.yaml` (which map to localize on, and its fit to the plan). **Site data: never in the repo.** Back it up off the robot; regenerating needs a new mapping drive and `software/ros/floorplan.py fit` against the plan |

### Neck-follow signs — in `/etc/opendubs/robot.env`

| Setting | Value | How it was measured |
|---|---|---|
| `OPENDUBS_NECK_FOLLOW_PAN_SIGN` | **`-1`** | 2026-10-16, at the robot: a pan of 70 turns the head to the **robot's right**, and the face page does not mirror image x. The code's default of +1 turned the head away from people and pinned it at the pan limit |
| `OPENDUBS_NECK_FOLLOW_TILT_SIGN` | `-1` | 2026-10-15, open-loop with both follow ranges at 0 so the head held centre. The code's default is also −1 |

### Also uncommitted on this robot

- **The contactor auto-arm** was an on-robot-only feature and was lost to a flash. It is in the
  repo now (`autoArmTick`), so this particular hole is closed.
- **The PWM mirror** is in the repo (`main.cpp`, off by default) despite
  `deployment-drift.md` listing it as robot-only. That note overstates the drift.

---

## Restoring after an accidental overwrite

Recreate `/opt/opendubs/rc_serial_probe/src/robot_config.h` from `src/robot_config.h.example`, with
the values in the tables above (all of them: the example's values are not this robot's), then
`sudo /opt/opendubs/rc_serial_probe/ota-from-thor.sh`. A robot without the file refuses to build.

Then **verify by driving** rather than trusting this file: forward, rotate, and strafe in *both*
directions, wheels clear of the floor. A sign error that happens to look right one way is the one
that bites later.

---

## Grippers — on the robot only, by design

The two OpenArm grippers were zeroed in different physical states, so the controller position for
"open" and "closed" differs per side and per robot. Shows say `open: 0..1`; the mapping lives in
**`/etc/opendubs/grippers.yaml` on the Thor** (or `$OPENDUBS_GRIPPER_CAL`), measured there, and is
deliberately **not** recorded in this file or anywhere in the repository. Measure and check it with
`software/choreo/grippers.py` (`template`, then `check`) — choreography plan item 6.2.

## Neck tilt band — on the device only, by design

The neck's tilt limits are set when the neck is commissioned on each robot, and they depend on how the
head is mounted. They live in the **neck controller's own NVS** (the M5Dial: namespace `opendubs`,
keys `tiltMin` / `tiltMax`), survive a firmware flash, and are deliberately **not** recorded in this
file or anywhere in the repository, so the number exists in exactly one place. The neck reports them
on request (`NECK` answers `tilt_band` and `commissioned`). How to read, set and commission them is in
`firmware/neck_dial/README.md`.

## Settings file — `/etc/opendubs/robot.env`

Per-robot settings the robot's services load at start (`EnvironmentFile=`): the robot's name, the
face and neck hosts, and any per-robot override. That includes the neck-follow signs
(`OPENDUBS_NECK_FOLLOW_PAN_SIGN`, `OPENDUBS_NECK_FOLLOW_TILT_SIGN`), which depend on how the head and
neck are mounted and are measured at each robot, open-loop with the head held still. The code's
defaults are not a measurement. Deploys only **read** it, never write it, and
`tools/deploy-audit.py` does not compare it, so a change to it never shows as drift. A temporary line
added for a measurement therefore stays, and is re-applied by every restart, until it is removed by
hand. Edits go through the Thor stack lane, with a dated backup beside the file.
