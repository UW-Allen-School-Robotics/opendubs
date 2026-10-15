"""The head follows the person the face is looking at: tablet tracking → the neck.

THIS MOVES THE NECK BY ITSELF. The face page tracks a person with the tablet's screen-side camera
(face-web/engine/track.js) and sends where it sees them over its door socket (face_door.py:
{"track": {"ix", "iy", "conf"}}, image coordinates 0..1, the newest face page only). Here those
become the neck's `GAZE <pan> <tilt> <lease_ms>` (head protocol §4), a few times a second, only when
ALL of these hold:

  - the Head & face setting `tracking` is `tablet`;
  - a neck is fitted (OPENDUBS_HEAD_NECK_HOST, not `none`);
  - no show is loaded (the runner has no run): a show aims the head itself;
  - the neck is not held by a person: RC or touch own it (the neck says so in `NECK`), and on the
    neck RC outranks GAZE anyway;
  - a fresh, confident detection (confidence ≥ CONF_MIN), outside the eyes-only band (|offset| >
    EYE_ONLY): a glance moves the eyes, never the head (notes/design/head-turn-gaze.md);
  - the neck is not moving: the camera turns with the head, so a frame taken mid-turn is not used.

The target is the neck's angle now plus the person's angle off the camera's axis, scaled by GAIN (so
it closes in over a few updates instead of overshooting) and clamped to the autonomous envelope
(±PAN_RANGE / ±TILT_RANGE around centre, the neck's own tracker limits). When the person is lost the
updates stop, the lease runs out, and the head stays where it is, as the protocol says.

Every number is robot config with a safe default, so calibrating at the robot needs no code change
(OPENDUBS_NECK_FOLLOW_* in /etc/opendubs/robot.env, read through head.py):
  PAN_SIGN, TILT_SIGN  +1 or -1: which way a person right of / below centre turns the head
  HFOV_DEG, VFOV_DEG   the screen-side camera's field of view
  GAIN                 the fraction of the angle closed per update (0.1..1)
  RATE_HZ              updates per second
  PAN_RANGE, TILT_RANGE  the envelope, degrees either side of 90

When the neck's own `TRACK` is built (firmware/neck_dial), this should send that instead: it ranks
below a show's GAZE on the neck itself.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time

log = logging.getLogger("neck_follow")

CENTRE = 90.0
LEASE_MS = 600
TARGET_FRESH_S = 0.5
CONF_MIN = 0.6           # the neck core's kHeadConfidence
EYE_ONLY = 0.25          # the neck core's kEyeOnly: below this offset the head never moves
NECK_PORT = 4242


def _f(name: str, default: float, lo: float, hi: float) -> float:
    import head
    try:
        v = float(head._setting(os.environ, f"OPENDUBS_NECK_FOLLOW_{name}") or default)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def config() -> dict:
    sign = lambda v: -1.0 if v < 0 else 1.0                              # noqa: E731
    return {"pan_sign": sign(_f("PAN_SIGN", 1, -1, 1)), "tilt_sign": sign(_f("TILT_SIGN", 1, -1, 1)),
            "hfov": _f("HFOV_DEG", 60, 20, 120), "vfov": _f("VFOV_DEG", 45, 15, 100),
            "gain": _f("GAIN", 0.5, 0.1, 1), "rate": _f("RATE_HZ", 5, 1, 10),
            "pan_range": _f("PAN_RANGE", 30, 0, 30), "tilt_range": _f("TILT_RANGE", 18, 0, 18)}


state = {"target": None, "target_at": 0.0, "why": "starting", "neck": None, "sent": None, "sent_count": 0}


def note_target(t) -> None:
    """From the face page: {"ix", "iy", "conf"} (image coords 0..1), or None when it sees nobody."""
    if not isinstance(t, dict):
        state["target"] = None
        return
    try:
        ix, iy, conf = float(t["ix"]), float(t["iy"]), float(t["conf"])
    except (KeyError, TypeError, ValueError):
        return
    if 0 <= ix <= 1 and 0 <= iy <= 1 and 0 <= conf <= 1:
        state["target"], state["target_at"] = (ix, iy, conf), time.monotonic()


def aim(neck: dict, target, cfg: dict):
    """The (pan, tilt) to send, or (None, why not). Pure: the rules in the module's docstring."""
    ix, iy, conf = target
    if conf < CONF_MIN:
        return None, "not confident enough"
    ox, oy = (ix - 0.5) * 2, (iy - 0.5) * 2                               # -1..1, image right / down
    if max(abs(ox), abs(oy)) <= EYE_ONLY:
        return None, "close enough: eyes only"
    if neck.get("owner") in ("rc", "touch"):
        return None, f"the neck is held ({neck.get('owner')})"
    if neck.get("moving"):
        return None, "the neck is moving"
    pan = neck["pan"] + cfg["pan_sign"] * ox * cfg["hfov"] / 2 * cfg["gain"]
    tilt = neck["tilt"] + cfg["tilt_sign"] * oy * cfg["vfov"] / 2 * cfg["gain"]
    pan = min(CENTRE + cfg["pan_range"], max(CENTRE - cfg["pan_range"], pan))
    tilt = min(CENTRE + cfg["tilt_range"], max(CENTRE - cfg["tilt_range"], tilt))
    return (round(pan, 1), round(tilt, 1)), None


def parse_neck(reply: str):
    """`OK neck pan=90.0 tilt=90.0 target=… moving=0 owner=none` → {pan, tilt, moving, owner}."""
    if not reply.startswith("OK neck"):
        return None
    kv = dict(p.split("=", 1) for p in reply.split()[2:] if "=" in p)
    try:
        return {"pan": float(kv["pan"]), "tilt": float(kv["tilt"]), "moving": kv.get("moving") == "1",
                "owner": kv.get("owner", "none")}
    except (KeyError, ValueError):
        return None


def _ask(host: str, line: str, timeout: float = 0.3):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(line.encode(), (socket.gethostbyname(host), NECK_PORT))
        return s.recvfrom(512)[0].decode(errors="replace").strip()
    except OSError:
        return None
    finally:
        s.close()


def why_not_now():
    """Why the head must not follow right now (a reason), or None. Blocking: run in an executor."""
    import head
    import head_settings
    if head_settings.load().get("tracking") != "tablet":
        return "tracking is not set to the tablet"
    if head.neck_host() is None:
        return "no neck is fitted"
    import kiosk_update                              # the runner's state: a show aims the head itself
    busy = kiosk_update.show_busy()                  # unknown counts as busy: a show must always win
    if busy:
        return busy
    return None


def step(cfg: dict):
    """One update. Blocking (UDP, the runner): run in an executor. Returns why nothing was sent, or None."""
    import head
    why = why_not_now()
    if why:
        return why
    t = state["target"]
    if t is None or time.monotonic() - state["target_at"] > TARGET_FRESH_S:
        return "nobody seen"
    host = head.neck_host()
    neck = parse_neck(_ask(host, "NECK") or "")
    state["neck"] = neck
    if neck is None:
        return "the neck did not answer NECK"
    aimed, why = aim(neck, t, cfg)
    if aimed is None:
        return why
    reply = _ask(host, f"GAZE {aimed[0]} {aimed[1]} {LEASE_MS}")
    state["sent"], state["sent_count"] = {"gaze": aimed, "reply": reply}, state["sent_count"] + 1
    return None


async def run() -> None:
    loop = asyncio.get_running_loop()
    while True:
        cfg = config()
        try:
            state["why"] = await loop.run_in_executor(None, step, cfg) or "following"
        except Exception as e:                       # noqa: BLE001 — never let the loop die
            state["why"] = f"error: {e}"
        await asyncio.sleep(1 / cfg["rate"])


_task = None


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(run())


def status() -> dict:
    return {"why": state["why"], "neck": state["neck"], "sent": state["sent"], "sent_count": state["sent_count"],
            "target": state["target"], "target_age_s": round(time.monotonic() - state["target_at"], 2) if state["target"] else None,
            "config": config()}
