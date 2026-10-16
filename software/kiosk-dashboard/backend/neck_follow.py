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
  - a fresh, confident detection (confidence ≥ CONF_MIN for its kind), further off than the eyes-only
    band (EYE_ONLY): small offsets are the eyes' to take (notes/design/head-turn-gaze.md).

AN ABSOLUTE BEARING, from where the head was WHEN THE FRAME WAS CAPTURED. The camera rides the head,
so an image offset is only true of the pose its frame was taken at, and frames reach here 0.3-1 s late
(detection, and the face page holding a cue for its dwell). Each sighting carries its age; the neck's
pose is kept for the last few seconds (read from NECK, which reports the slewed position, every
update); and the person's bearing is that pose plus their angle off the camera's axis. The head is
sent to the bearing, GAIN of the way (1: all of it; the neck slews at 70 °/s), clamped to the
autonomous envelope (±PAN_RANGE / ±TILT_RANGE around centre). A sighting re-sent while the head turns
gives the SAME bearing, so it never accumulates (2026-10-16: re-aiming a held offset ran pan to its
limit), and every sighting can be used, at the tracker's rate. When the person is lost the updates
stop, the lease runs out, and the head stays where it is, as the protocol says.

Every number is robot config with a safe default, so calibrating at the robot needs no code change
(OPENDUBS_NECK_FOLLOW_* in /etc/opendubs/robot.env, read through head.py):
  PAN_SIGN, TILT_SIGN  +1 or -1: which way a person right of / below centre turns the head. Both
                       default to -1, MEASURED at the robot. TILT: the neck's tilt RISES with its degrees
                       (2026-10-15, when its tilt band was commissioned) and the image's y grows
                       DOWNWARD, so a person below centre must lower the tilt. PAN: the neck's pan FALLS
                       toward the robot's right (watched, 2026-10-16), and the screen-side camera looks
                       out along the robot's gaze with its frames unmirrored, so a person right of the
                       image's centre is on the robot's right and must lower the pan. Seen closing the
                       loop on a person the same day.
  HFOV_DEG, VFOV_DEG   the screen-side camera's field of view
  GAIN                 the fraction of the way to the bearing per update (0.1..1, default 1)
  RATE_HZ              updates per second (default 10: faster than the tracker, so none is waited for)
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
# Per kind (2026-10-15, with the user at the robot): a PERSON is a detector's box, believed at ~0.5 when
# someone stands still, so the head never turned at 0.6. Motion and face keep the neck core's 0.6.
# The runaway and limit guards (below) stay the backstop whatever this lets through.
CONF_MIN_BY_KIND = {"person": 0.45, "motion": CONF_MIN, "face": CONF_MIN}
# Below this offset from where the head points now (a fraction of the half field of view, ~2.4° of pan)
# the head stays and the eyes take it: the head does the centring, the eyes the jitter. The user asked
# for the head to make "much more" effort to centre (2026-10-16); the Tab5's 0.25 left a person a
# third of the way to the edge with the head not trying.
EYE_ONLY = 0.08
NECK_PORT = 4242
POSE_KEEP_S = 4.0        # the neck's pose is remembered this long: longer than a held cue's dwell
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
            "hfov": _f("HFOV_DEG", 60, 20, 120), "vfov": _f("VFOV_DEG", 45, 15, 100),
            "gain": _f("GAIN", 1, 0.1, 1), "rate": _f("RATE_HZ", 10, 1, 10),
            "pan_range": _f("PAN_RANGE", 30, 0, 30), "tilt_range": _f("TILT_RANGE", 18, 0, 18)}


state = {"target": None, "target_at": 0.0, "why": "starting", "neck": None, "neck_at": 0.0, "sent": None,
         "sent_count": 0, "stopped": None, "limit_since": None,
         "tracker": None, "tracker_at": 0.0, "seen_at": None, "poses": [], "hist_seen": None, "bearing": None,
         "sighting": None, "prev_sighting": None}

# ---- the safety net: a wrong sign must not run the head into its limits ---------------------------
# The camera rides the head, so a wrong PAN_SIGN or TILT_SIGN is positive feedback: turning the wrong
# way puts the person further off-centre, which turns it further (seen on the robot 2026-10-15: both
# axes ran to their envelope limits and lost the person). Two guards, whatever the signs are:
#  - RUNAWAY, between two SIGHTINGS (a held one re-sent is not a new one). With the right sign a person
#    standing still has the same bearing whatever the head does, and a walker's bearing moves as they
#    do. With a wrong sign the computed bearing is (2 × the head's pose − the true one): it runs AWAY
#    as the head turns, twice as far. So: the head turned at least RUNAWAY_DEG between the two frames,
#    the bearing moved the SAME way by at least RUNAWAY_RATIO times as much, and the person stayed on
#    ONE side of the image (someone crossing the view flips sides: 2026-10-16, a crossing tripped the
#    old guard). It trips on the second sighting, before the head reaches the limit. Following stops
#    and stays stopped (latched, and said on /api/head/follow) until the tracking setting is changed.
#  - AT A LIMIT: a target pinned at the envelope's edge for LIMIT_HOLD_S is out of reach. Nothing is
#    sent (the lease runs out, the head stays) until the aim comes off the limit.
RUNAWAY_DEG, RUNAWAY_RATIO = 4.0, 1.5
LIMIT_HOLD_S = 2.0


def runaway(prev, cur) -> str | None:
    """The axis that is running away ("pan" / "tilt"), or None. `prev`, `cur`: two sightings, each
    (pan_at, tilt_at, pan_bearing, tilt_bearing, ox, oy): the head's pose when the frame was captured,
    the bearing worked out from it, and the image offset."""
    if prev is None or cur is None:
        return None
    for axis, ai, bi, oi in (("pan", 0, 2, 4), ("tilt", 1, 3, 5)):
        da, db = cur[ai] - prev[ai], cur[bi] - prev[bi]
        one_side = (cur[oi] > 0 and prev[oi] > 0) or (cur[oi] < 0 and prev[oi] < 0)
        if abs(da) >= RUNAWAY_DEG and da * db > 0 and abs(db) >= RUNAWAY_RATIO * abs(da) and one_side:
            return axis
    return None


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
    """From the face page: {"ix", "iy", "conf", "kind", "age_ms"} (image coords 0..1; age_ms: how long ago
    the frame behind it was captured), or None when it sees nobody."""
    if not isinstance(t, dict):
        state["target"] = None
        return
    try:
        ix, iy, conf = float(t["ix"]), float(t["iy"]), float(t["conf"])
    except (KeyError, TypeError, ValueError):
        return
    if 0 <= ix <= 1 and 0 <= iy <= 1 and 0 <= conf <= 1:
        now = _clock()
        state["target"], state["target_at"] = (ix, iy, conf), now
        age = t.get("age_ms")
        ok = isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= 10000
        state["seen_at"] = now - age / 1000 if ok else None
        k = t.get("kind")
        state["kind"] = k if k in ("face", "person", "motion") else "face"


def bearing(at, target, cfg: dict):
    """The person's (pan, tilt) bearing: the head's pose `at` when the frame was captured, plus their
    angle off the camera's axis."""
    ix, iy, _ = target
    ox, oy = (ix - 0.5) * 2, (iy - 0.5) * 2                               # -1..1, image right / down
    return (at[0] + cfg["pan_sign"] * ox * cfg["hfov"] / 2, at[1] + cfg["tilt_sign"] * oy * cfg["vfov"] / 2)


def aim(neck: dict, target, cfg: dict, kind: str = "face", at=None):
    """The (pan, tilt) to send, or (None, why not). Pure: the rules in the module's docstring.
    `at`: the head's (pan, tilt) when the frame was captured; by default where it is now."""
    conf = target[2]
    if conf < CONF_MIN_BY_KIND.get(kind, CONF_MIN):
        return None, "not confident enough"
    if neck.get("owner") in ("rc", "touch"):
        return None, f"the neck is held ({neck.get('owner')})"
    bp, bt = bearing(at or (neck["pan"], neck["tilt"]), target, cfg)
    dp, dt = bp - neck["pan"], bt - neck["tilt"]
    if abs(dp) <= EYE_ONLY * cfg["hfov"] / 2 and abs(dt) <= EYE_ONLY * cfg["vfov"] / 2:
        return None, "close enough: eyes only"
    pan = neck["pan"] + dp * cfg["gain"]
    tilt = neck["tilt"] + dt * cfg["gain"]
    pan = min(CENTRE + cfg["pan_range"], max(CENTRE - cfg["pan_range"], pan))
    tilt = min(CENTRE + cfg["tilt_range"], max(CENTRE - cfg["tilt_range"], tilt))
    return (round(pan, 1), round(tilt, 1)), None


def note_pose(t: float, pan: float, tilt: float) -> None:
    """The neck's pose at `t` (from NECK, which reports the slewed position), kept POSE_KEEP_S."""
    ps = [p for p in state["poses"] if p[0] >= t - POSE_KEEP_S and p[0] < t]
    state["poses"] = ps + [(t, pan, tilt)]


def pose_at(t: float):
    """The neck's (pan, tilt) at time `t`, between the poses kept; None if `t` is older than all of them."""
    ps = state["poses"]
    if not ps or t < ps[0][0] - 0.05:
        return None
    if t <= ps[0][0]:
        return ps[0][1], ps[0][2]
    for (t0, p0, q0), (t1, p1, q1) in zip(ps, ps[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
            return p0 + f * (p1 - p0), q0 + f * (q1 - q0)
    return ps[-1][1], ps[-1][2]


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


def robot_state():
    """robot_state's FSM state now ("SAFE_IDLE", "DEGRADED", ...), or None if it cannot be read."""
    import status
    return (status._robot_state() or {}).get("state")


def why_not_now():
    """Why the head must not follow right now (a reason), or None. Blocking: run in an executor."""
    import head
    import head_settings
    if head_settings.load().get("tracking") != "tablet":
        # Changing the setting is how an operator acknowledges a stop: it clears the latch.
        state.update(stopped=None, limit_since=None, hist_seen=None, sighting=None, prev_sighting=None)
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


def step(cfg: dict):
    """One update. Blocking (UDP, the runner): run in an executor. Returns why nothing was sent, or None."""
    import head
    why = why_not_now()
    if why:
        return why
    if state["stopped"]:
        return state["stopped"]
    # The neck's pose, every update while following is allowed: a sighting is placed by where the head
    # was when its frame was captured, and frames are late.
    host = head.neck_host()
    neck = parse_neck(_ask(host, "NECK") or "")
    state["neck"], state["neck_at"] = neck, _clock()
    if neck is None:
        return "the neck did not answer NECK"
    note_pose(state["neck_at"], neck["pan"], neck["tilt"])
    t = state["target"]
    if t is None or _clock() - state["target_at"] > TARGET_FRESH_S:
        return "nobody seen"
    seen = state["seen_at"]
    if seen is None:
        return "the face page sends no sighting time: reload it (the head aims by when a frame was taken)"
    at = pose_at(seen)
    if at is None:
        return "the sighting is older than the head's remembered pose"
    state["bearing"] = tuple(round(v, 1) for v in bearing(at, t, cfg))
    aimed, why = aim(neck, t, cfg, state.get("kind") or "face", at=at)
    if aimed is None:
        return why
    # Pinned at the envelope's edge: out of reach. Hold (send nothing) after LIMIT_HOLD_S.
    # (an axis with no range, as when the head is held at centre to measure the signs, is never "at a limit")
    at_limit = ((cfg["pan_range"] > 0 and abs(aimed[0] - CENTRE) >= cfg["pan_range"] - 0.05) or
                (cfg["tilt_range"] > 0 and abs(aimed[1] - CENTRE) >= cfg["tilt_range"] - 0.05))
    now = _clock()
    if at_limit:
        state["limit_since"] = state["limit_since"] or now
        if now - state["limit_since"] >= LIMIT_HOLD_S:
            return f"holding: the aim has been at the envelope's limit for {LIMIT_HOLD_S:.0f} s (out of reach, or a wrong sign)"
    else:
        state["limit_since"] = None
    # The runaway guard compares SIGHTINGS: a held one re-sent is the same bearing, not a new one.
    if state["hist_seen"] is None or abs(seen - state["hist_seen"]) > 1e-3:
        b = bearing(at, t, cfg)
        state["prev_sighting"], state["hist_seen"] = state["sighting"], seen
        state["sighting"] = (at[0], at[1], b[0], b[1], (t[0] - 0.5) * 2, (t[1] - 0.5) * 2)
    axis = runaway(state["prev_sighting"], state["sighting"])
    if axis:
        state["stopped"] = (f"STOPPED: the head ran away on {axis} (as it turned, the person's bearing ran away "
                            f"from it, twice as far): check OPENDUBS_NECK_FOLLOW_{axis.upper()}_SIGN. "
                            "Change the tracking setting to resume.")
        log.warning("neck follow: %s", state["stopped"])
        return state["stopped"]
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
    tr = state["tracker"]
    tracker = dict(tr, age_s=round(time.monotonic() - state["tracker_at"], 1)) if tr else None
    return {"why": state["why"], "stopped": state["stopped"], "tracker": tracker, "kind": state.get("kind"),
            "neck": state["neck"],
            "neck_age_s": round(_clock() - state["neck_at"], 1) if state["neck"] else None,
            "sent": state["sent"], "sent_count": state["sent_count"],
            "target": state["target"], "target_age_s": round(_clock() - state["target_at"], 2) if state["target"] else None,
            "sighting_age_s": round(_clock() - state["seen_at"], 2) if state["target"] and state["seen_at"] is not None else None,
            "bearing": state["bearing"],
            "config": config()}


def live_neck():
    """The neck's NECK right now (not the last one read before a send). Blocking: run in an executor."""
    import head
    host = head.neck_host()
    return parse_neck(_ask(host, "NECK") or "") if host else None
