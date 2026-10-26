"""The stage box's sound, from the dashboard (software/stage-pi, the Tablet & head interface lane's).

The box plays the show's music through ONE selectable output: auto, hdmi, jack, usb or bt (a Bluetooth
speaker paired to it). Its agent listens on its own loopback only, so the box PULLS: every 2 s it POSTs its
sound report here, and the answer carries the commands the dashboard has queued for it. It runs them one
at a time and reports each one's result by its id.

    POST /api/stage/box/report    the box: {"name", "sound": {...}, "results": {id: {ok, text, at}},
                                  "queued": [id]}  ->  {"commands": [{"id", "op", ...}]}
    GET  /api/stage/box           the dashboard's Stage box card: the newest report, its age, what is queued
    POST /api/stage/box/command   the card: {"op": "output", "value": auto|hdmi|jack|usb|bt}
                                  | {"op": "volume", "output", "value": 0..1} | {"op": "scan"}
                                  | {"op": "pair", "addr"} | {"op": "forget"} | {"op": "test"}

Refused while a show is loaded or playing on the stage pages (stage.hub.show): everything but the volume.
The output and the test tone would be heard over the show, and pairing a speaker can move the sound to it.
The box checks the same again before it acts.
"""
from __future__ import annotations

import itertools
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from refusals import refuse

router = APIRouter()
MAX_REPORT_BYTES = 32 * 1024
ONLINE_S = 8.0                    # the box reports every 2 s: three missed reports and it is offline
COMMAND_TTL_S = 60.0              # a command the box has not taken by then is dropped
SETTINGS = ("auto", "hdmi", "jack", "usb", "bt")
OUTPUTS = SETTINGS[1:]
DURING_SHOW = ("volume",)         # the one command a running show may take

box = {"report": None, "at": None, "from": None}
pending: list[dict] = []          # {"id", "op", ..., "_at"}
_ids = itertools.count(1)


def show_running() -> bool:
    import stage
    show = stage.hub.show or {}
    return show.get("state") in ("loaded", "playing")


def online(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    return box["at"] is not None and now - box["at"] <= ONLINE_S


def validate(c: dict) -> str | None:
    """Why a command is malformed, or None."""
    op = c.get("op")
    if op == "output":
        return None if c.get("value") in SETTINGS else f"value must be one of {', '.join(SETTINGS)}"
    if op == "volume":
        v = c.get("value")
        if c.get("output") not in OUTPUTS:
            return f"output must be one of {', '.join(OUTPUTS)}"
        return None if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 else "value must be 0..1"
    if op == "pair":
        a = str(c.get("addr") or "").split(":")
        return None if len(a) == 6 and all(len(p) == 2 for p in a) else "addr must be a Bluetooth address"
    if op in ("scan", "forget", "test"):
        return None
    return "op must be output, volume, scan, pair, forget or test"


@router.post("/api/stage/box/report")
async def box_report(request: Request) -> JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_REPORT_BYTES:
        return JSONResponse({"error": "too big"}, status_code=413)
    try:
        r = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(r, dict):
        return JSONResponse({"error": "not an object"}, status_code=400)
    now = time.monotonic()
    box.update(report=r, at=now, **{"from": request.client.host if request.client else None})
    taken = set((r.get("results") or {}).keys()) | set(r.get("queued") or [])
    pending[:] = [c for c in pending if c["id"] not in taken and now - c["_at"] <= COMMAND_TTL_S]
    return JSONResponse({"commands": [{k: v for k, v in c.items() if k != "_at"} for c in pending]})


@router.get("/api/stage/box")
def box_status() -> JSONResponse:
    now = time.monotonic()
    r = box["report"] or {}
    return JSONResponse({"online": online(now), "age_s": None if box["at"] is None else round(now - box["at"], 1),
                         "name": r.get("name"), "sound": r.get("sound"), "results": r.get("results") or {},
                         "pending": [c["id"] for c in pending], "show_running": show_running()})


@router.post("/api/stage/box/command")
async def box_command(request: Request) -> JSONResponse:
    try:
        c = await request.json()
    except ValueError:
        c = None
    if not isinstance(c, dict):
        return JSONResponse({"ok": False, "error": "send a JSON object"}, status_code=400)
    why = validate(c)
    if why:
        return JSONResponse({"ok": False, "error": why}, status_code=400)
    if not online():
        return refuse("LINK_STAGE_BOX_OFFLINE", "the stage box is not reporting: is it on, and on the robot's Wi-Fi?")
    if c["op"] not in DURING_SHOW and show_running():
        return refuse("SHOW_STAGE_SOUND_BUSY", "a show is loaded or playing: the stage box's sound stays as it "
                      "is until it ends")
    cmd = {k: c[k] for k in ("op", "value", "output", "addr") if k in c}
    cmd.update(id=f"c{next(_ids)}-{int(time.time())}", _at=time.monotonic())
    pending.append(cmd)
    return JSONResponse({"ok": True, "id": cmd["id"]})
