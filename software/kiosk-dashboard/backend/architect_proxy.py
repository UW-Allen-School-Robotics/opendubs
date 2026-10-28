"""The ARCHITECT tab's view of the robot's ARCHITECT services (software/architect on daphne/halloween).

Three loopback services, each optional, each forwarded READ-ONLY under /api/architect/, so the page
talks to one origin (standing rule 12) and nothing here can start, approve or stop anything:

  * /api/architect/runner         the demo runner's state (ARCHITECT's program: code, summary, sha,
                                   approved or not, runs, events): demo_runner, 127.0.0.1:8098/api/state
  * /api/architect/listener?after=N  what the Jabra listener heard and did, and the program a show was
                                   recorded from: listener, 127.0.0.1:8099/api/feed
  * /api/architect/frame.jpg      the Orbbec's colour image, 640 wide: camera_feed, 127.0.0.1:8097/frame.jpg
                                   (it subscribes only while asked, so the tab costs nothing when closed)
  * /api/architect/state          which part of the demo is happening, for the face's Halloween ghost (a
                                   content skin, face/skins/ghost.js): {"available": true, "state", "since_s",
                                   "runs", "last", "refused_s"}, or {"available": false}. Always 200.

A service that is not running answers 503 with {"error": ...}, and the page says so.

THE GHOST'S STATE comes from whichever drives the demo. With the demo runner up (ARCHITECT's own program,
`--agent program`), it is the runner's state, summarised by `summarise` (from suzy/halloween, unchanged).
In show mode (`listener --show`, what the Halloween demo runs) the demo runner is not running: the listener
starts a show-palette routine, and the show runner plays it. Then `summarise_show` builds the same shape
from the listener's feed and the show runner's palette: RUNNING from the listener's `show_started` while
the routine is armed or running; `last` run_ended when it ends COMPLETED, run_failed when it ends REFUSED,
ABORTED or ESTOPPED; `refused_s` from the listener's `show_refused` and `held` (its "Hold on" when no run
started). Every age is on this machine's clock: the listener, the runners and the dashboard share it.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
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


# ---- /api/architect/state: the face's Halloween ghost ------------------------------------------------
REFUSED = ("trigger_refused", "trigger_ignored")       # the demo runner's
SHOW_REFUSED = ("show_refused", "held")                # the listener's, in show mode
SHOW_FAILED = ("REFUSED", "ABORTED", "ESTOPPED")       # the show runner's ends that are not a hand-off


def summarise(st: dict | None, now: float) -> dict:
    """The demo runner's /api/state -> what the face needs. Pure: `now` is the runner's clock (time.time())."""
    if not isinstance(st, dict) or not isinstance(st.get("state"), str):
        return {"available": False}
    events = [e for e in st.get("events") or [] if isinstance(e, dict)]
    changes = [e for e in events if "to" in e]
    refused = [e for e in events if e.get("event") in REFUSED and isinstance(e.get("t"), (int, float))]
    return {
        "available": True,
        "state": st["state"],
        "since_s": st.get("since_s"),
        "runs": st.get("runs"),
        "last": changes[-1].get("event") if changes else None,
        "refused_s": round(now - refused[-1]["t"], 1) if refused else None,
    }


_seen: dict = {}       # summarise_show's memory between polls: the palette's last end, and when it was seen


def summarise_show(feed: dict | None, palette: dict | None, now: float, seen: dict = _seen) -> dict:
    """The listener's /api/feed and the show runner's /api/palette, in show mode -> the same shape as
    `summarise`. A routine's end carries no time, so the end is dated when a new one is first seen (the
    face asks twice a second); one already there when this starts is old news, never a fresh HAPPY."""
    status = (feed or {}).get("status") if isinstance(feed, dict) else None
    if not isinstance(status, dict) or not status.get("show"):
        return {"available": False}
    routine = status["show"]
    events = [e for e in feed.get("events") or [] if isinstance(e, dict) and isinstance(e.get("t"), (int, float))]
    started = [e for e in events if e.get("event") == "show_started" and e.get("routine") == routine]
    refused = [e for e in events if e.get("event") in SHOW_REFUSED]
    pal = palette if isinstance(palette, dict) else {}
    end = pal.get("last") if isinstance(pal.get("last"), dict) and pal["last"].get("id") == routine else None
    key = (end.get("status"), end.get("code"), end.get("cause")) if end else None
    if "end" not in seen:
        seen.update(end=key, at=float("-inf"))
    elif key != seen["end"]:
        seen.update(end=key, at=now)
    out = {"available": True, "runs": None,
           "refused_s": round(now - refused[-1]["t"], 1) if refused else None}
    if pal.get("running") == routine:
        t0 = started[-1]["t"] if started else now
        return {**out, "state": "RUNNING", "since_s": round(now - t0, 1), "last": "run_started"}
    last = None
    if end and seen["at"] > float("-inf"):
        last = "run_ended" if end.get("status") == "COMPLETED" else "run_failed" if end.get("status") in SHOW_FAILED else None
    since = round(now - seen["at"], 1) if last else None
    return {**out, "state": status.get("state") or "ARMED", "since_s": since, "last": last}


def _json_or_none(code: int, body: bytes):
    if code != 200:
        return None
    try:
        return json.loads(body or b"null")
    except ValueError:
        return None


@router.get("/api/architect/state")
async def state():
    import choreo_proxy                                  # the show runner, where the Shows page reaches it
    st = _json_or_none(*(await _fetch(f"{RUNNER}/api/state"))[:2])
    if st is not None:
        return summarise(st, time.time())
    feed, pal = await asyncio.gather(_fetch(f"{LISTENER}/api/feed?after=0"),
                                     _fetch(f"{choreo_proxy.RUNNER_URL}/api/palette"))
    return summarise_show(_json_or_none(*feed[:2]), _json_or_none(*pal[:2]), time.time())
