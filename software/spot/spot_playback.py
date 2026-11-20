"""ENCORE's Spot plan, as Spot SDK commands: one synchronized command per window (body, arm and jaw
together), and the first-run envelope the player holds a real Spot to. No robot is needed here: the
commands are built from the plan and a time-conversion function, so this can be checked at a desk.

How a plan becomes commands:
- BODY -> a stand command whose mobility params carry `body_control.base_offset_rt_footprint`, an SE3
  trajectory. The plan gives the body at its keyframes; Spot interpolates linearly between points, so the
  path is sampled every SAMPLE_S along a monotone cubic through the keys (ENCORE's own shape: each key
  reached at its time, no overshoot, smooth starts and stops).
- ARM  -> an ArmJointTrajectory with the plan's positions and velocities at its keys, which Spot
  interpolates as a cubic: the same curve.
- GRIP -> a claw-gripper ScalarTrajectory of jaw angles, sampled like the body.

Windows are chained: each window's trajectory keeps the previous window's points and its reference time,
so a command sent `lead_s` early carries on through the end of the one it replaces instead of jumping to
its own start. Spot's own default speed and acceleration limits for the arm and the jaw are left in place.
"""
import math
import re

from bosdyn.api import geometry_pb2, trajectory_pb2
from bosdyn.api.spot import robot_command_pb2 as spot_command_pb2
from bosdyn.client.robot_command import RobotCommandBuilder
from bosdyn.util import seconds_to_duration, seconds_to_timestamp

SAMPLE_S = 0.1
JOINTS = ("sh0", "sh1", "el0", "el1", "wr0", "wr1")
OPEN_RAD = -1.5708            # the claw's fully open angle (the SDK's own constant); 0 is closed

# The first-run envelope: a real Spot plays only shows inside it, whatever ENCORE's placeholder limits
# allow. Feet planted (no BASE), so a cable to the Mac is never pulled; small body offsets; slow joints.
FIRST_RUN = {
    "parts": {"BODY", "ARM", "GRIP"},
    "body": {"height": 0.10, "roll": 20.0, "pitch": 22.0, "yaw": 20.0},          # |offset| (m, deg)
    "body_speed": {"height": 0.15, "roll": 45.0, "pitch": 45.0, "yaw": 45.0},   # per s
    "arm_speed_deg": 90.0,
    # Spot arm joint ranges (deg) as ENCORE's profile has them: the model's own, less 3 deg
    "arm": {"sh0": (-147, 177), "sh1": (-180, 27), "el0": (3, 177), "el1": (-157, 157),
            "wr0": (-102, 102), "wr1": (-162, 162)},
    "points_max": 250,
}


# ---- the shape between keys: a monotone cubic (Fritsch-Carlson), zero slope at the ends -----------
def _monotone(ts, ys):
    """A function t -> y through (ts, ys): every point hit, no overshoot."""
    pts = []
    for t, y in zip(ts, ys):                     # a key's start and the previous key's end can coincide
        if pts and abs(t - pts[-1][0]) < 1e-9:
            pts[-1] = (t, y)
        else:
            pts.append((t, y))
    n = len(pts)
    if n == 1:
        return lambda t: pts[0][1]
    h = [pts[k + 1][0] - pts[k][0] for k in range(n - 1)]
    d = [(pts[k + 1][1] - pts[k][1]) / h[k] for k in range(n - 1)]
    m = [0.0] * n
    for k in range(1, n - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])

    def at(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        k = max(i for i in range(n - 1) if pts[i][0] <= t)
        s = (t - pts[k][0]) / h[k]
        h00, h10, h01, h11 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
        return h00 * pts[k][1] + h10 * h[k] * m[k] + h01 * pts[k + 1][1] + h11 * h[k] * m[k + 1]
    return at


def _samples(t_end):
    n = max(1, math.ceil(t_end / SAMPLE_S - 1e-9))
    return [min(t_end, i * SAMPLE_S) for i in range(n + 1)]


def _rpy(q):
    w, x, y, z = q["w"], q["x"], q["y"], q["z"]
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return {"roll": math.degrees(roll), "pitch": math.degrees(pitch), "yaw": math.degrees(yaw)}


def _quat(roll_deg, pitch_deg, yaw_deg):
    r, p, y = (math.radians(a) / 2 for a in (roll_deg, pitch_deg, yaw_deg))
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return geometry_pb2.Quaternion(w=cr * cp * cy + sr * sp * sy, x=sr * cp * cy - cr * sp * sy,
                                   y=cr * sp * cy + sr * cp * sy, z=cr * cp * sy - sr * sp * cy)


def body_channels(command):
    """The body's keys as {channel: [(t, value)]}: height (m), roll, pitch, yaw (deg)."""
    out = {c: [] for c in ("height", "roll", "pitch", "yaw")}
    for p in command["points"]:
        t = p["time_since_reference"]
        out["height"].append((t, p["pose"]["position"]["z"]))
        for c, v in _rpy(p["pose"]["rotation"]).items():
            out[c].append((t, v))
    return out


def sampled(command):
    """A BODY or GRIP command sampled every SAMPLE_S: [(t, {channel: value})]."""
    chans = (body_channels(command) if command["part"] == "BODY"
             else {"open": [(p["time_since_reference"], p["open_fraction"]) for p in command["points"]]})
    fns = {c: _monotone([t for t, _ in kv], [v for _, v in kv]) for c, kv in chans.items()}
    t_end = command["points"][-1]["time_since_reference"]
    return [(t, {c: f(t) for c, f in fns.items()}) for t in _samples(t_end)]


# ---- the envelope ------------------------------------------------------------------------------
def envelope_problems(plan, env=FIRST_RUN):
    """Why this plan is outside the first-run envelope; empty when it is inside."""
    out = []
    parts = {c["part"] for c in plan.get("commands", [])}
    if parts - env["parts"]:
        out.append(f"moves {', '.join(sorted(parts - env['parts']))}: the first runs keep Spot's feet planted")
    for c in plan.get("commands", []):
        where = f"window {c['window']} {c['part']}"
        if len(c["points"]) > env["points_max"]:
            out.append(f"{where}: {len(c['points'])} points, over {env['points_max']}")
        if c["part"] in ("BODY", "GRIP"):
            s = sampled(c)
            for (t0, a), (t1, b) in zip(s, s[1:]):
                for ch, v in b.items():
                    if c["part"] == "GRIP":
                        if not -1e-6 <= v <= 1 + 1e-6:
                            out.append(f"{where}: jaw {v:.2f} at {t1:.2f} s is outside 0..1")
                        continue
                    lim, cap = env["body"][ch], env["body_speed"][ch]
                    if abs(v) > lim + 1e-6:
                        out.append(f"{where}: body {ch} {v:.3g} at {t1:.2f} s is past {lim:g}")
                    if t1 > t0 and abs(v - a[ch]) / (t1 - t0) > cap + 1e-6:
                        out.append(f"{where}: body {ch} moves {abs(v - a[ch]) / (t1 - t0):.3g}/s at {t1:.2f} s, "
                                   f"over {cap:g}")
        elif c["part"] == "ARM":
            for p in c["points"]:
                for j in JOINTS:
                    deg = math.degrees(p["position"][j])
                    lo, hi = env["arm"][j]
                    if not lo - 1e-6 <= deg <= hi + 1e-6:
                        out.append(f"{where}: {j} {deg:.1f} deg at {p['time_since_reference']:.2f} s is outside "
                                   f"{lo}..{hi}")
                    spd = abs(math.degrees(p["velocity"][j]))
                    if spd > env["arm_speed_deg"] + 1e-6:
                        out.append(f"{where}: {j} moves {spd:.0f} deg/s at {p['time_since_reference']:.2f} s, over "
                                   f"{env['arm_speed_deg']:g}")
    seen, uniq = set(), []
    for p in out:                       # one line per kind of problem per window is enough to act on
        key = re.sub(r"[-\d.]+", "#", p.split(" at ")[0])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


# ---- the commands ------------------------------------------------------------------------------
def _chain(carry, command, now_show_s):
    """This window's points joined to the previous window's, timed from the previous window's start;
    points already past are dropped. Returns (reference show time, [(t, point)])."""
    if carry is None:
        return command["show_start_s"], [(p["time_since_reference"], p) for p in command["points"]]
    ref, old = carry
    shift = command["show_start_s"] - ref
    new = [(p["time_since_reference"] + shift, p) for p in command["points"]]
    keep = [(t, p) for t, p in old if t < new[0][0] - 1e-6]
    past = [i for i, (t, _) in enumerate(keep) if ref + t < now_show_s - 0.5]
    if past:
        keep = keep[past[-1]:]          # keep the last point already past, so Spot interpolates from it
    return ref, keep + new


def build_window(commands, t0_wall, now_wall, robot_ts, carry):
    """One RobotCommand for a window's commands (BODY, ARM, GRIP). `robot_ts(wall_s)` gives the robot's
    Timestamp for a local wall time (the SDK's TimeSync). `carry` maps part -> (reference show time,
    [(t, point)]) from the previous window, and is updated. Returns (command, end_wall or None): the
    mobility command must be given an end time."""
    now_show = now_wall - t0_wall
    cmd, end_wall = None, None
    for c in sorted(commands, key=lambda c: c["part"]):
        part = c["part"]
        if part in ("BODY", "GRIP"):
            pts = [{"time_since_reference": t, "values": v} for t, v in sampled(c)]
        else:
            pts = c["points"]
        ref, chain = _chain(carry.get(part), {"show_start_s": c["show_start_s"], "points": pts}, now_show)
        carry[part] = (ref, chain)
        ref_ts = robot_ts(t0_wall + ref)
        if part == "BODY":
            traj = trajectory_pb2.SE3Trajectory(reference_time=ref_ts)
            for t, p in chain:
                v = p["values"]
                traj.points.add(time_since_reference=seconds_to_duration(t), pose=geometry_pb2.SE3Pose(
                    position=geometry_pb2.Vec3(x=0.0, y=0.0, z=v["height"]),
                    rotation=_quat(v["roll"], v["pitch"], v["yaw"])))
            params = spot_command_pb2.MobilityParams(
                body_control=spot_command_pb2.BodyControlParams(base_offset_rt_footprint=traj))
            part_cmd = RobotCommandBuilder.synchro_stand_command(params=params)
            end_wall = t0_wall + ref + chain[-1][0] + 1.0
        elif part == "ARM":
            part_cmd = RobotCommandBuilder.arm_joint_move_helper(
                joint_positions=[[p["position"][j] for j in JOINTS] for _, p in chain],
                times=[t for t, _ in chain],
                joint_velocities=[[p["velocity"][j] for j in JOINTS] for _, p in chain],
                ref_time=ref_ts)
        elif part == "GRIP":
            part_cmd = RobotCommandBuilder.claw_gripper_open_fraction_command(0.0)
            g = part_cmd.synchronized_command.gripper_command.claw_gripper_command
            g.trajectory.Clear()
            g.trajectory.reference_time.CopyFrom(ref_ts)
            g.trajectory.interpolation = trajectory_pb2.POS_INTERP_LINEAR
            for t, p in chain:
                g.trajectory.points.add(point=OPEN_RAD * p["values"]["open"], time_since_reference=seconds_to_duration(t))
        else:
            raise RuntimeError(f"refused: {part} is not played on a real Spot yet")
        cmd = part_cmd if cmd is None else RobotCommandBuilder.build_synchro_command(cmd, part_cmd)
    return cmd, end_wall


def wall_timestamp(wall_s):
    """For checking at a desk: the robot's clock taken to be the local one."""
    return seconds_to_timestamp(wall_s)
