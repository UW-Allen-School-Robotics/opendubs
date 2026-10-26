"""The Shows page's view of the show runner (choreography plan 4.4).

The runner (software/choreo/runner.py, `palette` mode) serves the operator's routines on its own
port. The dashboard forwards a small, fixed set of its calls under /api/choreo/ so the page talks to
ONE origin — which is what lets the same page work over plain HTTP and over HTTPS (standing rule
12) with no cross-origin rules.

Read-only except "run a routine" (with how it starts: `start` deadman|countdown|now and
`countdown_s`, nothing else) and "stop". What a routine may do is the runner's business: on the
robot it drives the real arms (plan 5.6); without arm output it drives only the twins, and the page
says which.

The other direction is /api/choreo/robot: the robot's state as the runner's preflight and abort
checks need it (plan 4.4c), from this dashboard's own rollup, so the runner and the status page
cannot disagree. READ-ONLY: it writes nothing, to the StamPLC or anywhere else. Anything it cannot
vouch for right now is null, and the runner treats null as "refuse" or "end the show", never as OK.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

RUNNER_URL = os.environ.get("OPENDUBS_RUNNER_URL", "http://127.0.0.1:8092")
TIMEOUT_S = 2.0
# The routine list validates every show on each request: allow it longer, so a busy robot answers
# late instead of not at all (and the page does not re-ask while the runner is still working).
LIST_TIMEOUT_S = 15.0

# Only the routes below are forwarded (the routines, a run, stop, and the projector's status and
# output). Nothing else on the runner is reachable this way — in particular not its fake-robot
# switches (/api/sim/*), which exist for twin testing only.
router = APIRouter()

# Show audio (plan 4.2) lives in its own module; its routes ride on this router so app.py needs no
# change to carry them.
import show_audio  # noqa: E402
router.routes.extend(show_audio.router.routes)


def _call(method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> tuple[int, object]:
    data = (json.dumps(body).encode() if body else b"") if method == "POST" else None
    req = urllib.request.Request(f"{RUNNER_URL}/api/{path}", method=method, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:          # the runner's own refusal: pass it on, with its words
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, {"error": f"runner answered {e.code}"}
    except (urllib.error.URLError, OSError, ValueError):
        return 503, {"error": "the show runner is not running", "runner": RUNNER_URL}


async def _forward(method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> JSONResponse:
    code, body = await asyncio.get_running_loop().run_in_executor(None, _call, method, path, body, timeout)
    return JSONResponse(body, status_code=code)


@router.get("/api/choreo/routines")
async def routines():
    return await _forward("GET", "routines", timeout=LIST_TIMEOUT_S)


@router.get("/api/choreo/video")
async def video():
    """The projector (notes/design/choreo-video.md): each output found / ready and why, the output
    selected, and the video playing with its sync error. The Shows page's projector card reads it."""
    return await _forward("GET", "video")


@router.post("/api/choreo/video/output")
async def video_output(request: Request):
    """The operator's play-time choice of video output: {output: cast|hdmi|stage|off|null}. Returns the
    whole GET body; refused while a show plays (SHOW_VIDEO_BUSY)."""
    try:
        raw = await request.json() if await request.body() else {}
    except ValueError:
        return JSONResponse({"error": "the body must be JSON: {output}"}, status_code=400)
    if not isinstance(raw, dict) or "output" not in raw:
        return JSONResponse({"error": "the body must be {output: cast|hdmi|stage|off|null}"}, status_code=400)
    return await _forward("POST", "video/output", {"output": raw["output"]})


@router.post("/api/choreo/video/settings")
async def video_settings(request: Request):
    """The projector's offsets and how it takes music ({audio_lead_s, video_lead_s, music_as}, and the
    stage page's own stage_audio_lead_s, stage_video_lead_s, and stage_audio_leads_s, its audio lead per sound
    output, merged by output; any of them), set at the robot by ear and
    stored there (notes/design/choreo-audio.md). Returns the whole GET body."""
    try:
        raw = await request.json() if await request.body() else {}
    except ValueError:
        return JSONResponse({"error": "the body must be JSON"}, status_code=400)
    keys = ("audio_lead_s", "video_lead_s", "music_as", "stage_audio_lead_s", "stage_video_lead_s", "stage_buffer_s",
            "stage_audio_leads_s")
    if not isinstance(raw, dict) or not any(k in raw for k in keys):
        return JSONResponse({"error": f"the body must set one of {', '.join(keys)}"}, status_code=400)
    return await _forward("POST", "video/settings", {k: raw[k] for k in keys if k in raw})


@router.get("/api/choreo/palette")
async def palette():
    return await _forward("GET", "palette")


RUN_KEYS = ("start", "countdown_s", "skip")     # how a run starts; the runner checks the values


@router.post("/api/choreo/run/{rid}")
async def run(rid: str, request: Request):
    """On a robot whose runner has the arms, the real arms follow every run (plan 5.6); the runner's
    preflight gates it on CH5 AUTO, the held deadman and the arms being active. The body may say how
    the run starts ({start: deadman|countdown|now, countdown_s}), and `skip`: the parts the operator
    approved running without (a can_skip variant, offered by a NEEDS_APPROVAL refusal); only those keys are passed on."""
    if not rid.replace("-", "").replace("_", "").isalnum():
        return JSONResponse({"error": "not a routine id"}, status_code=400)
    try:
        raw = await request.json() if await request.body() else {}
    except ValueError:
        return JSONResponse({"error": "the body must be JSON: {start, countdown_s}"}, status_code=400)
    body = {k: raw[k] for k in RUN_KEYS if isinstance(raw, dict) and raw.get(k) is not None}
    return await _forward("POST", f"run/{rid}", body or None)


@router.post("/api/choreo/stop")
async def stop():
    return await _forward("POST", "stop")


# ---- the robot's state, for the runner ----------------------------------------------------------
# CH5 as the choreography format names it (cue.CH5_MODES), from the rollup's authority names.
CH5_NAME = {"OFF": "STANDBY", "RC_MANUAL": "MANUAL", "AUTONOMOUS": "AUTO"}
PROBE_MAX_AGE_S = 10.0     # an arms/head answer older than this is no answer


# Every lift command an operator sends THROUGH THIS DASHBOARD (/api/mast without `by=show`: the
# StamPLC page's Z-AXIS HOLD buttons, the ROS /zaxis triggers) counts here. A show's lift output
# stands down for the rest of its run when this moves — the operator wins, as the RC stick does in
# the firmware. The head's RAISE/LOWER go straight to the StamPLC and are not counted
# (show_outputs.py says what that means for a show).
_mast_operator = {"seq": 0}
_mast_lock = threading.Lock()


def note_operator_mast() -> None:
    with _mast_lock:
        _mast_operator["seq"] += 1


def operator_mast_seq() -> int:
    with _mast_lock:
        return _mast_operator["seq"]


def robot_view(rs: dict, saf: dict, rc: dict, arms=None, head=None, effectors=None) -> dict:
    """The runner's fields from the dashboard's robot-state rollup (`rs`), safety (`saf`) and RC
    link (`rc`). Pure, so the mapping is tested without a robot. None means UNKNOWN."""
    inp = rs.get("inputs") or {}
    rc_ok = bool(rc.get("fresh"))
    faults = list(inp.get("faults") or [])
    if rs.get("state") == "INIT":
        faults.append("the robot is still starting up")
    if inp.get("charging"):
        faults.append("charging — drive inhibited")
    return {
        # The rollup counts a stale StamPLC link as an e-stop; for the runner it is UNKNOWN, which
        # ends a show just the same and says truthfully why.
        "estop": bool(inp.get("estopped")) if saf.get("fresh") else None,
        "ch5": CH5_NAME.get(inp.get("authority")) if rc_ok else None,
        "deadman": (bool(inp["deadman"]) if inp.get("deadman") is not None else None) if rc_ok else None,
        "fault": "; ".join(faults) or None,
        "motion_power": saf.get("motion_enabled") if saf.get("fresh") else None,
        "arms": arms,
        "head": head,         # the head tablet showing its face page, and no call holding it (_probe_now)
        # Per part (cue.EFFECTORS names): the Arms lane's openarm.effector_status(), when this
        # dashboard has it. With it the runner checks each part a show needs and offers a can_skip
        # variant; without it, the all-or-nothing `arms` above decides, as before.
        **({"effectors": effectors} if isinstance(effectors, dict) else {}),
        "arm_pose": {"ARM_L": {}, "ARM_R": {}},    # not measured yet: live output (5.6) must
        "mast_operator_seq": operator_mast_seq(),
    }


def arms_from_summary(text: str, err) -> str | None:
    """`enabled=16 disabled=0 faults=0 missing=0` -> "active" | "inactive" | None.

    APPROXIMATION, recorded in the plan's stub register: "active" here means every arm motor is
    enabled and none faulted or missing. It does not read whether a controller is commanding them;
    live output (5.6) must."""
    if err or not text:
        return None
    n = {k: int(v) for k, _, v in (t.partition("=") for t in text.split()) if v.isdigit()}
    if "enabled" not in n:
        return None
    clean = n.get("disabled", 0) == 0 and n.get("faults", 0) == 0 and n.get("missing", 0) == 0
    return "active" if n["enabled"] > 0 and clean else "inactive"


def arms_from_effectors(eff: dict) -> str:
    """The all-or-nothing `arms` field from the per-part status, for a runner that predates it:
    "active" when every arm is ok and every gripper is ok or deliberately excluded (the summary
    stops counting an excluded gripper as missing, too)."""
    ok = all((eff.get(f"arm_{s}") or {}).get("state") == "ok" for s in ("left", "right")) and all(
        (g := eff.get(f"grip_{s}") or {}).get("state") == "ok" or g.get("excluded") for s in ("left", "right"))
    return "active" if ok else "inactive"


_probe = {"arms": (None, 0.0), "head": (None, 0.0), "effectors": (None, 0.0)}
_probe_lock = threading.Lock()


def _probe_now():
    """Ask the arms and the head, ON DEMAND only (the runner asks once, at preflight). The arm
    motor read goes over the arms' own CAN bus, which is why nothing polls it continuously; the
    head is asked with PING, which only reports."""
    import openarm
    import state_out
    effectors = None
    if hasattr(openarm, "effector_status"):          # the Arms lane's per-part status (#403 section 6)
        try:
            effectors = openarm.effector_status()
        except Exception:  # noqa: BLE001  unknown parts are left to the all-or-nothing arms field
            effectors = None
    if isinstance(effectors, dict):
        arms = arms_from_effectors(effectors)       # one motor read per probe, not two
    else:                                           # None: the motors could not be read that way
        m = openarm._motor_summary()
        arms = arms_from_summary(m.get("text") or "", m.get("error"))
    head = head_up(state_out.face_addr(), state_out._TAB5_UDP_PORT)
    now = time.monotonic()
    with _probe_lock:
        _probe["arms"], _probe["head"], _probe["effectors"] = (arms, now), (head, now), (effectors, now)


def head_up(addr, port) -> bool:
    """Is the head ready for a show: the head tablet showing its face page, with no video call
    holding the tablet? The face door (face_door.py) answers PING even with no page connected, as
    `face=absent`, so a PONG alone says only that the door is there. A call holds the tablet, and the
    show's face and music could not play on it."""
    import tablet_relay
    if tablet_relay.call_busy():
        return False
    if not addr:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    try:
        for _ in range(2):                       # UDP: one retry before calling the head down
            s.sendto(b"PING\n", (addr, port))
            try:
                reply = s.recvfrom(512)[0]
            except socket.timeout:
                continue
            return reply.startswith(b"PONG") and b"face=absent" not in reply
    except OSError:
        pass
    finally:
        s.close()
    return False


def _probed(key):
    with _probe_lock:
        v, at = _probe[key]
    return v if time.monotonic() - at < PROBE_MAX_AGE_S else None


def _robot_now() -> dict:
    import status
    return robot_view(status._robot_state(), status._safety(), status._rc_link(),
                      _probed("arms"), _probed("head"), _probed("effectors"))


@router.get("/api/choreo/robot")
async def robot(probe: int = 0):
    loop = asyncio.get_running_loop()
    if probe:
        await loop.run_in_executor(None, _probe_now)
    return JSONResponse(await loop.run_in_executor(None, _robot_now))
