"""Look-at: aim a head camera at a point, for a capture mission (HallPass), and hold it there.

THIS MOVES THE NECK. A mission runner on the Thor asks for a point; this works out the neck's pan and
tilt that put the camera's optical axis through it, sends the neck `GAZE <pan> <tilt> <lease_ms>`
(head protocol §4), waits until the head has stopped there, and keeps renewing the GAZE until the
runner releases it (or `hold_s` runs out, so a runner that dies frees the neck).

  POST /api/head/look_at {"owner", "frame_id", "x", "y", "z", "camera", "lift_m", "tol_deg",
                          "timeout_s", "hold_s"}
      Blocks until the head settles or `timeout_s` passes:
        {"settled": true, "pan_deg", "tilt_deg", "err_deg", "t_settled"}
        {"settled": false, "reason", "pan_deg", "tilt_deg", "err_deg", ...}
      reason: "out_of_range" (nothing moved; `need` and `limits` say why), "timeout", "stopped_short"
      (the head stopped, `err_deg` off: PRECISION below), "neck_silent", or "blocked: <why>" (a gate
      below). The head holds its aim either way until the release.
  POST /api/head/look_at/release {"owner"}     the head is free again (GAZE OFF)
  GET  /api/head/look_at                       the session, and the head's limits
  /head/look_at/state  std_msgs/String, JSON   the same as GET, ~5 Hz, for the rosbridge feed

Angles are the REAL joint angles in the robot description's signs, in degrees, the same quantities as
/head/joint_states (neck_state.py): head_pan + turns the head to the robot's left, head_tilt + tips the
face down, 0 is straight ahead and level. `err_deg` is the larger of the two joints' distances from
the solution, read from the neck's own report (what /head/joint_states carries).

THE POINT: `frame_id` is `base_link` or `lift_platform`. A runner holding the point in `map` or a
floor frame transforms it to `base_link` with its own tf2. From there to the camera is the robot's
URDF (OPENDUBS_URDF, the file robot_state_publisher reads), with the neck's angles and `lift_m` for
the lift's `zaxis`: nothing publishes the lift's height yet, so the caller states it (0 = at the
bottom). `camera` is the link whose +x is aimed: `camera_link` (the head-top Femto) by default.

THE GATES are neck_follow's, exactly (neck_follow.motion_gate: a neck fitted, the arms' shoulders
clear, not DEGRADED or E_STOP, no show loaded), plus a person: the neck held by RC or touch, or the
tablet being touched. Unlike following, look-at does not depend on the Head & face `tracking` setting.
A gate that closes during a hold stops the renewals; the neck's lease runs out within a second and
the head stays where it is. The session stays open, so tracking does not take the head back mid-
mission; GET says why it is not holding, and the next look-at tries again.

WHO OWNS THE NECK: look-at, from its first call until the release or `hold_s`. neck_follow sends
nothing meanwhile (its why_not_now asks active()). A show still wins: a loaded show is a gate.

PRECISION. The neck stops a move once within its deadband of the target (firmware/neck_dial
pantilt_core: kDeadbandDeg, 1.5 commanded degrees, which on these 300-degree servos is 2.5 real ones),
so a GAZE lands up to that far short. From rest it moves in steps of its slew per tick (70 deg/s at
50 Hz, 1.4 commanded degrees), never less. So: one corrective NUDGE, when the first stop is more than
half a step off, sends that axis one step further, and lands within about 0.7 commanded degrees
(~1.2 real). A tighter `tol_deg` needs the deadband to apply only to starting a move (firmware).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("look_at")
router = APIRouter()

TOPIC = "/head/look_at/state"
CENTRE = 90.0
PAN_CMD = (0.0, 180.0)                      # the neck's pan travel, commanded (neck_state.PAN_TRAVEL)
GAZE_LEASE_MS = 1000                        # the neck's maximum (head protocol §4)
RENEW_S = 0.3
POLL_S = 0.05
DEADBAND_CMD = 1.5                          # pantilt_core kDeadbandDeg
STEP_CMD = 1.4                              # pantilt_core kSlewDps x kPeriodMs: one tick's move
NUDGE_CMD = DEADBAND_CMD + 0.1              # just past the deadband: the neck moves one step and stops
MAX_HOLD_S = 600.0
FRAMES = ("base_link", "lift_platform")
PAN_JOINT, TILT_JOINT, LIFT_JOINT = "head_pan", "head_tilt", "zaxis"
OWNER_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_clock = time.monotonic


# ---- the robot's description: just enough of a URDF for one chain ------------------------------------
def _rpy(r: float, p: float, y: float):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]


def _axis_angle(a, t: float):
    x, y, z = a
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    x, y, z = x / n, y / n, z / n
    c, s, v = math.cos(t), math.sin(t), 1 - math.cos(t)
    return [[c + x * x * v, x * y * v - z * s, x * z * v + y * s],
            [y * x * v + z * s, c + y * y * v, y * z * v - x * s],
            [z * x * v - y * s, z * y * v + x * s, c + z * z * v]]


def _T(R, p=(0.0, 0.0, 0.0)):
    return [R[0] + [p[0]], R[1] + [p[1]], R[2] + [p[2]], [0.0, 0.0, 0.0, 1.0]]


def _mul(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _inv(T):
    R = [[T[j][i] for j in range(3)] for i in range(3)]
    p = [-sum(R[i][k] * T[k][3] for k in range(3)) for i in range(3)]
    return _T(R, p)


def _apply(T, v):
    return [sum(T[i][k] * v[k] for k in range(3)) + T[i][3] for i in range(3)]


def _floats(s: str | None, n: int = 3):
    try:
        v = [float(x) for x in (s or "").split()]
    except ValueError:
        return None
    return v if len(v) == n else None


def parse_urdf(text: str) -> dict:
    """{child link: {name, type, parent, origin (4x4), axis, lower, upper}} for every joint."""
    joints = {}
    for j in ET.fromstring(text).iter("joint"):
        par, ch = j.find("parent"), j.find("child")
        if par is None or ch is None:
            continue
        o = j.find("origin")
        xyz = _floats(o.get("xyz") if o is not None else None) or [0.0, 0.0, 0.0]
        rpy = _floats(o.get("rpy") if o is not None else None) or [0.0, 0.0, 0.0]
        ax = j.find("axis")
        lim = j.find("limit")
        joints[ch.get("link")] = {
            "name": j.get("name"), "type": j.get("type"), "parent": par.get("link"),
            "origin": _T(_rpy(*rpy), xyz),
            "axis": (_floats(ax.get("xyz")) if ax is not None else None) or [1.0, 0.0, 0.0],
            "lower": float(lim.get("lower")) if lim is not None and lim.get("lower") else None,
            "upper": float(lim.get("upper")) if lim is not None and lim.get("upper") else None}
    return joints


def chain(joints: dict, root: str, tip: str) -> list:
    """The joints from `root` down to `tip`, in order. ValueError if `tip` does not hang below `root`."""
    out, link = [], tip
    while link != root:
        j = joints.get(link)
        if j is None:
            raise ValueError(f"{tip} is not below {root} in the robot description")
        out.append(j)
        link = j["parent"]
    return out[::-1]


def fk(ch: list, q: dict):
    """The tip's pose in the root's frame, for joint values `q` (radians, metres; missing = 0)."""
    T = _T([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
    for j in ch:
        T = _mul(T, j["origin"])
        v = q.get(j["name"], 0.0)
        if j["type"] in ("revolute", "continuous"):
            T = _mul(T, _T(_axis_angle(j["axis"], v)))
        elif j["type"] == "prismatic":
            a = j["axis"]
            T = _mul(T, _T([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], (a[0] * v, a[1] * v, a[2] * v)))
    return T


def _bearing(ch: list, q: dict, point):
    """(azimuth, elevation) of `point` seen from the tip along its +x, radians: 0, 0 when aimed."""
    x, y, z = _apply(_inv(fk(ch, q)), point)
    return math.atan2(y, x), math.atan2(z, math.hypot(x, y)), x


def solve(ch: list, point, lift_m: float = 0.0):
    """The (pan, tilt) in radians that aim the tip's +x through `point`, or None if no solution
    converges (the point is too close to the camera, or behind the pan axis's reach). Pure."""
    base = {LIFT_JOINT: lift_m}
    pan = tilt = 0.0
    # A start from the plain bearing, so Newton begins on the right side of the head.
    az, el, _ = _bearing(ch, {**base, PAN_JOINT: 0.0, TILT_JOINT: 0.0}, point)
    pan, tilt = az, -el
    h = 1e-5
    for _ in range(30):
        q = {**base, PAN_JOINT: pan, TILT_JOINT: tilt}
        az, el, fwd = _bearing(ch, q, point)
        if abs(az) < 1e-7 and abs(el) < 1e-7 and fwd > 0:
            return pan, tilt
        a1, e1, _ = _bearing(ch, {**q, PAN_JOINT: pan + h}, point)
        a2, e2, _ = _bearing(ch, {**q, TILT_JOINT: tilt + h}, point)
        J = [[(a1 - az) / h, (a2 - az) / h], [(e1 - el) / h, (e2 - el) / h]]
        det = J[0][0] * J[1][1] - J[0][1] * J[1][0]
        if abs(det) < 1e-12:
            return None
        dp = (J[1][1] * az - J[0][1] * el) / det
        dt = (-J[1][0] * az + J[0][0] * el) / det
        pan, tilt = pan - dp, tilt - dt
        pan = math.atan2(math.sin(pan), math.cos(pan))
    return None


_desc: dict = {"path": None, "mtime": None, "joints": None}


def urdf_path(env=os.environ) -> str:
    p = (env.get("OPENDUBS_URDF") or "").strip()
    if p:
        return p
    robot = "/opt/opendubs/description/opendubs.urdf"
    if os.path.exists(robot):
        return robot
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "description", "opendubs", "opendubs.urdf"))


def description() -> dict:
    """The robot's joints, re-read when the file changes (a deploy)."""
    p = urdf_path()
    m = os.path.getmtime(p)
    if _desc["joints"] is None or _desc["path"] != p or _desc["mtime"] != m:
        with open(p, encoding="utf-8") as fh:
            _desc.update(path=p, mtime=m, joints=parse_urdf(fh.read()))
    return _desc["joints"]


# ---- the neck's degrees ------------------------------------------------------------------------------
def _k() -> float:
    import neck_state
    return neck_state._span() / 180.0


def to_cmd(pan: float, tilt: float, k: float):
    """Real radians (description signs) -> the neck's commanded degrees: neck_state.to_radians reversed."""
    return CENTRE + math.degrees(pan) / k, CENTRE - math.degrees(tilt) / k


def to_real_deg(pan_cmd: float, tilt_cmd: float, k: float):
    return (pan_cmd - CENTRE) * k, -(tilt_cmd - CENTRE) * k


def limits(tilt_band, k: float, joints: dict | None = None) -> dict:
    """The head's reach in real degrees: the neck's travel (pan) and this robot's commissioned tilt band,
    inside the description's joint limits."""
    p = sorted(to_real_deg(c, CENTRE, k)[0] for c in PAN_CMD)
    t = sorted(to_real_deg(CENTRE, c, k)[1] for c in tilt_band)
    for name, rng in ((PAN_JOINT, p), (TILT_JOINT, t)):
        j = next((v for v in (joints or {}).values() if v["name"] == name), None)
        if j and j["lower"] is not None and j["upper"] is not None:
            rng[0], rng[1] = max(rng[0], math.degrees(j["lower"])), min(rng[1], math.degrees(j["upper"]))
    return {"pan_deg": [round(p[0], 2), round(p[1], 2)], "tilt_deg": [round(t[0], 2), round(t[1], 2)]}


def nudge(aim_cmd: float, at_cmd: float) -> float:
    """Where to send one axis after the first stop: one step further if it stopped more than half a step
    short, else the aim itself (inside the deadband: the neck stays put)."""
    r = aim_cmd - at_cmd
    if abs(r) <= STEP_CMD / 2:
        return aim_cmd
    return at_cmd + math.copysign(NUDGE_CMD, r)


# ---- the session -------------------------------------------------------------------------------------
session: dict = {"active": False, "owner": None, "target": None, "aim_cmd": None, "aim_deg": None, "cmd": None,
                 "until": 0.0, "settled": False, "why": "idle", "err_deg": None, "t_settled": None,
                 "holding": False, "blocked": None, "sent": None}


def active() -> bool:
    """True while a mission owns the neck: from its first look-at to its release or hold_s."""
    if session["active"] and _clock() >= session["until"]:
        _end("hold_s ran out")
    return session["active"]


def _end(why: str) -> None:
    session.update(active=False, holding=False, why=why)


def _neck():
    """The neck's last report, as /head/joint_states carries it, or None when it is too old to go by."""
    import neck_state
    st = neck_state.status()
    if st["unknown"]:
        return None
    return {"pan": neck_state.last["pan"], "tilt": neck_state.last["tilt"], "moving": bool(neck_state.last["moving"]),
            "owner": neck_state.last["owner"], "tilt_band": neck_state.last["tilt_band"], "at": neck_state.last["at"]}


def gate(neck) -> str | None:
    """Why look-at may not move the neck now, or None. Blocking: run in an executor."""
    import neck_follow
    why = neck_follow.motion_gate()
    if why:
        return why
    if neck_follow.being_touched():
        return "the tablet is being touched"
    if neck and neck["owner"] in ("rc", "touch", "hold"):
        return f"the neck is held ({neck['owner']})"
    return None


def _send(pan_cmd: float, tilt_cmd: float):
    import head
    import neck_follow
    host = head.neck_host()
    if host is None:
        return None
    line = f"GAZE {pan_cmd:.2f} {tilt_cmd:.2f} {GAZE_LEASE_MS}"
    reply = neck_follow._ask(host, line, 0.2)
    session["sent"] = {"line": line, "reply": reply, "at": time.time()}
    return reply


def _gaze_off() -> None:
    import head
    import neck_follow
    host = head.neck_host()
    if host is not None:
        neck_follow._ask(host, "GAZE OFF", 0.2)


def _errors(aim_cmd, neck, k):
    """Per-axis real-degree errors (pan, tilt) between the aim and where the neck reports it is."""
    a = to_real_deg(*aim_cmd, k)
    n = to_real_deg(neck["pan"], neck["tilt"], k)
    return abs(a[0] - n[0]), abs(a[1] - n[1])


def _snapshot() -> dict:
    neck = _neck()
    k = _k()
    out = {k_: session[k_] for k_ in ("active", "owner", "target", "settled", "why", "err_deg", "t_settled", "holding",
                                      "blocked")}
    out["aim_deg"] = session["aim_deg"]
    if neck:
        pd, td = to_real_deg(neck["pan"], neck["tilt"], k)
        out.update(pan_deg=round(pd, 2), tilt_deg=round(td, 2), moving=neck["moving"], neck_owner=neck["owner"])
        try:
            out["limits"] = limits(neck["tilt_band"], k, description())
        except (OSError, ET.ParseError):
            out["limits"] = limits(neck["tilt_band"], k)
    else:
        out.update(pan_deg=None, tilt_deg=None, limits=None)
    return out


def _bad(field: str, why: str) -> JSONResponse:
    return JSONResponse({"error": f"{field}: {why}"}, status_code=400)


def _num(body: dict, name: str, default=None):
    v = body.get(name, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


async def _wait_still(after: float, deadline: float):
    """The first fresh neck report taken after `after` that says it is not moving, or None at the
    deadline (or "silent" if the neck stopped answering)."""
    while _clock() < deadline:
        neck = _neck()
        if neck is None:
            return "silent"
        if neck["at"] >= after and not neck["moving"]:
            return neck
        await asyncio.sleep(POLL_S)
    return None


@router.post("/api/head/look_at")
async def post_look_at(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return _bad("body", "send a JSON object")
    owner = body.get("owner")
    if not isinstance(owner, str) or not owner or not owner[0].isalpha() or not set(owner) <= OWNER_CHARS:
        return _bad("owner", "lower-case letters, digits and _, starting with a letter")
    frame = body.get("frame_id")
    if frame not in FRAMES:
        return _bad("frame_id", f"one of {', '.join(FRAMES)}: transform the point with tf2 first")
    xyz = [_num(body, a) for a in ("x", "y", "z")]
    if None in xyz:
        return _bad("x, y, z", "numbers, metres")
    camera = body.get("camera", "camera_link")
    lift_m, tol = _num(body, "lift_m", 0.0), _num(body, "tol_deg", 1.0)
    timeout_s, hold_s = _num(body, "timeout_s", 4.0), _num(body, "hold_s", 60.0)
    if lift_m is None or not 0 <= lift_m <= 1.0:
        return _bad("lift_m", "the lift's height, 0 to 1 m")
    if tol is None or not 0.1 <= tol <= 10:
        return _bad("tol_deg", "0.1 to 10 degrees")
    if timeout_s is None or not 0.5 <= timeout_s <= 10:
        return _bad("timeout_s", "0.5 to 10 s")
    if hold_s is None or not 1 <= hold_s <= MAX_HOLD_S:
        return _bad("hold_s", f"1 to {MAX_HOLD_S:.0f} s")
    if active() and session["owner"] != owner:
        return JSONResponse({"error": f"the head is aimed by {session['owner']}: it releases first"}, status_code=409)

    try:
        joints = description()
        ch = chain(joints, frame, camera)
    except (OSError, ET.ParseError) as e:
        return JSONResponse({"error": f"the robot description: {e}"}, status_code=503)
    except ValueError as e:
        return _bad("camera", str(e))
    names = {j["name"] for j in ch}
    if not {PAN_JOINT, TILT_JOINT} <= names:
        return _bad("camera", f"{camera} does not turn with the head")

    loop = asyncio.get_running_loop()
    k = _k()
    t0 = _clock()
    deadline = t0 + timeout_s

    def answer(settled: bool, err=None, **kw) -> JSONResponse:
        snap = _snapshot()
        return JSONResponse({"settled": settled, "pan_deg": snap["pan_deg"], "tilt_deg": snap["tilt_deg"],
                             "err_deg": err, **kw})

    neck = _neck()
    if neck is None:
        return answer(False, reason="neck_silent")
    sol = solve(ch, xyz, lift_m)
    lim = limits(neck["tilt_band"], k, joints)
    if sol is None:
        return answer(False, reason="out_of_range", need=None, limits=lim,
                      why="no aim reaches that point (too close to the camera?)")
    need = [round(math.degrees(sol[0]), 2), round(math.degrees(sol[1]), 2)]
    if not (lim["pan_deg"][0] <= need[0] <= lim["pan_deg"][1] and lim["tilt_deg"][0] <= need[1] <= lim["tilt_deg"][1]):
        return answer(False, reason="out_of_range", need={"pan_deg": need[0], "tilt_deg": need[1]}, limits=lim)
    why = await loop.run_in_executor(None, gate, neck)
    if why:
        return answer(False, reason=f"blocked: {why}")

    aim = tuple(round(v, 2) for v in to_cmd(sol[0], sol[1], k))
    session.update(active=True, owner=owner, until=t0 + hold_s, settled=False, err_deg=None, t_settled=None,
                   aim_cmd=aim, cmd=aim, aim_deg={"pan_deg": need[0], "tilt_deg": need[1]}, holding=False, blocked=None,
                   target={"frame_id": frame, "x": xyz[0], "y": xyz[1], "z": xyz[2], "camera": camera, "lift_m": lift_m},
                   why="aiming")
    _ensure_hold_task()

    # Aim, wait for the stop, nudge once if it stopped short, wait again.
    sends = [aim]
    for attempt in range(2):
        target = sends[-1]
        session["cmd"] = target                      # what the renewals carry until the next send
        sent_at = time.time()
        reply = await loop.run_in_executor(None, _send, *target)
        if not reply or not reply.startswith("OK"):
            session["why"] = f"the neck answered {reply!r}" if reply else "the neck did not answer GAZE"
            return answer(False, reason="neck_silent" if not reply else f"blocked: {session['why']}")
        session["holding"] = True
        still = await _wait_still(sent_at + 0.1, deadline)
        if still == "silent":
            return answer(False, reason="neck_silent")
        if still is None:
            session["why"] = "timeout"
            return answer(False, reason="timeout")
        ep, et = _errors(aim, still, k)
        session["err_deg"] = round(max(ep, et), 2)
        if max(ep, et) <= tol:
            session.update(cmd=aim, settled=True, t_settled=time.time(), why="settled")
            # Renewals carry the aim itself: inside the deadband, so the head stays where it stopped.
            await loop.run_in_executor(None, _send, *aim)
            return answer(True, session["err_deg"], t_settled=session["t_settled"])
        if attempt == 0:
            sends.append((nudge(aim[0], still["pan"]), nudge(aim[1], still["tilt"])))
    session.update(cmd=aim, why=f"stopped {session['err_deg']} deg from the aim (the neck's deadband)")
    await loop.run_in_executor(None, _send, *aim)
    return answer(False, session["err_deg"], reason="stopped_short", why=session["why"])


@router.post("/api/head/look_at/release")
async def post_release(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = None
    owner = body.get("owner") if isinstance(body, dict) else None
    if session["active"] and owner != session["owner"]:
        return JSONResponse({"error": f"the head is aimed by {session['owner']}"}, status_code=409)
    was = session["active"]
    _end("released")
    if was:
        await asyncio.get_running_loop().run_in_executor(None, _gaze_off)
    return JSONResponse({"ok": True, "released": was})


@router.get("/api/head/look_at")
def get_look_at() -> dict:
    active()
    return _snapshot()


# ---- the hold: renew the GAZE while the session lasts, and say so on ROS -----------------------------
_task = None
_ros = None


def _hold_tick() -> None:
    """One renewal. Blocking: run in an executor."""
    if not active() or session["cmd"] is None or not session["holding"]:
        return
    neck = _neck()
    # `blocked` says why the aim is not being held right now; `settled` stays the last look-at's answer.
    why = gate(neck) or (None if neck is not None else "the neck is not answering")
    session["blocked"] = why
    if why:
        return
    _send(*session["cmd"])


async def _run() -> None:
    global _ros
    import tablet_relay
    _ros = tablet_relay.Rosbridge(topics={TOPIC: "std_msgs/msg/String"})
    _ros.start()
    loop = asyncio.get_running_loop()
    last_pub = 0.0
    while True:
        try:
            await loop.run_in_executor(None, _hold_tick)
            now = _clock()
            if now - last_pub >= 0.2:
                last_pub = now
                await _ros.publish(TOPIC, {"data": json.dumps(_snapshot(), default=str)})
        except Exception as e:                       # noqa: BLE001 — never let the hold loop die
            log.debug("look_at: %s", e)
        await asyncio.sleep(RENEW_S)


def _ensure_hold_task() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_run())


async def start() -> None:
    _ensure_hold_task()
