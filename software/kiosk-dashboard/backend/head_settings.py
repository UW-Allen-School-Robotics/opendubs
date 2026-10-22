"""The head's idle settings: what the face does when no show is running (the Head & face page).

  GET /api/head/settings    the settings, every key present (defaults filled in)
  PUT /api/head/settings    change some of them; returns the whole set

Kept on the robot (OPENDUBS_HEAD_SETTINGS, default /var/lib/opendubs/head/settings.json, in a folder
provision.sh gives the dashboard's user), written atomically, so they survive restarts and reboots. Never in the repo: they are this robot's choices.
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

PATH = os.environ.get("OPENDUBS_HEAD_SETTINGS") or "/var/lib/opendubs/head/settings.json"

DEFAULTS = {
    "screen_on": True,          # keep the tablet's screen on
    "idle_eyes": True,          # expressions change by themselves when no show is running
    "tracking": "off",          # off | tablet (the face's own camera)
    "skin": "default",          # a code skin or a library skin's id
    "theme": "plain",
    "mouth": False,             # Face & HRI's mouth; off by default (face-design.md)
    "volume_idle": None,        # 0..1, or None: leave the tablet's own volume alone
    "bright_idle": 1.0,         # 0..1
    "bright_min": 0.15,         # no show can dim the face below this
    "vol_max": 1.0,             # no show can make it louder than this
    "face_return_min": 2,       # the kiosk goes back to the face after this long untouched; 0 = never
    "show_reticle": False,      # draw the tracker's reticle on the face (a diagnostic; off by default)
    "state_words": "hide_auto_manual",   # the state in plain words: always | hide_auto_manual | off (state_words.py)
}
# The RealSense option (d415) was removed 2026-10-22: its tracker sent the Tab5's retired angle frame,
# which the neck Dial refuses, so it steered nothing. A saved "d415" no longer passes _check, so
# load() gives "off" in its place.
TRACKING = ("off", "tablet")


def _check(key, v):
    """None if `v` is acceptable for `key`, else why not."""
    if key not in DEFAULTS:
        return f"unknown setting {key!r}"
    if key in ("screen_on", "idle_eyes", "mouth", "show_reticle"):
        return None if isinstance(v, bool) else f"{key} must be true or false"
    if key == "tracking":
        return None if v in TRACKING else f"tracking must be one of {', '.join(TRACKING)}"
    if key == "state_words":
        import state_words
        return None if v in state_words.MODES else f"state_words must be one of {', '.join(state_words.MODES)}"
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
    s = load()
    import head                                 # read-only: the robot's own config, not a setting
    s["neck"] = head.neck_host() or "none"
    s["airplay"] = airplay_state()
    return JSONResponse(s)


# AirPlay on/off is the receiver unit's own state (the Thor lane's opendubs-airplay.service), not a
# value kept here: "on" is ENABLED and running, so it survives a reboot. Turning it off stops the
# receiver, so the robot disappears from iPhones. A sudoers rule from the Thor lane's provisioning
# allows exactly `systemctl enable --now|disable --now|is-active|is-enabled opendubs-airplay`.
AIRPLAY_UNIT = "opendubs-airplay"


def airplay_state() -> str:
    """"on", "off", or "absent" (no receiver installed on this robot)."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-enabled", AIRPLAY_UNIT], capture_output=True, text=True, timeout=5)
    except Exception:                            # noqa: BLE001 — no systemd (a laptop): no receiver
        return "absent"
    out = (r.stdout or "").strip()
    if out in ("enabled", "enabled-runtime"):
        return "on"
    if out in ("disabled", "masked", "static", "indirect"):
        return "off"
    return "absent"


async def _set_airplay(on: bool):
    """Turn the receiver on (enable --now) or off (disable --now). Returns an error, or None."""
    import asyncio
    import subprocess
    cmd = ["sudo", "-n", "systemctl", "enable" if on else "disable", "--now", AIRPLAY_UNIT]
    try:
        r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:                       # noqa: BLE001
        return str(e)
    if r.returncode != 0:
        return (r.stderr or r.stdout or f"systemctl returned {r.returncode}").strip()
    return None


@router.put("/api/head/settings")
async def put_settings(request: Request) -> JSONResponse:
    try:
        change = await request.json()
    except ValueError:
        return JSONResponse({"error": "send a JSON object of settings"}, status_code=400)
    if not isinstance(change, dict) or not change:
        return JSONResponse({"error": "send a JSON object of settings"}, status_code=400)
    if "airplay" in change:                      # the receiver's state, not a stored setting
        want = change.pop("airplay")
        if not isinstance(want, bool):
            return JSONResponse({"error": "airplay must be true or false"}, status_code=400)
        if airplay_state() == "absent":
            return JSONResponse({"error": "no AirPlay receiver is installed on this robot"}, status_code=409)
        err = await _set_airplay(want)
        if err:
            return JSONResponse({"error": f"AirPlay could not be turned {'on' if want else 'off'}: {err}"},
                                status_code=503)
        if not change:
            s = load(); s["airplay"] = airplay_state()
            return JSONResponse(s)
    problems = [p for k, v in change.items() if (p := _check(k, v))]
    if problems:
        return JSONResponse({"error": "; ".join(problems)}, status_code=400)
    s = load()
    s.update(change)
    if s["bright_idle"] < s["bright_min"]:
        s["bright_idle"] = s["bright_min"]
    if s["volume_idle"] is not None and s["volume_idle"] > s["vol_max"]:
        s["volume_idle"] = s["vol_max"]
    try:
        save(s)
    except OSError as e:                         # e.g. the folder was never provisioned: say so plainly
        return JSONResponse({"error": f"the robot can't save the head settings in {os.path.dirname(PATH)} "
                                      f"({e.strerror}); provision.sh creates that folder"}, status_code=503)
    import face_door                            # push to every face page now
    await face_door.broadcast({"settings": s})
    import head
    s["neck"] = head.neck_host() or "none"      # as GET has it, so a save does not blank the neck note
    s["airplay"] = airplay_state()
    return JSONResponse(s)
