"""ENCORE's Spot plan, as Spot SDK commands: one synchronized command per window (body, arm and jaw
together), and the first-run envelope the player holds a real Spot to. No robot is needed here: the
commands are built from the plan in the local clock (the SDK converts to Spot's), so this can be checked
at a desk, and command_problems() says what Spot would refuse before anything is sent.

How a plan becomes commands:
- BODY -> a stand command whose mobility params carry `body_control.base_offset_rt_footprint`, an SE3
  trajectory. The plan gives the body at its keyframes; Spot interpolates linearly between points, so the
  path is sampled along a monotone cubic through the keys (ENCORE's own shape: each key reached at its
  time, no overshoot, smooth starts and stops). Spot refuses a body trajectory with too many points, so a
  window sends every key plus in-between samples up to POINTS_MAX, the longest gaps split first.
- ARM  -> an ArmJointTrajectory with the plan's positions and velocities at its keys, which Spot
  interpolates as a cubic: the same curve.
- GRIP -> a claw-gripper ScalarTrajectory of jaw angles, sampled like the body.
- BASE -> an SE2 trajectory command in Spot's `vision` frame: where Spot stands, sampled like the body.
  ENCORE gives it in Spot's starting frame (x forward, y left, from where Spot stood when the show
  started), and the session supplies that start as a vision-frame pose when it sends the first window.
  Spot plans its own steps to follow it. The window's MobilityParams carry the gait (locomotion hint),
  the swing height, a speed cap Spot itself holds to, and the BODY offsets, which ride in the same
  params instead of a stand command. A walking window has an end time: if no next window comes, Spot
  finishes the trajectory and stands.

Windows are chained: each window's trajectory keeps the previous window's points and its reference time,
so a command sent `lead_s` early carries on through the end of the one it replaces instead of jumping to
its own start. Spot's own default speed and acceleration limits for the arm and the jaw are left in place.
"""
import copy
import math
import re

from bosdyn.api import basic_command_pb2, geometry_pb2, mobility_command_pb2, robot_command_pb2, \
    synchronized_command_pb2, trajectory_pb2
from bosdyn.api.spot import robot_command_pb2 as spot_command_pb2
from bosdyn.client.robot_command import RobotCommandBuilder
from bosdyn.util import duration_to_seconds, seconds_to_duration, seconds_to_timestamp

SAMPLE_S = 0.1               # the envelope checks speeds on this fine grid
POINTS_MAX = 50              # body and jaw points sent per window (keys first); Spot refused 201
ARM_POINTS_MAX = 250         # ENCORE's own cap per arm command
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

# Walking: the first-run envelope plus BASE. Used only when the player is started with --walking, which
# takes the owner's word at Spot. The base caps are ours (placeholders until measured at Spot): Run
# Rudolph Run laps at 0.28 m/s. Spot's SDK has no WALK hint: its walking gaits are CRAWL (one foot at a
# time), AMBLE and TROT (its usual gait). JOG and HOP are never sent.
WALKING = {
    **FIRST_RUN,
    "parts": {"BASE", "BODY", "ARM", "GRIP"},
    "base_speed": 0.35,           # m/s, along the path, and the vel_limit Spot is sent
    "base_turn_deg": 30.0,        # deg/s
    "base_radius": 3.0,           # m from where Spot started
    "gaits": {"CRAWL": spot_command_pb2.HINT_CRAWL, "AMBLE": spot_command_pb2.HINT_AMBLE,
              "TROT": spot_command_pb2.HINT_TROT},
    "swings": {"LOW": spot_command_pb2.SWING_HEIGHT_LOW, "MEDIUM": spot_command_pb2.SWING_HEIGHT_MEDIUM,
               "HIGH": spot_command_pb2.SWING_HEIGHT_HIGH},
    "gait_default": "TROT",
    "swing_default": "MEDIUM",
}
ENVELOPES = {"first-run": FIRST_RUN, "walking": WALKING}
_active = {"name": "first-run"}


def use_envelope(name):
    """The envelope this process holds a real Spot to: "first-run" (feet planted) or "walking"."""
    if name not in ENVELOPES:
        raise ValueError(f"no envelope {name!r}; one of {', '.join(ENVELOPES)}")
    _active["name"] = name


def envelope_name():
    return _active["name"]


def active_envelope():
    return ENVELOPES[_active["name"]]


def mobility(command, env=None):
    """A BASE command's step style, {gait, swing, speed_cap}, from its optional `mobility` field."""
    env = env or WALKING
    m = command.get("mobility") or {}
    return {"gait": str(m.get("gait", env.get("gait_default", "TROT"))).upper(),
            "swing": str(m.get("swing", env.get("swing_default", "MEDIUM"))).upper(),
            "speed_cap": float(m.get("speed_cap", env.get("base_speed", 0.0)))}


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


def _sparse(keys, t_end, n_max=POINTS_MAX):
    """Every key time, then the longest gaps halved until there are n_max times (or gaps reach SAMPLE_S)."""
    ts = sorted({round(t, 6) for t in keys} | {0.0, round(t_end, 6)})
    while len(ts) < n_max:
        i = max(range(len(ts) - 1), key=lambda k: ts[k + 1] - ts[k])
        if ts[i + 1] - ts[i] < 2 * SAMPLE_S:
            break
        ts.insert(i + 1, (ts[i] + ts[i + 1]) / 2)
    return ts


def base_channels(command):
    """The base's keys as {channel: [(t, value)]}: x, y (m) and angle (rad), in Spot's starting frame."""
    return {c: [(p["time_since_reference"], p["pose"][c]) for p in command["points"]] for c in ("x", "y", "angle")}


SAMPLED_PARTS = ("BODY", "BASE", "GRIP")


def link_windows(plan):
    """A copy of the plan in which each BODY, BASE and GRIP command also carries its neighbours' points
    next to its edges (`_before`, `_after`, in its own time). The curve through each window boundary is
    then the same from both sides, as ENCORE's is. Without them each window's curve slows to a stop at
    its edges, which a body barely shows and a walk shows as a stutter every window."""
    plan = copy.deepcopy(plan)
    by_part = {}
    for c in plan.get("commands", []):
        if c["part"] in SAMPLED_PARTS and c.get("points"):
            by_part.setdefault(c["part"], []).append(c)
    for cs in by_part.values():
        cs.sort(key=lambda c: c["show_start_s"])
        for a, b in zip(cs, cs[1:]):
            shift = b["show_start_s"] - a["show_start_s"]
            a_end = a["points"][-1]["time_since_reference"]
            nxt = [q for q in b["points"] if q["time_since_reference"] + shift > a_end + 1e-6]
            if nxt:
                a["_after"] = dict(nxt[0], time_since_reference=nxt[0]["time_since_reference"] + shift)
            b_start = b["points"][0]["time_since_reference"]
            prv = [q for q in a["points"] if q["time_since_reference"] - shift < b_start - 1e-6]
            if prv:
                b["_before"] = dict(prv[-1], time_since_reference=prv[-1]["time_since_reference"] - shift)
    return plan


def sampled(command, fine=True):
    """A BODY, BASE or GRIP command along its curve: [(t, {channel: value})]. fine: every SAMPLE_S (for
    the envelope's speed checks); otherwise the keys and in-between samples Spot is sent (_sparse). The
    curve runs through the neighbours' points that link_windows() added, sampled only in this window."""
    ctx = dict(command, points=([command["_before"]] if "_before" in command else []) + command["points"]
               + ([command["_after"]] if "_after" in command else []))
    chans = (body_channels(ctx) if command["part"] == "BODY"
             else base_channels(ctx) if command["part"] == "BASE"
             else {"open": [(p["time_since_reference"], p["open_fraction"]) for p in ctx["points"]]})
    fns = {c: _monotone([t for t, _ in kv], [v for _, v in kv]) for c, kv in chans.items()}
    t_end = command["points"][-1]["time_since_reference"]
    ts = _samples(t_end) if fine else _sparse([p["time_since_reference"] for p in command["points"]], t_end)
    return [(t, {c: f(t) for c, f in fns.items()}) for t in ts]


# ---- the envelope ------------------------------------------------------------------------------
def envelope_problems(plan, env=None):
    """Why this plan is outside the envelope (the active one by default); empty when it is inside."""
    env = env or active_envelope()
    plan = link_windows(plan)
    out = []
    parts = {c["part"] for c in plan.get("commands", [])}
    if parts - env["parts"]:
        out.append(f"moves {', '.join(sorted(parts - env['parts']))}: the first runs keep Spot's feet planted "
                   f"(walking needs the player started with --walking)")
    for c in plan.get("commands", []):
        where = f"window {c['window']} {c['part']}"
        if c["part"] == "BASE" and "BASE" in env["parts"]:
            out += [f"{where}: {p}" for p in _base_envelope(c, env)]
            continue
        if c["part"] in ("BODY", "GRIP") and len(c["points"]) > POINTS_MAX:
            out.append(f"{where}: {len(c['points'])} keys, over the {POINTS_MAX} points Spot is sent")
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


def _base_envelope(c, env):
    """A BASE command against the walking envelope: its step style, its distance from the start, and its
    speed and turn rate along the curve Spot is sent."""
    out = []
    m = mobility(c, env)
    if m["gait"] not in env["gaits"]:
        out.append(f"gait {m['gait']} is not one of {', '.join(env['gaits'])}")
    if m["swing"] not in env["swings"]:
        out.append(f"swing {m['swing']} is not one of {', '.join(env['swings'])}")
    if not 0 < m["speed_cap"] <= env["base_speed"] + 1e-9:
        out.append(f"speed cap {m['speed_cap']:g} m/s is outside 0..{env['base_speed']:g}")
    if len(c["points"]) > POINTS_MAX:
        out.append(f"{len(c['points'])} keys, over the {POINTS_MAX} points Spot is sent")
    s = sampled(c)
    for (t0, a), (t1, b) in zip(s, s[1:]):
        r = math.hypot(b["x"], b["y"])
        if r > env["base_radius"] + 1e-6:
            out.append(f"{r:.2f} m from the start at {t1:.2f} s, past {env['base_radius']:g}")
        v = math.hypot(b["x"] - a["x"], b["y"] - a["y"]) / (t1 - t0)
        if v > min(env["base_speed"], m["speed_cap"]) + 1e-6:
            out.append(f"walks {v:.2f} m/s at {t1:.2f} s, over {min(env['base_speed'], m['speed_cap']):g}")
        w = math.degrees(abs(b["angle"] - a["angle"])) / (t1 - t0)
        if w > env["base_turn_deg"] + 1e-6:
            out.append(f"turns {w:.0f} deg/s at {t1:.2f} s, over {env['base_turn_deg']:g}")
    return out


# ---- the commands ------------------------------------------------------------------------------
FUTURE_MARGIN_S = 0.3          # every point sent is at least this far ahead of now (send latency, clock sync)


def _chain(carry, command, now_show_s):
    """This window's points joined to the previous window's, timed from the previous window's start.
    Every point not at least FUTURE_MARGIN_S ahead is dropped: Spot's gripper refuses a multipoint
    trajectory with any point before its current time (2026-11-23), and Spot starts each trajectory from
    where the part is now, so a past point adds nothing. Returns (reference show time, [(t, point)])."""
    if carry is None:
        ref, chain = command["show_start_s"], [(p["time_since_reference"], p) for p in command["points"]]
    else:
        ref, old = carry
        shift = command["show_start_s"] - ref
        new = [(p["time_since_reference"] + shift, p) for p in command["points"]]
        chain = [(t, p) for t, p in old if t < new[0][0] - 1e-6] + new
    return ref, [(t, p) for t, p in chain if ref + t > now_show_s + FUTURE_MARGIN_S]


END_MARGIN_S = 1.0             # a walking window ends this long after its last point, if no next one replaces it
VISION = "vision"


def to_vision(start, x, y, angle):
    """A point in Spot's starting frame, as (x, y, angle) in the vision frame. `start` is the vision-frame
    pose (x, y, angle) Spot stood at. The angle is left unwrapped, so a lap never reads as a turn back."""
    sx, sy, sa = start
    c, s_ = math.cos(sa), math.sin(sa)
    return sx + c * x - s_ * y, sy + s_ * x + c * y, sa + angle


def from_vision(start, x, y, angle):
    sx, sy, sa = start
    c, s_ = math.cos(sa), math.sin(sa)
    dx, dy = x - sx, y - sy
    return c * dx + s_ * dy, -s_ * dx + c * dy, angle - sa


def _points(c):
    if c["part"] in SAMPLED_PARTS:
        return [{"time_since_reference": t, "values": v} for t, v in sampled(c, fine=False)]
    pts = []                            # ARM: ENCORE gives two points at a time where one key ends and the
    for q in c["points"]:               # next starts, same pose: keep one, as _monotone does for the body
        if pts and abs(q["time_since_reference"] - pts[-1]["time_since_reference"]) < 1e-6:
            pts[-1] = q
        else:
            pts.append(q)
    return pts


def build_window(commands, t0_wall, now_wall, carry, start=None, env=None):
    """One RobotCommand for a window's commands (BASE, BODY, ARM, GRIP), its times in the LOCAL clock: the
    SDK's RobotCommandClient.robot_command converts every trajectory's reference_time to Spot's clock
    itself (EDIT_TREE_CONVERT_LOCAL_TIME_TO_ROBOT_TIME), so converting here as well would shift it twice.
    `carry` maps part -> (reference show time, [(t, point)]) from the previous window, and is updated.
    `start` is Spot's vision-frame pose (x, y, angle) when the show started; a BASE window needs it.
    Without BASE the body rides in a stand command, which has no end time: after its last point the body
    holds that pose until the next mobility command. With BASE it rides in the SE2 trajectory command's
    params; end_time_s(cmd) gives the end time to send it with."""
    env = env or active_envelope()
    now_show = now_wall - t0_wall
    chains = {}
    for c in commands:
        part = c["part"]
        if part not in ("BASE", "BODY", "ARM", "GRIP"):
            raise RuntimeError(f"refused: {part} is not played on a real Spot yet")
        ref, chain = _chain(carry.get(part), {"show_start_s": c["show_start_s"], "points": _points(c)}, now_show)
        if not chain:
            raise RuntimeError(f"refused: window {c['window']} {part} was built too late; every point is past")
        carry[part] = (ref, chain)
        chains[part] = (c, seconds_to_timestamp(t0_wall + ref), chain)

    cmd = None
    body_params = None
    if "BODY" in chains:
        _, ref_ts, chain = chains["BODY"]
        traj = trajectory_pb2.SE3Trajectory(reference_time=ref_ts)
        for t, p in chain:
            v = p["values"]
            traj.points.add(time_since_reference=seconds_to_duration(t), pose=geometry_pb2.SE3Pose(
                position=geometry_pb2.Vec3(x=0.0, y=0.0, z=v["height"]),
                rotation=_quat(v["roll"], v["pitch"], v["yaw"])))
        body_params = spot_command_pb2.BodyControlParams(base_offset_rt_footprint=traj)
    if "BASE" in chains:
        c, ref_ts, chain = chains["BASE"]
        if start is None:
            raise RuntimeError("refused: a BASE window needs Spot's starting pose in the vision frame")
        if "BASE" not in env["parts"]:
            raise RuntimeError("refused: BASE is outside the envelope (walking needs --walking)")
        m = mobility(c, env)
        cap = min(m["speed_cap"], env["base_speed"])
        turn = math.radians(env["base_turn_deg"])
        params = spot_command_pb2.MobilityParams(
            locomotion_hint=env["gaits"][m["gait"]], swing_height=env["swings"][m["swing"]],
            vel_limit=geometry_pb2.SE2VelocityLimit(
                max_vel=geometry_pb2.SE2Velocity(linear=geometry_pb2.Vec2(x=cap, y=cap), angular=turn),
                min_vel=geometry_pb2.SE2Velocity(linear=geometry_pb2.Vec2(x=-cap, y=-cap), angular=-turn)))
        if body_params is not None:
            params.body_control.CopyFrom(body_params)
        traj = trajectory_pb2.SE2Trajectory(reference_time=ref_ts, interpolation=trajectory_pb2.POS_INTERP_LINEAR)
        for t, p in chain:
            v = p["values"]
            x, y, a = to_vision(start, v["x"], v["y"], v["angle"])
            traj.points.add(time_since_reference=seconds_to_duration(t),
                            pose=geometry_pb2.SE2Pose(position=geometry_pb2.Vec2(x=x, y=y), angle=a))
        any_params = RobotCommandBuilder._to_any(params)
        cmd = robot_command_pb2.RobotCommand(synchronized_command=synchronized_command_pb2.SynchronizedCommand.Request(
            mobility_command=mobility_command_pb2.MobilityCommand.Request(
                se2_trajectory_request=basic_command_pb2.SE2TrajectoryCommand.Request(
                    trajectory=traj, se2_frame_name=VISION), params=any_params)))
    elif body_params is not None:
        cmd = RobotCommandBuilder.synchro_stand_command(params=spot_command_pb2.MobilityParams(body_control=body_params))
    if "ARM" in chains:
        _, ref_ts, chain = chains["ARM"]
        part_cmd = RobotCommandBuilder.arm_joint_move_helper(
            joint_positions=[[p["position"][j] for j in JOINTS] for _, p in chain],
            times=[t for t, _ in chain],
            joint_velocities=[[p["velocity"][j] for j in JOINTS] for _, p in chain],
            ref_time=ref_ts)
        cmd = part_cmd if cmd is None else RobotCommandBuilder.build_synchro_command(cmd, part_cmd)
    if "GRIP" in chains:
        _, ref_ts, chain = chains["GRIP"]
        part_cmd = RobotCommandBuilder.claw_gripper_open_fraction_command(0.0)
        g = part_cmd.synchronized_command.gripper_command.claw_gripper_command
        g.trajectory.Clear()
        g.trajectory.reference_time.CopyFrom(ref_ts)
        g.trajectory.interpolation = trajectory_pb2.POS_INTERP_LINEAR
        for t, p in chain:
            g.trajectory.points.add(point=OPEN_RAD * p["values"]["open"], time_since_reference=seconds_to_duration(t))
        cmd = part_cmd if cmd is None else RobotCommandBuilder.build_synchro_command(cmd, part_cmd)
    return cmd


def end_time_s(cmd):
    """The local-clock end time to send a walking window with (robot_command's end_time_secs), or None
    for a window that doesn't walk. Spot requires one on every trajectory command."""
    r = cmd.synchronized_command.mobility_command
    if not r.HasField("se2_trajectory_request"):
        return None
    tr = r.se2_trajectory_request.trajectory
    ref = tr.reference_time.seconds + tr.reference_time.nanos * 1e-9
    return ref + duration_to_seconds(tr.points[-1].time_since_reference) + END_MARGIN_S


# ---- what the robot would refuse, checked before sending (and by the fake Spot) --------------------
def _trajectories(cmd):
    """(name, reference_time, [seconds since reference]) for each trajectory in a built command."""
    out, sc = [], cmd.synchronized_command
    se2 = sc.mobility_command.se2_trajectory_request.trajectory
    if se2.points:
        out.append(("BASE", se2.reference_time, [duration_to_seconds(p.time_since_reference) for p in se2.points]))
    if sc.HasField("mobility_command") and sc.mobility_command.HasField("params"):
        mp = spot_command_pb2.MobilityParams()
        sc.mobility_command.params.Unpack(mp)
        b = mp.body_control.base_offset_rt_footprint
        if b.points:
            out.append(("BODY", b.reference_time, [duration_to_seconds(p.time_since_reference) for p in b.points]))
    a = sc.arm_command.arm_joint_move_command.trajectory
    if a.points:
        out.append(("ARM", a.reference_time, [duration_to_seconds(p.time_since_reference) for p in a.points]))
    g = sc.gripper_command.claw_gripper_command.trajectory
    if g.points:
        out.append(("GRIP", g.reference_time, [duration_to_seconds(p.time_since_reference) for p in g.points]))
    return out


def _base_problems(cmd, start, env):
    """The walking guard on a built window, checked just before it is sent: the frame, the gait and swing
    (only the envelope's), the speed cap Spot is told to hold, and every point sent against the radius,
    speed and turn caps, read back into Spot's starting frame."""
    r = cmd.synchronized_command.mobility_command
    if not r.HasField("se2_trajectory_request"):
        return []
    if "BASE" not in env["parts"]:
        return ["BASE: walking is outside the envelope (the player needs --walking)"]
    if start is None:
        return ["BASE: no starting pose"]
    out, req = [], r.se2_trajectory_request
    if req.se2_frame_name != VISION:
        out.append(f"BASE: frame {req.se2_frame_name!r}, not {VISION!r}")
    mp = spot_command_pb2.MobilityParams()
    r.params.Unpack(mp)
    if mp.locomotion_hint not in env["gaits"].values():
        out.append(f"BASE: gait {spot_command_pb2.LocomotionHint.Name(mp.locomotion_hint)} is not allowed")
    if mp.swing_height not in env["swings"].values():
        out.append(f"BASE: swing {spot_command_pb2.SwingHeight.Name(mp.swing_height)} is not allowed")
    lim = mp.vel_limit.max_vel
    if not mp.HasField("vel_limit") or max(abs(lim.linear.x), abs(lim.linear.y)) > env["base_speed"] + 1e-9 \
            or abs(lim.angular) > math.radians(env["base_turn_deg"]) + 1e-9:
        out.append("BASE: the speed cap Spot is sent is missing or over the envelope's")
    pts = [(duration_to_seconds(p.time_since_reference), from_vision(start, p.pose.position.x, p.pose.position.y,
                                                                      p.pose.angle)) for p in req.trajectory.points]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if math.hypot(b[0], b[1]) > env["base_radius"] + 1e-6:
            out.append(f"BASE: a point is {math.hypot(b[0], b[1]):.2f} m from the start, past {env['base_radius']:g}")
            break
        if math.hypot(b[0] - a[0], b[1] - a[1]) / (t1 - t0) > env["base_speed"] * 1.05:
            out.append(f"BASE: {math.hypot(b[0] - a[0], b[1] - a[1]) / (t1 - t0):.2f} m/s between points, "
                       f"over {env['base_speed']:g}")
            break
        if math.degrees(abs(b[2] - a[2])) / (t1 - t0) > env["base_turn_deg"] * 1.05:
            out.append(f"BASE: {math.degrees(abs(b[2] - a[2])) / (t1 - t0):.0f} deg/s between points, "
                       f"over {env['base_turn_deg']:g}")
            break
    return out


def command_problems(cmd, t0_wall, now_wall, start=None, env=None):
    """Why Spot would refuse or misplay this built command, or the walking guard refuses it; empty when it
    looks right. Checked on the command as built (local clock), against what has gone wrong or the SDK
    documents."""
    out = _base_problems(cmd, start, env or active_envelope())
    for name, ref, ts in _trajectories(cmd):
        cap = POINTS_MAX + 10 if name in ("BODY", "GRIP", "BASE") else ARM_POINTS_MAX
        if len(ts) > cap:
            out.append(f"{name}: {len(ts)} points, over {cap} (Spot refused 201 body points)")
        if any(b <= a for a, b in zip(ts, ts[1:])):
            out.append(f"{name}: point times are not strictly increasing")
        ref_s = ref.seconds + ref.nanos * 1e-9
        if abs(ref_s - t0_wall) > 24 * 3600:
            out.append(f"{name}: reference time is {ref_s - t0_wall:+.0f} s from the show start; it must be in the "
                       f"local clock (the SDK converts it)")
        if ref_s + ts[0] <= now_wall:     # Spot's gripper refused this (2026-11-23); none is sent past
            out.append(f"{name}: its first point is {now_wall - ref_s - ts[0]:.1f} s in the past")
    g = cmd.synchronized_command.gripper_command.claw_gripper_command.trajectory
    if any(not OPEN_RAD - 1e-6 <= p.point <= 1e-6 for p in g.points):
        out.append(f"GRIP: a jaw angle is outside {OPEN_RAD}..0 rad")
    return out
