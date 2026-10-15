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
    s = load()
    import head                                 # read-only: the robot's own config, not a setting
    s["neck"] = head.neck_host() or "none"
    s["airplay"] = airplay_state()
    s["realsense"] = realsense_present()
    return JSONResponse(s)


# ---- tracking: the setting is the one truth, and the RealSense tracker's service follows it -------
# `tracking: d415` runs the robot's own tracker (opendubs-yolo-tracker); anything else stops it. The
# Operate page's AI TRACKING button writes this same setting (app.py), so the two cannot disagree,
# and the dashboard puts the service in line with the setting as it starts.
USB_SYSFS = "/sys/bus/usb/devices"
# Intel RealSense D4xx product ids (vendor 8086): D415, D435, D435i, D455, D405, D410, D400.
REALSENSE_PRODUCTS = {"0ad3", "0b07", "0b3a", "0b5c", "0b5b", "0ad2", "0ad1"}
TRACKER_UNIT = "opendubs-yolo-tracker"


def realsense_present() -> bool:
    """Whether a RealSense depth camera is plugged in (read from sysfs; no driver needed)."""
    import glob
    for d in glob.glob(os.path.join(USB_SYSFS, "*")):
        try:
            with open(os.path.join(d, "idVendor")) as v, open(os.path.join(d, "idProduct")) as p:
                if v.read().strip() == "8086" and p.read().strip() in REALSENSE_PRODUCTS:
                    return True
        except OSError:
            continue
    return False


def set_tracker(on: bool):
    """Start or stop the RealSense tracker's service. Returns an error, or None. Blocking."""
    import subprocess
    verb = "start" if on else "stop"
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", verb, TRACKER_UNIT], capture_output=True, text=True, timeout=15)
    except Exception as e:                       # noqa: BLE001
        return f"systemctl {verb} failed: {e}"
    if r.returncode != 0:
        return (r.stderr or r.stdout or f"systemctl {verb} returned {r.returncode}").strip()
    return None


def tracker_active() -> bool:
    import subprocess
    try:
        return subprocess.run(["systemctl", "is-active", TRACKER_UNIT], capture_output=True,
                              text=True, timeout=3).stdout.strip() == "active"
    except Exception:                            # noqa: BLE001
        return False


def reconcile_tracker():
    """Put the tracker's service in line with the setting (at the dashboard's start). Blocking."""
    want = load().get("tracking") == "d415"
    if want != tracker_active():
        return set_tracker(want)
    return None


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
    if change.get("tracking") == "d415" and not realsense_present():
        return JSONResponse({"error": "no RealSense camera is connected to the robot"}, status_code=409)
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
    if "tracking" in change:                    # the RealSense tracker follows the setting
        import asyncio
        err = await asyncio.to_thread(set_tracker, s["tracking"] == "d415")
        if err:
            s["tracker_error"] = err
    s["airplay"] = airplay_state()
    s["realsense"] = realsense_present()
    return JSONResponse(s)
