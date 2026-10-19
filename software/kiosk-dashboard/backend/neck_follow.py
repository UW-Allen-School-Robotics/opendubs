"""The head follows the person the face is looking at: tablet tracking → the neck.

THIS MOVES THE NECK BY ITSELF. The face page tracks a person with the tablet's screen-side camera
(face-web/engine/track.js) and sends where it sees them over its door socket (face_door.py:
{"track": {"ix", "iy", "conf", "kind"}}, image coordinates 0..1, the newest face page only). Here
that becomes the neck's `GAZE <pan> <tilt> <lease_ms>` (head protocol §4), RATE_HZ times a second.

THE TAB5'S LAW, EXACTLY (firmware/tab5_idf/app_face/pantilt.cpp; the Dial's pantilt_core carries the
same). It worked on this robot, with these servos and a camera that rode the head as the tablet's
does, so it is re-applied rather than redesigned (the user's direction, 2026-10-16):

    a confident target (conf ≥ CONF_MIN for its kind) further off-centre than EYE_ONLY:
        pan  = 90 + PAN_SIGN  · nx · PAN_RANGE      (nx, ny: the target's image offset, -1..1)
        tilt = 90 + TILT_SIGN · ny · TILT_RANGE
    anything else (no one, not confident, inside the eyes-only band): centre, 90 / 90

and the neck slews there itself, at 70 °/s. The head's angle is a fixed function of where the person
appears, not a correction added to where it is: since the camera turns with the head, it settles
partway toward them, and a late or repeated sighting only repeats the same target. Nothing
accumulates, so nothing can run away. (Two integrating designs did, 2026-10-16: a held sighting
re-aimed again and again, then a bearing at full gain that swung, because these are 300° servos and
a commanded degree is ~1.67 real ones.)

It sends nothing at all, and the head stays where it is when the lease runs out, unless ALL hold:
  - the Head & face setting `tracking` is `tablet`;
  - a neck is fitted (OPENDUBS_HEAD_NECK_HOST, not `none`);
  - the robot is not in a reserved state (DEGRADED, E_STOP): the user's decision, 2026-10-16;
  - no show is loaded (the runner has no run): a show aims the head itself;
  - the neck is not held by a person: RC or touch own it (the neck says so in `NECK`).
And a target pinned at the envelope's edge for LIMIT_HOLD_S is out of reach: nothing is sent until it
comes off the edge.

Config, per robot (OPENDUBS_NECK_FOLLOW_* in /etc/opendubs/robot.env, read through head.py):
  PAN_SIGN, TILT_SIGN  which way a person right of / below centre turns the head. Both -1, measured:
                       pan FALLS toward the robot's right (watched 2026-10-16) and the tablet's frames
                       are unmirrored; tilt RISES with its degrees and image y grows downward. (The
                       Tab5's own nx had the opposite pan sense: its law reads 90 + nx·30.)
  PAN_RANGE, TILT_RANGE  the Tab5's 30 / 18 degrees either side of 90
  RATE_HZ              GAZEs a second (10)
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
import time

log = logging.getLogger("neck_follow")

CENTRE = 90.0
LEASE_MS = 600
TARGET_FRESH_S = 0.5
CONF_MIN = 0.6           # the Tab5's kHeadConfidence
# A PERSON is a detector's box, believed at ~0.5 when someone stands still (2026-10-15, #329).
CONF_MIN_BY_KIND = {"person": 0.45, "motion": CONF_MIN, "face": CONF_MIN}
EYE_ONLY = 0.25          # the Tab5's kEyeOnly: an offset this small is the eyes', and the head goes home
LIMIT_HOLD_S = 2.0
# A QUIET NECK (the user's call, 2026-10-19: the servos' noise is the main reason voice mishears and the
# wake word is missed, and the spotter listens exactly while the head tracks). The law above says where
# to look; this says when and how fast to go there, erring toward a still head over tight tracking:
#   - HOLD, sending nothing, when the person is near the middle of the picture or not confidently seen;
#     home (centre) only after nobody has been seen for NOBODY_HOME_S. The Tab5's "go home inside the
#     band" made the head nod between centre and the person; it no longer does.
#   - MOVE only when the target is at least MIN_MOVE_PAN / MIN_MOVE_TILT from where the head points.
#   - then SLOWLY, QUIET_DPS, in small steps at RATE_HZ (the neck's own slew is 70 °/s and GAZE carries no
#     speed), and stop sending once there: the lease lapses and the neck holds.
#   - and not at all while voice is listening (someone_talking()).
QUIET_DPS = 25.0
MIN_MOVE_PAN, MIN_MOVE_TILT = 6.0, 4.0
NOBODY_HOME_S = 3.0
NECK_PORT = 4242
_clock = time.monotonic


def _f(name: str, default: float, lo: float, hi: float) -> float:
    import head
    try:
        v = float(head._setting(os.environ, f"OPENDUBS_NECK_FOLLOW_{name}") or default)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def config() -> dict:
    sign = lambda v: -1.0 if v < 0 else 1.0                              # noqa: E731
    return {"pan_sign": sign(_f("PAN_SIGN", -1, -1, 1)), "tilt_sign": sign(_f("TILT_SIGN", -1, -1, 1)),
            "rate": _f("RATE_HZ", 20, 1, 20),
            "pan_range": _f("PAN_RANGE", 30, 0, 30), "tilt_range": _f("TILT_RANGE", 18, 0, 18)}


state = {"target": None, "target_at": 0.0, "kind": None, "why": "starting", "neck": None, "neck_at": 0.0,
         "sent": None, "sent_count": 0, "limit_since": None, "tracker": None, "tracker_at": 0.0,
         "cmd": None, "moving": False, "nobody_since": None}


def note_status(s) -> None:
    """From the face page every 2 s: its tracker's state, Hz, % found, frame size, last find."""
    if not isinstance(s, dict):
        return
    keep = {}
    if isinstance(s.get("state"), str):
        keep["state"] = s["state"][:160]
    if s.get("kind") in ("none", "motion", "person", "face"):
        keep["kind"] = s["kind"]
    if isinstance(s.get("person_model"), str):
        keep["person_model"] = s["person_model"][:160]
    c = s.get("cues")
    if isinstance(c, dict):
        keep["cues"] = {k: int(v) for k, v in c.items() if k in ("motion", "person", "face") and isinstance(v, int)}
    for k in ("hz", "found_pct", "last_found_s", "confidence"):
        if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool):
            keep[k] = s[k]
    f = s.get("frame")
    if isinstance(f, list) and len(f) == 2 and all(isinstance(x, int) for x in f):
        keep["frame"] = f
    state["tracker"], state["tracker_at"] = keep, time.monotonic()


def note_target(t) -> None:
    """From the face page: {"ix", "iy", "conf", "kind"} (image coords 0..1), or None when it sees nobody."""
    if not isinstance(t, dict):
        state["target"] = None
        return
    try:
        ix, iy, conf = float(t["ix"]), float(t["iy"]), float(t["conf"])
    except (KeyError, TypeError, ValueError):
        return
    if 0 <= ix <= 1 and 0 <= iy <= 1 and 0 <= conf <= 1:
        state["target"], state["target_at"] = (ix, iy, conf), _clock()
        k = t.get("kind")
        state["kind"] = k if k in ("face", "person", "motion") else "face"


def aim(target, cfg: dict, kind: str = "face"):
    """The Tab5's law: ((pan, tilt), why). `target` (ix, iy, conf) or None. Pure."""
    if target is None:
        return (CENTRE, CENTRE), "nobody seen: centre"
    ix, iy, conf = target
    if conf < CONF_MIN_BY_KIND.get(kind, CONF_MIN):
        return (CENTRE, CENTRE), "not confident enough: centre"
    nx, ny = (ix - 0.5) * 2, (iy - 0.5) * 2                               # -1..1, image right / down
    if math.hypot(nx, ny) <= EYE_ONLY:
        return (CENTRE, CENTRE), "close enough: the eyes take it, the head goes home"
    pr, tr = cfg["pan_range"], cfg["tilt_range"]
    pan = min(CENTRE + pr, max(CENTRE - pr, CENTRE + cfg["pan_sign"] * nx * pr))
    tilt = min(CENTRE + tr, max(CENTRE - tr, CENTRE + cfg["tilt_sign"] * ny * tr))
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


_RESOLVE_OK_S, _RESOLVE_RETRY_S = 30.0, 10.0
_resolved: dict = {}                    # host -> (address or None, when it was looked up)


def _resolve(host: str):
    """The neck's address, or None. Looked up by name (mDNS) at most every 30 s while it answers, and
    not again for 10 s after a failure: a lookup of an absent neck takes ~5 s, and at 10 updates a
    second that starved the loop. Never needs the internet: .local is answered on the robot's network."""
    addr, at = _resolved.get(host, (None, float("-inf")))
    now = time.monotonic()
    if now - at < (_RESOLVE_OK_S if addr else _RESOLVE_RETRY_S):
        return addr
    try:
        addr = socket.gethostbyname(host)
    except OSError:
        addr = None
    _resolved[host] = (addr, now)
    return addr


def _ask(host: str, line: str, timeout: float = 0.3):
    addr = _resolve(host)
    if addr is None:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(line.encode(), (addr, NECK_PORT))
        return s.recvfrom(512)[0].decode(errors="replace").strip()
    except OSError:
        return None
    finally:
        s.close()


def robot_state():
    """robot_state's FSM state now ("SAFE_IDLE", "DEGRADED", ...), or None if it cannot be read."""
    import status
    return (status._robot_state() or {}).get("state")


def why_not_now():
    """Why the head must not follow right now (a reason), or None. Blocking: run in an executor."""
    import head
    import head_settings
    if head_settings.load().get("tracking") != "tablet":
        state["limit_since"] = None
        return "tracking is not set to the tablet"
    if head.neck_host() is None:
        return "no neck is fitted"
    # Never in a RESERVED state (DEGRADED, E_STOP): the user's decision (2026-10-16), the same rule as
    # the reticle and a show's light. The head waits, and follows again by itself when the state clears:
    # nothing is latched. A state that cannot be read counts as reserved.
    import show_light
    try:
        rs = robot_state()
    except Exception:                                # noqa: BLE001 — unreadable means "do not move"
        rs = None
    if rs is None:
        return "the robot's state is unknown: the head waits"
    if rs in show_light.RESERVED:
        return f"the robot is {rs}: the head does not follow in a reserved state, and resumes when it clears"
    import kiosk_update                              # the runner's state: a show aims the head itself
    busy = kiosk_update.show_busy()                  # unknown counts as busy: a show must always win
    if busy:
        return busy
    return None


def someone_talking() -> bool:
    """True while the voice service is listening to someone (voice_proxy.listening(): the latest relayed
    `state` is "listen"). The head then HOLDS STILL: its servos' noise was getting into the microphone,
    and holding its gaze on whoever is speaking reads better (the user's idea, 2026-10-19). It moves
    again on think, speak or idle. Absent or broken, it says no: the head follows as before."""
    try:
        import voice_proxy
        f = getattr(voice_proxy, "listening", None)
        return bool(f and f())
    except Exception:                                # noqa: BLE001 — never let voice stop the head for good
        return False


def step(cfg: dict):
    """One update. Blocking (UDP, the runner): run in an executor. Returns why, or None when following."""
    import head
    why = why_not_now()
    if why:
        return why
    if someone_talking():                            # no GAZE at all: the lease lapses and the neck holds
        return "holding still: someone is talking to the robot"
    host = head.neck_host()
    neck = parse_neck(_ask(host, "NECK") or "")
    state["neck"], state["neck_at"] = neck, _clock()
    if neck is None:
        return "the neck did not answer NECK"
    if neck["owner"] in ("rc", "touch"):
        return f"the neck is held ({neck['owner']})"
    t = state["target"]
    if t is not None and _clock() - state["target_at"] > TARGET_FRESH_S:
        t = None
    aimed, why = aim(t, cfg, state.get("kind") or "face")
    now = _clock()
    if why is not None and not state["moving"]:
        if t is not None:                            # not confident, or near the middle: the eyes take it
            state["nobody_since"] = None
            return why.split(":")[0] + ": holding still"
        state["nobody_since"] = state["nobody_since"] or now
        if now - state["nobody_since"] < NOBODY_HOME_S:
            return "nobody seen: holding still"
    elif t is not None:
        state["nobody_since"] = None
    # Where the head points: what this last sent, unless someone else (RC, a show) has moved it since.
    here = (neck["pan"], neck["tilt"])
    if state["cmd"] is None or neck["owner"] not in ("gaze", "none"):
        state["cmd"] = here
    cmd = state["cmd"]
    if not state["moving"]:
        if abs(aimed[0] - cmd[0]) < MIN_MOVE_PAN and abs(aimed[1] - cmd[1]) < MIN_MOVE_TILT:
            return "close enough: holding still"
        state["moving"] = True
    # Pinned at the envelope's edge: out of reach. Hold (send nothing) after LIMIT_HOLD_S.
    at_limit = ((cfg["pan_range"] > 0 and abs(aimed[0] - CENTRE) >= cfg["pan_range"] - 0.05) or
                (cfg["tilt_range"] > 0 and abs(aimed[1] - CENTRE) >= cfg["tilt_range"] - 0.05))
    if at_limit:
        state["limit_since"] = state["limit_since"] or now
        if now - state["limit_since"] >= LIMIT_HOLD_S:
            state["moving"] = False
            return f"holding: the target has been at the envelope's edge for {LIMIT_HOLD_S:.0f} s (out of reach)"
    else:
        state["limit_since"] = None
    step_deg = QUIET_DPS / cfg["rate"]
    nxt = tuple(round(c + max(-step_deg, min(step_deg, a - c)), 2) for c, a in zip(cmd, aimed))
    reply = _ask(host, f"GAZE {nxt[0]} {nxt[1]} {LEASE_MS}")
    state["cmd"] = nxt
    state["moving"] = nxt != tuple(aimed)            # there: stop sending, the neck holds
    state["sent"], state["sent_count"] = {"gaze": nxt, "reply": reply}, state["sent_count"] + 1
    return why


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
    tr = state["tracker"]
    tracker = dict(tr, age_s=round(time.monotonic() - state["tracker_at"], 1)) if tr else None
    return {"why": state["why"], "tracker": tracker, "kind": state.get("kind"),
            "neck": state["neck"],
            "neck_age_s": round(_clock() - state["neck_at"], 1) if state["neck"] else None,
            "sent": state["sent"], "sent_count": state["sent_count"],
            "target": state["target"], "target_age_s": round(_clock() - state["target_at"], 2) if state["target"] else None,
            "config": config()}


def live_neck():
    """The neck's NECK right now (not the last one read before a send). Blocking: run in an executor."""
    import head
    host = head.neck_host()
    return parse_neck(_ask(host, "NECK") or "") if host else None
