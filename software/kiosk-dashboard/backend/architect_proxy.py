"""The Halloween demo runner's state, for the face, read-only.

The ARCHITECT demo runner (software/architect/opendubs_architect/demo_runner.py) serves its state on
127.0.0.1:8098, which the tablet cannot reach. The face's Halloween ghost (a content skin,
software/face-web/content/face/skins/ghost.js) shows which part of the demo is happening -- EEK! as a
"trick or treat" is taken, FOCUS while the arm gets the candy, HAPPY after -- so it reads the runner's
state here, from the dashboard the face page already came from:

  GET /api/architect/state   {"available": true, "state", "since_s", "runs", "last", "refused_s"}
                             or {"available": false} when the runner is not running or not answering

  GET /api/architect/day     the demo day's coordinator (demo_day.py, 127.0.0.1:8096): {"available": true,
                             "state", "since_s", "why", "show", "person", "nav"} or {"available": false}

`last` is the event of the runner's last state change (run_started, run_ended, run_failed, ...), and
`refused_s` how long ago it last refused or ignored a "trick or treat" (the listener's "Hold on, I'm
thinking!"), null if not recently. Both are worked out here, on the runner's own clock, so the face never
compares its clock with the robot's.

The coordinator decides what the whole robot is doing on the day (IDLE_NAV, APPROACH, ENGAGED, CANDY,
CHOREO, REST, PAUSED), and its candy show is a palette show, which the runner never sees (dev.sh's
default, CANDY_RUNNER=0): so the face reads both, the coordinator first.

READ-ONLY, and only these two calls: nothing reaches the runner's POST routes (heard, trigger, stop) or
the coordinator's (request, pause, resume) this way. A face can only look, never start or stop a run. This is the first piece of the ARCHITECT page's
proxy the runner's README plans under /api/architect/; that page's own calls can join it here.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from fastapi import APIRouter

RUNNER_URL = os.environ.get("OPENDUBS_ARCHITECT_URL", "http://127.0.0.1:8098")
DAY_URL = os.environ.get("OPENDUBS_DAY_URL", "http://127.0.0.1:8096")
TIMEOUT_S = 1.0                 # the face asks twice a second; a slow runner is an absent one
REFUSED = ("trigger_refused", "trigger_ignored")

router = APIRouter()


def _get_state(base: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base}/api/state", timeout=TIMEOUT_S) as r:
            return json.loads(r.read() or b"null")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _runner_state() -> dict | None:
    return _get_state(RUNNER_URL)


def _day_state() -> dict | None:
    return _get_state(DAY_URL)


def summarise(st: dict | None, now: float) -> dict:
    """The runner's /api/state -> what the face needs. Pure: `now` is the runner's clock (time.time())."""
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


@router.get("/api/architect/state")
def architect_state() -> dict:
    return summarise(_runner_state(), time.time())


def summarise_day(st: dict | None) -> dict:
    """The coordinator's /api/state -> what the face needs: its state, how long, why, and the show."""
    if not isinstance(st, dict) or not isinstance(st.get("state"), str):
        return {"available": False}
    return {"available": True, **{k: st.get(k) for k in ("state", "since_s", "why", "show", "person", "nav")}}


@router.get("/api/architect/day")
def architect_day() -> dict:
    return summarise_day(_day_state())
