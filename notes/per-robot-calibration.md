# Per-robot calibration — recorded values

**This file is a BACKUP, not a source.** These values belong to one physical robot and must never
be copied into another, nor flashed at a robot without verifying them on that robot.

The values live as `static constexpr` in the robot's own copy of `rc_serial_probe/src/main.cpp`,
which is not the repo's copy. `deploy-all.sh` never writes that file: it is on
`tools/deploy-audit.py`'s `PER_ROBOT` list, which the deploy reads. They can still be lost by a hand
copy, by flashing a repo checkout over USB, or by reflashing the Thor. This file is the copy to restore
from.

The real fix is to move them out of committed source into NVS. The firmware already does this for
one thing: the RC map lives in NVS under the `rccfg` namespace (`rc_config.cpp`), set by the
`RCCAL` host command. That is the template.

---

## opendubs2

Read from `/opt/opendubs/rc_serial_probe/src/main.cpp` on 2026-10-01, after recovering the drive
signs and verifying them by driving.

### Drive — `main.cpp` (a per-robot file; deploys skip it)

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

### Also uncommitted on this robot

- **The contactor auto-arm** was an on-robot-only feature and was lost to a flash. It is in the
  repo now (`autoArmTick`), so this particular hole is closed.
- **The PWM mirror** is in the repo (`main.cpp`, off by default) despite
  `deployment-drift.md` listing it as robot-only. That note overstates the drift.

---

## Restoring after an accidental overwrite

```sh
# on the robot, patching rather than copying — the repo's main.cpp has neither value
sudo sed -i 's/kDriveInvert\[4\]   = {1, 1, 1, -1}/kDriveInvert[4]   = {-1, -1, 1, -1}/' \
  /opt/opendubs/rc_serial_probe/src/main.cpp
# kStrafeInvert does not exist in the repo at all — re-add it and apply it in driveMix()
sudo /opt/opendubs/rc_serial_probe/ota-from-thor.sh
```

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
