"""The STAGE PAGE's feed (notes/design/choreo-stage.md): the live skeletons, relayed to the page on the
projector. Choreography's.

    /ws/skeleton/in   the Thor lane's relay pushes canonical frames here (software/choreo/skeleton_feed.py
                      shapes them). LOCALHOST ONLY: nothing off the robot can feed the stage.
    /ws/skeleton      the stage page: receives the newest frame (never a queue — a slow page skips frames),
                      keeps the Thor's clock (ping/pong, as show audio does) and reports the latency it
                      measured: feed capture -> painted, on the Thor's clock.
    /api/stage        the feed's state: frames/s in, the pages connected, the latest latency, the show.
    /api/stage/show   the SHOW's timing, from the show runner (localhost only): the videos to play and
                      when, the beat grid, start (the Thor's monotonic t0) and stop. Every page gets it,
                      one that joins mid-show too — so the same page works in the projector's app, on a
                      small computer by the projector, or in any browser: it needs nothing else.

PRIVACY — a fixed rule: only skeleton points pass through here. No image is accepted, kept or logged, and
nothing is recorded — not even the points.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
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
        self.show = None                         # {"id", "videos", "beat", "state", "t0_mono"} or None

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

    def control(self, msg: dict):
        """A show message: to every page, ahead of any skeleton frame, never dropped."""
        text = json.dumps(msg)
        for p in self.pages.values():
            p["ctl"].append(text)
            p["event"].set()

    def show_command(self, m: dict) -> dict:
        op = m.get("op")
        if op == "load":
            # audio_leads_s: the stage's audio lead per sound output (each page picks its box's own);
            # skeletons: the show draws the skeleton layer over its video (else the video plays clean)
            self.show = {"id": m.get("id"), "videos": m.get("videos") or [], "beat": m.get("beat"),
                         "buffer_s": m.get("buffer_s"), "audio_leads_s": m.get("audio_leads_s") or {},
                         "skeletons": m.get("skeletons") is True, "state": "loaded", "t0_mono": None}
        elif op == "start" and self.show and self.show.get("id") == m.get("id"):
            self.show.update(state="playing", t0_mono=m.get("t0_mono"))
        elif op == "stop" and self.show and self.show.get("id") == m.get("id"):
            self.show.update(state="stopped", cut=bool(m.get("cut")))
        elif op == "mute" and self.show and self.show.get("id") == m.get("id"):
            self.show.update(muted=True)           # the tablet has the music now: the pages play silent
        else:
            return {"error": f"op must be load, or start/stop/mute for the loaded show — not {op!r}"}
        self.control({"type": "show", **self.show})
        return {"ok": True, "pages": len(self.pages)}

    def view(self) -> dict:
        age = None if self.last_frame_at is None else round(time.monotonic() - self.last_frame_at, 2)
        return {"feed": {"hz": round(self.rate, 1), "age_s": age, "status": self.last_status},
                "pages": len(self.pages), "latency_ms": self.latency_ms,
                "pages_info": [p["info"] for p in self.pages.values() if p.get("info")],
                "preload": [p.get("preload") for p in self.pages.values()],   # one per page; None: not reported
                "stalls": [p["stall"] for p in self.pages.values() if p.get("stall")],
                "show": None if self.show is None else {k: self.show.get(k) for k in ("id", "state", "t0_mono")}}


hub = Hub()

# What a page may say of itself (frontend/stage/stage.js; a stage Pi's own device report folded in):
# kept per page, newest only, plain values only.
PAGE_FIELDS = {"device": str, "name": str, "model": str, "audio_output": str, "audio_name": str, "under_voltage_now": bool,
               "under_voltage_since_boot": bool, "throttled_now": bool, "screen_connected": bool, "screen_mode": str,
               "viewport": list, "dpr": (int, float), "clock_err_ms": (int, float), "sound": str,
               "stretch": (int, float),   # the page's ?stretch: how much the screen stretches its input across
               "media_items": int, "media_bytes": (int, float), "media_free": (int, float), "media_syncing": bool,
               # the box's own estimate of its sound output's delay, and the audio lead the page is using
               "audio_latency_ms": (int, float), "audio_lead_ms": (int, float), "audio_lead_from": str,
               "audio_setting": str}   # the output CHOSEN on the box (auto, hdmi, jack, usb, bt); audio_output is the one playing


def page_info(raw: dict) -> dict:
    out = {k: raw[k] for k, t in PAGE_FIELDS.items()
           if k in raw and isinstance(raw[k], t) and not (t is not bool and isinstance(raw[k], bool))}
    for k in ("device", "name", "model", "audio_output", "audio_name", "screen_mode", "sound", "audio_lead_from", "audio_setting"):
        if k in out:
            out[k] = out[k][:80]
    if "viewport" in out:
        vp = out["viewport"][:2]
        out["viewport"] = [int(x) for x in vp if isinstance(x, (int, float)) and not isinstance(x, bool)]
    out["at"] = round(time.time(), 1)
    return out


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
    me = {"ws": ws, "slot": None, "ctl": [], "event": asyncio.Event()}
    hub.pages[pid] = me
    if hub.show is not None:                      # joining mid-show: what is playing, and since when
        me["ctl"].append(json.dumps({"type": "show", **hub.show}))
        me["event"].set()

    async def sender():
        while True:
            await me["event"].wait()
            me["event"].clear()
            while me["ctl"]:
                await ws.send_text(me["ctl"].pop(0))
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
            elif isinstance(m.get("page"), dict):     # the page on itself: its screen, sound, power (a stage Pi)
                me["info"] = page_info(m["page"])
            elif isinstance(m.get("preload"), dict):  # its head start on the show's video (the runner waits)
                pl = m["preload"]
                num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0  # noqa: E731
                me["preload"] = {"id": str(pl.get("id"))[:80], "ready": pl.get("ready") is True,
                                 "local": pl.get("local") is True, "buffered_s": num(pl.get("buffered_s")),
                                 "need_s": num(pl.get("need_s")),
                                 **({"error": str(pl["error"])[:160]} if pl.get("error") else {})}
            elif isinstance(m.get("stall"), dict):    # it ran out of video mid-show (the tablet takes the music)
                me["stall"] = {"id": str(m["stall"].get("id"))[:80], "at_s": m["stall"].get("at_s")
                               if isinstance(m["stall"].get("at_s"), (int, float)) else None}
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        task.cancel()
        hub.pages.pop(pid, None)


@router.get("/api/stage")
async def stage_state():
    return JSONResponse(hub.view())



@router.post("/api/stage/show")
async def stage_show(request: Request):
    """The show runner's timing for the stage pages (localhost only)."""
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        return JSONResponse({"error": "the show's timing comes from the show runner, on this machine"}, status_code=403)
    try:
        m = await request.json()
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(m, dict):
        return JSONResponse({"error": "the body must be a JSON object"}, status_code=400)
    out = hub.show_command(m)
    return JSONResponse(out, status_code=400 if "error" in out else 200)
