"""Show audio: the one device that plays a show's music and sound files (choreography plan 4.2).

The show runner keeps the show clock on the Thor; the music plays on a dashboard client — the tablet
by default. This module is the meeting point, inside the dashboard so a page only ever talks to one
origin (standing rule 12):

  * /ws/show-audio — a page that may play show audio connects here. Over it the page measures the
    Thor's clock (NTP-style pings: the page sends its time, the Thor answers with its own monotonic
    receive and send times), claims the role, and receives the runner's commands.
  * POST /api/choreo/audio — the runner's commands: load these files, start at this Thor time, stop
    with a fade, or cut. Relayed to the ONE device holding the claim, never broadcast.
  * GET /api/choreo/audio — who holds it, and whether what was last loaded is ready.
  * GET /api/choreo/media/<path> — the show's audio files, from the shows directory's audio/ and
    sounds/ only; and `library/songs/<file>`, a song from the show library (plan 4.6).

Decided (plan 4.2): the device downloads the whole file and starts it on cue, rather than streaming;
the last device to press "Play show audio here" holds the role, and the kiosk tablet claims it on
start-up when nobody holds it; if no device is ready, the routine runs silently with a warning.

Nothing here moves the robot. A page that loses this connection stops its own audio (DEC-7: a
tablet drop stops the music and nothing else).
"""
from __future__ import annotations

import asyncio
import itertools
import os
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

SHOWS_DIR = os.environ.get("OPENDUBS_CHOREO_SHOWS", "/opt/opendubs/choreo/shows")
MEDIA_SUBDIRS = ("audio", "sounds")
# The show library (software/choreo/library.py): only its songs are served, under media/library/.
LIBRARY_DIR = os.environ.get("OPENDUBS_LIBRARY") or "/var/lib/opendubs/library"
LIBRARY_MEDIA_SUBDIRS = ("songs",)
OPS = ("load", "start", "stop", "cut")

router = APIRouter()


def thor_ms() -> float:
    """The Thor's monotonic clock in ms — the same CLOCK_MONOTONIC the runner keeps the show on."""
    return time.monotonic() * 1000.0


class Hub:
    """Connected pages, the one holding the show-audio role, and the last load's readiness. Pure
    bookkeeping plus sends, so it is tested without a network."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.clients: dict[int, tuple] = {}     # id -> (websocket, label)
        self.holder: int | None = None
        self.load_id = None                      # the runner's last load
        self.ready_id = None                     # the holder's last "ready"
        self.last_load = None                    # resent to a device that claims mid-show
        self.lock = asyncio.Lock()

    def view(self) -> dict:
        lbl = self.clients[self.holder][1] if self.holder in self.clients else None
        return {"holder": lbl, "connected": len(self.clients),
                "load": self.load_id, "ready": self.load_id is not None and self.ready_id == self.load_id}

    async def _send(self, cid, msg):
        ws = self.clients.get(cid, (None,))[0]
        if ws is None:
            return False
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # noqa: BLE001  a dead socket is cleaned up by its own handler
            return False

    async def _announce(self):
        v = self.view()
        for cid in list(self.clients):
            await self._send(cid, {"holder": v["holder"], "you_hold": cid == self.holder})

    async def join(self, ws, label) -> int:
        cid = next(self.ids)
        self.clients[cid] = (ws, label)
        await self._send(cid, {"holder": self.view()["holder"], "you_hold": False})
        return cid

    async def leave(self, cid):
        self.clients.pop(cid, None)
        if self.holder == cid:
            self.holder, self.ready_id = None, None
            await self._announce()

    async def claim(self, cid, label, only_if_free=False) -> bool:
        if only_if_free and self.holder in self.clients:
            return False
        ws = self.clients[cid][0]
        self.clients[cid] = (ws, label or self.clients[cid][1])
        if self.holder != cid:
            old, self.holder, self.ready_id = self.holder, cid, None
            if old in self.clients:
                await self._send(old, {"op": "cut"})     # the old holder stops what it plays
        await self._announce()
        if self.last_load is not None:
            await self._send(cid, self.last_load)        # a new holder gets the current show's files
        return True

    def ready(self, cid, load_id):
        if cid == self.holder:
            self.ready_id = load_id

    async def command(self, msg) -> dict:
        if msg.get("op") == "load":
            self.load_id, self.ready_id, self.last_load = msg.get("id"), None, msg
        delivered = await self._send(self.holder, msg) if self.holder in self.clients else False
        return {**self.view(), "delivered": delivered}


hub = Hub()


@router.websocket("/ws/show-audio")
async def show_audio_ws(ws: WebSocket):
    # A page on another origin may not claim the show-audio role (Hygiene S4); scripts and the
    # runner send no Origin and pass.
    import origin_guard
    if await origin_guard.refuse_websocket(ws):
        return
    await ws.accept()
    cid = await hub.join(ws, "a browser")
    try:
        while True:
            m = await ws.receive_json()
            if "ping" in m:      # clock: answered at once, with receive and send times
                t1 = thor_ms()
                await ws.send_json({"pong": m["ping"], "t1": t1, "t2": thor_ms()})
            elif "claim" in m:
                await hub.claim(cid, str(m.get("label") or "a browser")[:40], bool(m.get("only_if_free")))
            elif "ready" in m:
                hub.ready(cid, m["ready"])
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await hub.leave(cid)


@router.get("/api/choreo/audio")
async def audio_state():
    return JSONResponse(hub.view())


@router.post("/api/choreo/audio")
async def audio_command(request: Request):
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(msg, dict) or msg.get("op") not in OPS:
        return JSONResponse({"error": f"op must be one of {', '.join(OPS)}"}, status_code=400)
    return JSONResponse(await hub.command(msg))


def media_path(rel: str) -> str | None:
    """A show's audio file, or None: only under the shows directory's audio/ or sounds/, or the
    library's songs/ (as `library/songs/...`)."""
    if rel.startswith("library/"):
        root = os.path.realpath(LIBRARY_DIR)
        full = os.path.realpath(os.path.join(root, rel[len("library/"):]))
        if not any(full.startswith(os.path.join(root, d) + os.sep) for d in LIBRARY_MEDIA_SUBDIRS):
            return None
        return full if os.path.isfile(full) else None
    root = os.path.realpath(SHOWS_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    if not any(full.startswith(os.path.join(root, d) + os.sep) for d in MEDIA_SUBDIRS):
        return None
    return full if os.path.isfile(full) else None


@router.get("/api/choreo/media/{rel:path}")
async def media(rel: str):
    p = media_path(rel)
    if p is None:
        return JSONResponse({"error": "no such show audio file"}, status_code=404)
    return FileResponse(p)
