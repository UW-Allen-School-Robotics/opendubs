"""The STAGE PAGE's feed (notes/design/choreo-stage.md): the live skeletons, relayed to the page on the
projector. Choreography's.

    /ws/skeleton/in   the Thor lane's relay pushes canonical frames here (software/choreo/skeleton_feed.py
                      shapes them). LOCALHOST ONLY: nothing off the robot can feed the stage.
    /ws/skeleton      the stage page: receives the newest frame (never a queue — a slow page skips frames),
                      keeps the Thor's clock (ping/pong, as show audio does) and reports the latency it
                      measured: feed capture -> painted, on the Thor's clock.
    /api/stage        the feed's state: frames/s in, the pages connected, the latest latency.

PRIVACY — a fixed rule: only skeleton points pass through here. No image is accepted, kept or logged, and
nothing is recorded — not even the points.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

router = APIRouter()
MAX_FRAME_BYTES = 64 * 1024        # a skeleton frame is a few kB; anything bigger is not one


def thor_ms() -> float:
    return time.monotonic() * 1000.0


class Hub:
    def __init__(self):
        self.pages: dict[int, dict] = {}         # id -> {"ws", "slot": newest unsent frame or None, "event"}
        self.n = 0
        self.frames_in, self.rate_at, self.rate = 0, time.monotonic(), 0.0
        self.last_frame_at, self.last_status = None, None
        self.latency_ms = None                   # the newest page report: {"p50", "p95", "n"}

    def count(self):
        now = time.monotonic()
        self.frames_in += 1
        if now - self.rate_at >= 2.0:
            self.rate, self.frames_in, self.rate_at = self.frames_in / (now - self.rate_at), 0, now

    def publish(self, text: str):
        """Newest-only: each page holds one slot; a newer frame replaces an unsent one."""
        self.count()
        self.last_frame_at = time.monotonic()
        for p in self.pages.values():
            p["slot"] = text
            p["event"].set()

    def view(self) -> dict:
        age = None if self.last_frame_at is None else round(time.monotonic() - self.last_frame_at, 2)
        return {"feed": {"hz": round(self.rate, 1), "age_s": age, "status": self.last_status},
                "pages": len(self.pages), "latency_ms": self.latency_ms}


hub = Hub()


def _local(ws: WebSocket) -> bool:
    host = (ws.client.host if ws.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


@router.websocket("/ws/skeleton/in")
async def skeleton_in(ws: WebSocket):
    if not _local(ws):                            # only the relay, on this machine
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        while True:
            text = await ws.receive_text()
            if len(text) > MAX_FRAME_BYTES:
                continue
            try:
                f = json.loads(text)
            except ValueError:
                continue
            if not isinstance(f, dict) or f.get("v") != 1 or not isinstance(f.get("people"), list):
                continue                          # not a canonical frame: dropped, never passed on
            hub.last_status = f.get("status", "live")
            hub.publish(text)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/skeleton")
async def skeleton_page(ws: WebSocket):
    import origin_guard
    if await origin_guard.refuse_websocket(ws):
        return
    await ws.accept()
    hub.n += 1
    pid = hub.n
    me = {"ws": ws, "slot": None, "event": asyncio.Event()}
    hub.pages[pid] = me

    async def sender():
        while True:
            await me["event"].wait()
            me["event"].clear()
            text, me["slot"] = me["slot"], None
            if text is not None:
                await ws.send_text(text)

    task = asyncio.create_task(sender())
    try:
        while True:
            m = await ws.receive_json()
            if "ping" in m:                       # the Thor's clock, as show audio keeps it
                t1 = thor_ms()
                await ws.send_json({"pong": m["ping"], "t1": t1, "t2": thor_ms()})
            elif isinstance(m.get("latency_ms"), dict):
                hub.latency_ms = {k: m["latency_ms"].get(k) for k in ("p50", "p95", "n")}
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        task.cancel()
        hub.pages.pop(pid, None)


@router.get("/api/stage")
async def stage_state():
    return JSONResponse(hub.view())
