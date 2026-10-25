"""The neck's live pan and tilt into ROS, so the arms' head-clearance limits can follow the head.

  /head/joint_states   sensor_msgs/msg/JointState, about RATE_HZ a second while the neck answers
      name      ["head_pan", "head_tilt"]: THE ROBOT DESCRIPTION'S joint names (description/opendubs/
                opendubs.urdf, the MJCF, sim/clearance.py), so a joint_state_publisher can merge this
                for robot_state_publisher (the Thor lane's; head_tilt_link carries the tablet's frames)
      position  radians from centre, the neck's commanded angles converted to the real ones, with the
                description's signs:
                  head_pan   axis +z: + turns the head to the robot's LEFT; 0 straight ahead
                  head_tilt  axis +y: + tips the face DOWN (+x swings toward -z); 0 level
      stamp     the Thor's clock when the neck answered
  /head/joint_targets  sensor_msgs/msg/JointState, the same joints and rate: where the neck is going.
      While the two differ the head is moving, and may be anywhere between them.
  /head/joint_limits   sensor_msgs/msg/JointState, LATCHED (transient local), re-sent when they change
      and every LIMITS_EVERY_S: the travel the neck's firmware enforces, as four "joints"
      head_pan_lower/_upper, head_tilt_lower/_upper, in the same radians and signs. Pan is the
      firmware's fixed 0-180 commanded; tilt is this robot's band (NVS, reported in NECK as tilt_band).

Through the local rosbridge, as one client (tablet_relay.Rosbridge). The neck is asked `NECK` over UDP
(head protocol); its reply carries the angles it is driving the servos to, slewed, which the servos
follow closely: there is no encoder to read.

NOTHING IS PUBLISHED WHEN THE NECK DOES NOT ANSWER, or when no neck is fitted: a consumer must treat a
gap of STALE_S or more (three missed periods) as "the head's angle is unknown", never as "unchanged".
That is the honest answer for a clearance limit; a stale angle is a confident wrong one.

THE HOLD (head protocol v9; firmware/neck_dial/neck/hold.h): NECK also says whether the head is held
(`hold=on`, by the knob or by software), or being released (`hold=releasing`: announced for 3 s before
it may move again). It is kept here for the dashboard's notice (GET /api/head/hold), and
POST /api/head/hold {"op": "here" | "off"} or {"pan": p, "tilt": t} holds or releases it from the
dashboard. A hold is a pause, never a motion: only `pan`/`tilt` move the head, at its own slew rate,
clamped to its limits.

Conversions, per robot (OPENDUBS_NECK_* in /etc/opendubs/robot.env, read through head.py):
  SERVO_SPAN_DEG  the servos' travel over 500-2500 us: 300 (goBILDA 2000-series), so a commanded degree
                  (0-180 over that pulse range) is 300/180 = 1.67 real degrees. From the servo's
                  specification, not measured on the robot.
  The directions: pan's commanded degrees FALL as the head turns to the robot's right (watched
  2026-10-16, neck_follow.py), so + left is (cmd - 90). Tilt's RISE as the face tips up (the same notes).
  Both were watched by eye, not measured with an instrument.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("neck_state")
router = APIRouter()

TOPIC = "/head/joint_states"
TARGETS = "/head/joint_targets"
LIMITS = "/head/joint_limits"
TYPE = "sensor_msgs/msg/JointState"
PAN_TRAVEL = (0.0, 180.0)                 # pantilt_core.h kRcPanMin / kRcPanMax
LIMITS_EVERY_S = 5.0
JOINTS = ["head_pan", "head_tilt"]
RATE_HZ = 20
STALE_S = 3 / RATE_HZ                    # what a consumer should call unknown (documented above)
CENTRE = 90.0
ASK_TIMEOUT_S = 0.08

_task = None
_ros = None
last = {"pan": None, "tilt": None, "at": None, "published": 0, "moving": None, "tilt_band": None,
        "owner": None, "hold": None, "hold_by": None}


def _span(env=None) -> float:
    import head
    try:
        v = float(head._setting(env or __import__("os").environ, "OPENDUBS_NECK_SERVO_SPAN_DEG") or 300)
    except (TypeError, ValueError):
        v = 300.0
    return v if 90 <= v <= 360 else 300.0


def to_radians(pan_cmd: float, tilt_cmd: float, span_deg: float = 300.0) -> tuple[float, float]:
    """The neck's commanded degrees (90 = centred) -> real radians from centre in the description's
    signs: pan + left; tilt + face DOWN, so the tilt's commanded degrees (which rise as it tips up)
    are negated."""
    k = span_deg / 180.0
    return (math.radians((pan_cmd - CENTRE) * k), -math.radians((tilt_cmd - CENTRE) * k))


def _stamp(now: float) -> dict:
    sec = math.floor(now)
    return {"sec": int(sec), "nanosec": int((now - sec) * 1e9) % 1_000_000_000}


def message(pan_cmd: float, tilt_cmd: float, now: float, span_deg: float = 300.0, names=None) -> dict:
    pan, tilt = to_radians(pan_cmd, tilt_cmd, span_deg)
    return {"header": {"stamp": _stamp(now), "frame_id": ""},
            "name": names or JOINTS, "position": [round(pan, 5), round(tilt, 5)], "velocity": [], "effort": []}


def limits_message(tilt_band: tuple[float, float], now: float, span_deg: float = 300.0) -> dict:
    a = to_radians(PAN_TRAVEL[0], tilt_band[0], span_deg)
    b = to_radians(PAN_TRAVEL[1], tilt_band[1], span_deg)
    pan_lo, pan_hi = sorted((a[0], b[0]))
    tilt_lo, tilt_hi = sorted((a[1], b[1]))          # negated tilt: the band's ends swap
    return {"header": {"stamp": _stamp(now), "frame_id": ""},
            "name": ["head_pan_lower", "head_pan_upper", "head_tilt_lower", "head_tilt_upper"],
            "position": [round(pan_lo, 5), round(pan_hi, 5), round(tilt_lo, 5), round(tilt_hi, 5)],
            "velocity": [], "effort": []}


def parse(reply: str):
    """`OK neck pan=… tilt=… target=p,t moving=… owner=… tilt_band=lo,hi …` → a dict, or None."""
    if not reply.startswith("OK neck"):
        return None
    kv = dict(p.split("=", 1) for p in reply.split()[2:] if "=" in p)
    try:
        tp, tt = (float(x) for x in kv["target"].split(","))
        lo, hi = (float(x) for x in kv["tilt_band"].split(","))
        return {"pan": float(kv["pan"]), "tilt": float(kv["tilt"]), "target": (tp, tt),
                "moving": kv.get("moving") == "1", "tilt_band": (lo, hi), "owner": kv.get("owner"),
                # a neck older than HOLD says nothing: not held
                "hold": kv.get("hold", "off"), "hold_by": kv.get("hold_by", "-")}
    except (KeyError, ValueError):
        return None


def _read():
    """One NECK, blocking: {pan, tilt, ...} or None."""
    import head
    import neck_follow
    host = head.neck_host()
    if host is None:
        return None
    return parse(neck_follow._ask(host, "NECK", ASK_TIMEOUT_S) or "")


async def run() -> None:
    global _ros
    import tablet_relay
    _ros = tablet_relay.Rosbridge(topics={TOPIC: TYPE, TARGETS: TYPE, LIMITS: TYPE}, latched={LIMITS})
    _ros.start()
    loop = asyncio.get_running_loop()
    span = _span()
    band, band_at = None, 0.0
    while True:
        t0 = time.monotonic()
        try:
            neck = await loop.run_in_executor(None, _read)
        except Exception as e:                       # noqa: BLE001 — never let this loop die
            log.debug("neck_state: %s", e)
            neck = None
        if neck is not None:
            now = time.time()
            last.update(pan=neck["pan"], tilt=neck["tilt"], at=now, moving=neck["moving"], tilt_band=neck["tilt_band"],
                        owner=neck["owner"], hold=neck["hold"], hold_by=neck["hold_by"])
            if await _ros.publish(TOPIC, message(neck["pan"], neck["tilt"], now, span)):
                last["published"] += 1
            await _ros.publish(TARGETS, message(*neck["target"], now, span))
            if neck["tilt_band"] != band or now - band_at >= LIMITS_EVERY_S:
                if await _ros.publish(LIMITS, limits_message(neck["tilt_band"], now, span)):
                    band, band_at = neck["tilt_band"], now
        await asyncio.sleep(max(0.0, 1 / RATE_HZ - (time.monotonic() - t0)))


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(run())


def status() -> dict:
    age = round(time.time() - last["at"], 2) if last["at"] else None
    return {"topic": TOPIC, "rate_hz": RATE_HZ, "pan_cmd": last["pan"], "tilt_cmd": last["tilt"],
            "moving": last["moving"], "tilt_band": last["tilt_band"],
            "age_s": age, "unknown": age is None or age >= STALE_S, "published": last["published"]}


# ---- the hold (head protocol v9) ---------------------------------------------------------------------
def hold_status() -> dict:
    """What the neck last said about its hold. `hold` is "on", "releasing", "off", or None when the
    neck's word is too old to go by (no neck, or it stopped answering)."""
    st = status()
    fresh = not st["unknown"]
    return {"hold": last["hold"] if fresh else None, "by": last["hold_by"] if fresh else None,
            "owner": last["owner"] if fresh else None, "pan_cmd": last["pan"], "tilt_cmd": last["tilt"],
            "age_s": st["age_s"]}


def hold_line(body) -> str | None:
    """The HOLD line for a request body, or None if it is not one."""
    if not isinstance(body, dict):
        return None
    if body.get("op") == "off":
        return "HOLD OFF"
    if body.get("op") == "here":
        return "HOLD HERE"
    p, t = body.get("pan"), body.get("tilt")
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (p, t)):
        return f"HOLD {p:.1f} {t:.1f}"
    return None


@router.get("/api/head/hold")
def get_hold() -> dict:
    return hold_status()


@router.post("/api/head/hold")
async def post_hold(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = None
    line = hold_line(body)
    if line is None:
        return JSONResponse({"error": 'send {"op": "here"|"off"} or {"pan": <deg>, "tilt": <deg>}'}, status_code=400)
    import head
    import neck_follow
    host = head.neck_host()
    if host is None:
        return JSONResponse({"error": "no neck is fitted"}, status_code=409)
    reply = await asyncio.get_running_loop().run_in_executor(None, neck_follow._ask, host, line, 0.5)
    if not reply or not reply.startswith("OK"):
        return JSONResponse({"error": f"the neck answered {reply!r}" if reply else "the neck did not answer"},
                            status_code=502)
    return JSONResponse({"ok": True, "sent": line, "reply": reply})
