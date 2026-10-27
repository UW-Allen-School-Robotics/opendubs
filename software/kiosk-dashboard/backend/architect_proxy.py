"""The ARCHITECT tab's view of the robot's ARCHITECT services (software/architect on daphne/halloween).

Three loopback services, each optional, each forwarded READ-ONLY under /api/architect/, so the page
talks to one origin (standing rule 12) and nothing here can start, approve or stop anything:

  * /api/architect/runner         the demo runner's state (ARCHITECT's program: code, summary, sha,
                                   approved or not, runs, events): demo_runner, 127.0.0.1:8098/api/state
  * /api/architect/listener?after=N  what the Jabra listener heard and did, and the program a show was
                                   recorded from: listener, 127.0.0.1:8099/api/feed
  * /api/architect/frame.jpg      the Orbbec's colour image, 640 wide: camera_feed, 127.0.0.1:8097/frame.jpg
                                   (it subscribes only while asked, so the tab costs nothing when closed)

A service that is not running answers 503 with {"error": ...}, and the page says so.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()

RUNNER = os.environ.get("OPENDUBS_ARCHITECT_RUNNER_URL", "http://127.0.0.1:8098")
LISTENER = os.environ.get("OPENDUBS_ARCHITECT_LISTENER_URL", "http://127.0.0.1:8099")
CAMERA = os.environ.get("OPENDUBS_ARCHITECT_CAMERA_URL", "http://127.0.0.1:8097")
TIMEOUT_S = 2.0


def _get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b"", e.headers.get("Content-Type", "text/plain")
    except (urllib.error.URLError, OSError, ValueError):
        return 503, b"", ""


async def _fetch(url: str):
    return await asyncio.get_running_loop().run_in_executor(None, _get, url)


def _json(code: int, body: bytes, what: str) -> JSONResponse:
    if code != 200:
        return JSONResponse({"error": f"the ARCHITECT {what} is not running"}, status_code=503)
    try:
        return JSONResponse(json.loads(body or b"null"))
    except ValueError:
        return JSONResponse({"error": f"the ARCHITECT {what} sent something that is not JSON"}, status_code=502)


@router.get("/api/architect/runner")
async def runner():
    code, body, _ = await _fetch(f"{RUNNER}/api/state")
    return _json(code, body, "runner")


@router.get("/api/architect/listener")
async def listener(after: int = 0):
    code, body, _ = await _fetch(f"{LISTENER}/api/feed?after={max(0, int(after))}")
    return _json(code, body, "listener")


@router.get("/api/architect/frame.jpg")
async def frame(request: Request):
    code, body, ctype = await _fetch(f"{CAMERA}/frame.jpg")
    if code == 200 and ctype.startswith("image/"):
        return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})
    why = body.decode("utf-8", "replace")[:200] if code == 503 and body else "the camera feed is not running"
    return JSONResponse({"error": why}, status_code=503, headers={"Cache-Control": "no-store"})
