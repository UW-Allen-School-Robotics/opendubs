"""Software motion's speed cap, and the conversion of a base command into the StamPLC's DRIVE units.

Software (AUTO) motion is capped at AUTO_CAP of FULL COMMAND, per robot, in /etc/opendubs/base.env
(OPENDUBS_AUTO_CAP, default 0.25). The cap is software only: the RC transmitter in MANUAL is not
limited by it. Raising or lowering it is a one-line edit and a restart of opendubs-drive-bridge and
opendubs-dance-tracker; no firmware is involved.

How it is applied. In AUTO the firmware scales every host DRIVE value by its own ceiling (kAutoMaxUnit,
0.35 of full command in firmware/rc_serial_probe/src/main.cpp) and by the CH8 knob. So a cap of 0.25 of
full command is DRIVE 0.25 / 0.35 = 0.71 at CH8 max, and less at a lower knob.

It caps WHEELS, not axes. On a mecanum base forward, strafe and turn add at each wheel, so capping each
axis alone would let a diagonal-plus-turn command drive a wheel well past the cap. The command is
therefore scaled, keeping its direction, until |forward| + |strafe| + |turn| (each in units of its own
full scale) is at most 1, and only then multiplied by the cap. No wheel can exceed the cap.

Full scales. OPENDUBS_MAX_LIN, OPENDUBS_MAX_STRAFE and OPENDUBS_MAX_ANG are the speeds the robot reaches
at the CAP (unit 1.0 on that axis alone): measured on each robot with short bursts at the cap, never at
full command. A command asking for more than the cap gets the cap.

Strafe trim (optional, default 0): a mecanum strafe can bleed into forward motion and yaw, and not
necessarily the same way in each direction (opendubs2 yaws clockwise strafing right and barely at all
strafing left). The dance tracker corrects that in closed loop; a trim adds feed-forward when a robot's
measured bleed is large enough for the loop's lag to matter. Each is k in "bleed = k x strafe
velocity" (ROS signs: +y left, +yaw counter-clockwise), set per direction:
  OPENDUBS_STRAFE_YAW_BLEED_LEFT / _RIGHT   rad/s of yaw per m/s of strafe
  OPENDUBS_STRAFE_FWD_BLEED_LEFT / _RIGHT   m/s forward per m/s of strafe
  OPENDUBS_STRAFE_YAW_BLEED / _FWD_BLEED    both directions at once (a per-direction value wins)

Pure Python: no ROS, so it can be tested on a laptop.
"""
FW_AUTO_MAX_UNIT = 0.35    # the firmware's kAutoMaxUnit: keep in step with rc_serial_probe main.cpp
DEFAULT_AUTO_CAP = 0.25    # fraction of full command; per robot in /etc/opendubs/base.env


def l1_limit(a, b, c):
    """(a, b, c) scaled down, keeping direction, so that |a| + |b| + |c| <= 1."""
    s = abs(a) + abs(b) + abs(c)
    return (a, b, c) if s <= 1.0 else (a / s, b / s, c / s)


def drive_units(vx, vy, wz, full_x, full_y, full_w, cap=DEFAULT_AUTO_CAP, fw_max=FW_AUTO_MAX_UNIT):
    """(fwd, rot, strafe) in DRIVE's range from a body command in m/s and rad/s, STILL IN ROS'S SIGNS
    (+strafe left, +rot counter-clockwise): to_firmware() converts it for the DRIVE line.

    full_* are the speeds at the cap. The result never makes any wheel exceed `cap` of full command."""
    def u(v, full):
        return v / full if full > 0 else 0.0
    fwd, strafe, rot = l1_limit(u(vx, full_x), u(vy, full_y), u(wz, full_w))
    k = min(1.0, cap / fw_max) if fw_max > 0 else 0.0
    return fwd * k, rot * k, strafe * k


def to_firmware(fwd, rot_ccw, strafe_left):
    """The firmware's DRIVE sign convention from ROS's (REP 103: +y left, +yaw counter-clockwise).

    The StamPLC's DRIVE <fwd> <rot> <str> takes +rot as ROTATE RIGHT (clockwise from above) and +str as
    STRAFE RIGHT, the same as its RC sticks (main.cpp: driveMix drives the left wheels forward on
    +rot; [DRIVE] telemetry reads "+1 right"). So turn and strafe flip; forward does not."""
    return fwd, -rot_ccw, -strafe_left


def strafe_trim(vx, vy, wz, fwd_bleed=(0.0, 0.0), yaw_bleed=(0.0, 0.0)):
    """Feed-forward against a strafe's bleed into forward motion and yaw.

    fwd_bleed and yaw_bleed are (left, right) pairs, or a single number for both: the k in
    "bleed = k x vy" for a strafe in that direction. The trim subtracts the bleed it predicts."""
    def k(pair):
        if isinstance(pair, (int, float)):
            return float(pair)
        return float(pair[0] if vy > 0 else pair[1])
    return vx - k(fwd_bleed) * vy, vy, wz - k(yaw_bleed) * vy


def bleed_from_env(env, axis):
    """The (left, right) trim for `axis` ("YAW" or "FWD") from OPENDUBS_STRAFE_<axis>_BLEED[_LEFT|_RIGHT]."""
    def f(name):
        v = env.get(name, "")
        return float(v) if v.strip() else None
    both = f(f"OPENDUBS_STRAFE_{axis}_BLEED")
    left, right = f(f"OPENDUBS_STRAFE_{axis}_BLEED_LEFT"), f(f"OPENDUBS_STRAFE_{axis}_BLEED_RIGHT")
    return (left if left is not None else (both or 0.0), right if right is not None else (both or 0.0))
