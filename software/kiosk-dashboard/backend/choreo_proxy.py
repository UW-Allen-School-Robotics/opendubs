"""The Shows page's view of the show runner (choreography plan 4.4).

The runner (software/choreo/runner.py, `palette` mode) serves the operator's routines on its own
port. The dashboard forwards a small, fixed set of its calls under /api/choreo/ so the page talks to
ONE origin — which is what lets the same page work over plain HTTP and over HTTPS (standing rule
12) with no cross-origin rules.

Read-only except "run a routine" and "stop". What a routine may do is the runner's business, and
until live output exists (plan 5.6) the runner drives only the twins: the page says so.

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

from fastapi import APIRouter
from fastapi.responses import JSONResponse

RUNNER_URL = os.environ.get("OPENDUBS_RUNNER_URL", "http://127.0.0.1:8092")
TIMEOUT_S = 2.0

# Only the four routes below are forwarded. Nothing else on the runner is reachable this way — in
# particular not its fake-robot switches (/api/sim/*), which exist for twin testing only.
router = APIRouter()

# Show audio (plan 4.2) lives in its own module; its routes ride on this router so app.py needs no
# change to carry them.
import show_audio  # noqa: E402
router.routes.extend(show_audio.router.routes)


def _call(method: str, path: str) -> tuple[int, object]:
    req = urllib.request.Request(f"{RUNNER_URL}/api/{path}", method=method,
                                 data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:          # the runner's own refusal: pass it on, with its words
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, {"error": f"runner answered {e.code}"}
    except (urllib.error.URLError, OSError, ValueError):
        return 503, {"error": "the show runner is not running", "runner": RUNNER_URL}


async def _forward(method: str, path: str) -> JSONResponse:
    code, body = await asyncio.get_running_loop().run_in_executor(None, _call, method, path)
    return JSONResponse(body, status_code=code)


@router.get("/api/choreo/routines")
async def routines():
    return await _forward("GET", "routines")


@router.get("/api/choreo/palette")
async def palette():
    return await _forward("GET", "palette")


@router.post("/api/choreo/run/{rid}")
async def run(rid: str):
    if not rid.replace("-", "").replace("_", "").isalnum():
        return JSONResponse({"error": "not a routine id"}, status_code=400)
    return await _forward("POST", f"run/{rid}")


@router.post("/api/choreo/stop")
async def stop():
    return await _forward("POST", "stop")


# ---- the robot's state, for the runner ----------------------------------------------------------
# CH5 as the choreography format names it (cue.CH5_MODES), from the rollup's authority names.
CH5_NAME = {"OFF": "STANDBY", "RC_MANUAL": "MANUAL", "AUTONOMOUS": "AUTO"}
PROBE_MAX_AGE_S = 10.0     # an arms/head answer older than this is no answer


def robot_view(rs: dict, saf: dict, rc: dict, arms=None, tab5=None) -> dict:
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
        "tab5": tab5,
        "arm_pose": {"ARM_L": {}, "ARM_R": {}},    # not measured yet: live output (5.6) must
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


_probe = {"arms": (None, 0.0), "tab5": (None, 0.0)}
_probe_lock = threading.Lock()


def _probe_now():
    """Ask the arms and the head, ON DEMAND only (the runner asks once, at preflight). The arm
    motor read goes over the arms' own CAN bus, which is why nothing polls it continuously; the
    head is asked with PING, which only reports."""
    import openarm
    import state_out
    m = openarm._motor_summary()
    arms = arms_from_summary(m.get("text") or "", m.get("error"))
    tab5 = False
    addr = state_out.face_addr()          # PING is the face's to answer
    if addr:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            for _ in range(2):                   # UDP: one retry before calling the head down
                s.sendto(b"PING\n", (addr, state_out._TAB5_UDP_PORT))
                try:
                    if s.recvfrom(512)[0].startswith(b"PONG"):
                        tab5 = True
                        break
                except socket.timeout:
                    pass
        except OSError:
            pass
        finally:
            s.close()
    now = time.monotonic()
    with _probe_lock:
        _probe["arms"], _probe["tab5"] = (arms, now), (tab5, now)


def _probed(key):
    with _probe_lock:
        v, at = _probe[key]
    return v if time.monotonic() - at < PROBE_MAX_AGE_S else None


def _robot_now() -> dict:
    import status
    return robot_view(status._robot_state(), status._safety(), status._rc_link(),
                      _probed("arms"), _probed("tab5"))


@router.get("/api/choreo/robot")
async def robot(probe: int = 0):
    loop = asyncio.get_running_loop()
    if probe:
        await loop.run_in_executor(None, _probe_now)
    return JSONResponse(await loop.run_in_executor(None, _robot_now))
