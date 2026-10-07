"""The Shows page's view of the show runner (choreography plan 4.4).

The runner (software/choreo/runner.py, `palette` mode) serves the operator's routines on its own
port. The dashboard forwards a small, fixed set of its calls under /api/choreo/ so the page talks to
ONE origin — which is what lets the same page work over plain HTTP and over HTTPS (standing rule
12) with no cross-origin rules.

Read-only except "run a routine" and "stop". What a routine may do is the runner's business, and
until live output exists (plan 5.6) the runner drives only the twins: the page says so.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse

RUNNER_URL = os.environ.get("OPENDUBS_RUNNER_URL", "http://127.0.0.1:8092")
TIMEOUT_S = 2.0

# Only the four routes below are forwarded. Nothing else on the runner is reachable this way — in
# particular not its fake-robot switches (/api/sim/*), which exist for twin testing only.
router = APIRouter()


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
