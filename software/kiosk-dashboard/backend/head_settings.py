"""The head's idle settings: what the face does when no show is running (the Head & face page).

  GET /api/head/settings    the settings, every key present (defaults filled in)
  PUT /api/head/settings    change some of them; returns the whole set

Kept on the robot (OPENDUBS_HEAD_SETTINGS, default /var/lib/opendubs/head-settings.json), written
atomically, so they survive restarts and reboots. Never in the repo: they are this robot's choices.
A change is pushed at once to every connected face page (face_door.py); a page that connects later
gets them on connect. A show overrides the levels and the skin with its own cues and ends with
`VOLUME IDLE` / `BRIGHT IDLE` (head protocol v5), which return to the values here.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("head_settings")
router = APIRouter()

PATH = os.environ.get("OPENDUBS_HEAD_SETTINGS") or "/var/lib/opendubs/head-settings.json"

DEFAULTS = {
    "screen_on": True,          # keep the tablet's screen on
    "idle_eyes": True,          # expressions change by themselves when no show is running
    "tracking": "off",          # off | tablet (the face's own camera) | d415 (the Thor's tracker)
    "skin": "default",          # a code skin or a library skin's id
    "theme": "plain",
    "mouth": False,             # Face & HRI's mouth; off by default (face-design.md)
    "volume_idle": None,        # 0..1, or None: leave the tablet's own volume alone
    "bright_idle": 1.0,         # 0..1
    "bright_min": 0.15,         # no show can dim the face below this
    "vol_max": 1.0,             # no show can make it louder than this
    "face_return_min": 2,       # the kiosk goes back to the face after this long untouched; 0 = never
}
TRACKING = ("off", "tablet", "d415")


def _check(key, v):
    """None if `v` is acceptable for `key`, else why not."""
    if key not in DEFAULTS:
        return f"unknown setting {key!r}"
    if key in ("screen_on", "idle_eyes", "mouth"):
        return None if isinstance(v, bool) else f"{key} must be true or false"
    if key == "tracking":
        return None if v in TRACKING else f"tracking must be one of {', '.join(TRACKING)}"
    if key in ("skin", "theme"):
        return None if isinstance(v, str) and 0 < len(v) <= 64 else f"{key} must be a name"
    if key == "volume_idle" and v is None:
        return None
    if key == "face_return_min":
        ok = not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 60
        return None if ok else "face_return_min must be minutes from 0 (never) to 60"
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        return f"{key} must be a number from 0 to 1"
    if key == "bright_min" and not 0.05 <= v <= 1:
        return "bright_min must be from 0.05 to 1: a face that goes black looks switched off"
    return None


def load() -> dict:
    s = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        for k, v in (saved.items() if isinstance(saved, dict) else ()):
            if _check(k, v) is None:
                s[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:                       # noqa: BLE001 — a bad file: defaults, and say so
        log.warning("head settings unreadable (%s): using defaults", e)
    return s


def save(s: dict) -> None:
    d = os.path.dirname(PATH) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".head-settings.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1, sort_keys=True)
    os.replace(tmp, PATH)


@router.get("/api/head/settings")
def get_settings() -> JSONResponse:
    return JSONResponse(load())


@router.put("/api/head/settings")
async def put_settings(request: Request) -> JSONResponse:
    try:
        change = await request.json()
    except ValueError:
        return JSONResponse({"error": "send a JSON object of settings"}, status_code=400)
    if not isinstance(change, dict) or not change:
        return JSONResponse({"error": "send a JSON object of settings"}, status_code=400)
    problems = [p for k, v in change.items() if (p := _check(k, v))]
    if problems:
        return JSONResponse({"error": "; ".join(problems)}, status_code=400)
    s = load()
    s.update(change)
    if s["bright_idle"] < s["bright_min"]:
        s["bright_idle"] = s["bright_min"]
    if s["volume_idle"] is not None and s["volume_idle"] > s["vol_max"]:
        s["volume_idle"] = s["vol_max"]
    save(s)
    import face_door                            # push to every face page now
    await face_door.broadcast({"settings": s})
    return JSONResponse(s)
