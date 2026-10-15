"""The kiosk app's next build, served to the robot tablet, which installs it itself (the app's
Updater.kt; notes/workplan-tablet.md DT-22).

  GET /api/kiosk/latest   {"versionCode", "versionName", "sha256", "install_ok", "why"}; 404 when this
                          robot serves no build
  GET /api/kiosk/apk      the signed APK

The build is SIGNED ON THE THOR (the Thor lane's sign-kiosk.sh, with the robot's own release key) and
put in OPENDUBS_KIOSK_DIR (default /var/lib/opendubs/kiosk/): opendubs-kiosk.apk and latest.json. This
module only reads them: it never holds a key and never signs.

`install_ok` is false while a show is loaded or armed (the runner has a run), so the tablet never
restarts its app under a show; the app asks again before it installs. With no runner running at all
there is no show, and an install may go ahead.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

DIR = os.environ.get("OPENDUBS_KIOSK_DIR") or "/var/lib/opendubs/kiosk"
APK_NAME = "opendubs-kiosk.apk"
APK_TYPE = "application/vnd.android.package-archive"


def _latest() -> dict | None:
    try:
        with open(os.path.join(DIR, "latest.json"), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and isinstance(d.get("versionCode"), int) and isinstance(d.get("sha256"), str)
            and os.path.isfile(os.path.join(DIR, APK_NAME))):
        return None
    return d


def show_busy() -> str | None:
    """Why an install must wait (a show has a run: loaded, armed or running), or None."""
    import choreo_proxy
    code, body = choreo_proxy._call("GET", "palette")
    if code == 503:
        return None                                  # no runner: no show
    if code != 200 or not isinstance(body, dict):
        return f"the show runner's state is unknown ({code})"
    if body.get("running"):
        return f"a show is loaded ({body.get('running')}, {body.get('status') or 'starting'})"
    return None


@router.get("/api/kiosk/latest")
async def latest() -> JSONResponse:
    d = _latest()
    if d is None:
        return JSONResponse({"error": "this robot serves no kiosk build"}, status_code=404)
    why = await asyncio.get_running_loop().run_in_executor(None, show_busy)
    return JSONResponse({"versionCode": d["versionCode"], "versionName": d.get("versionName"),
                         "sha256": d["sha256"], "install_ok": why is None, "why": why})


@router.get("/api/kiosk/apk")
def apk():
    if _latest() is None:
        return JSONResponse({"error": "this robot serves no kiosk build"}, status_code=404)
    return FileResponse(os.path.join(DIR, APK_NAME), media_type=APK_TYPE, filename=APK_NAME)
