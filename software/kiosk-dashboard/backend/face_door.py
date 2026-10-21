"""The tablet face's door, on the Thor: head-protocol lines in over UDP, out to the face page.

The tablet face draws in a web page (software/face-web) and understands the head protocol in that
page (tablet plan DT-10). Something must carry the protocol's UDP lines to it. The plan's door lives
in the tablet's kiosk app (5.2); until that is built, this one lives here, in the dashboard, which
already runs on every robot and already serves the tablet:

  UDP 127.0.0.1:4242      the face half. The show runner (OPENDUBS_HEAD_FACE_HOST=127.0.0.1) and
                          this dashboard's own STATE line send here. Loopback only: nothing off
                          this machine can drive the face through it.
  /ws/face                the face page's end, same origin as the page (origin_guard applies).
  /face/                  the face page itself (software/face-web, deployed beside this app).

Each line goes to the NEWEST connected page, which after a reload is the live one, and its reply
goes back to the sender. With no page connected, PING still answers (`face=absent`), so a probe can
tell "no face showing" from "no door". The neck is not here: with the Tab5 fitted it stays the neck
(OPENDUBS_HEAD_NECK_HOST), and GAZE never comes to this port.

Set OPENDUBS_FACE_DOOR=0 to keep the door closed, e.g. on a robot whose face is still the Tab5's.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

import origin_guard

log = logging.getLogger("face_door")
router = APIRouter()

# The face page's own files, as served at /face/ (app.py mounts the same directory).
FACE_DIR = os.environ.get("OPENDUBS_FACE_WEB_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "face-web")
_FACE_PARTS = ("index.html", "engine", "vendor")      # what the page runs; not its tests, tools or skin library


def face_version(root: str = None) -> str:
    """Changes whenever a deploy changes a file the face page runs: names, sizes and mtimes, so it is
    cheap (the vendored models are stat'ed, never read). The page polls it and reloads itself (main.js),
    because turning tracking on only adds a module to the page already running: on 2026-10-16 a deployed
    fix sat unused until the page happened to reload."""
    import hashlib
    root = root or FACE_DIR
    h = hashlib.sha1()
    for part in _FACE_PARTS:
        top = os.path.join(root, part)
        paths = [top] if os.path.isfile(top) else sorted(
            os.path.join(d, f) for d, _, fs in os.walk(top) for f in fs if "__pycache__" not in d)
        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            h.update(f"{os.path.relpath(path, root)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:12]


# Wake-word hits, counted on the tablet (engine/voice.js) and kept here for the operator: when, and whether
# it was taken to the voice service. Never audio. For gate V-7 (notes/workplan-voice.md): an hour of normal
# use with nobody saying the phrase, against the service's `not_addressed` count.
wake_hits: list = []


@router.post("/api/head/wake_hit")
async def post_wake_hit(request: Request) -> dict:
    try:
        m = json.loads((await request.body())[:200] or b"{}")
        at, ok = float(m.get("at")), bool(m.get("accepted"))
    except (ValueError, TypeError, AttributeError):
        return {"ok": False}
    wake_hits.append((at, ok))
    del wake_hits[:-1000]
    log.info("wake word heard on the face%s", "" if ok else " (not taken: busy or just spoken)")
    return {"ok": True}


voice_health: dict = {}


@router.post("/api/head/voice_health")
async def post_voice_health(request: Request) -> dict:
    """The face page's listening health, every 2 s (engine/voice.js): numbers only, never audio."""
    try:
        m = json.loads((await request.body())[:4000] or b"{}")
    except ValueError:
        return {"ok": False}
    if isinstance(m, dict):
        import time
        voice_health.clear(); voice_health.update(m, received_age_s=None, _rx=time.time())
    return {"ok": True}


# ---- one clip of what the spotter hears, on purpose only ----------------------------------------
# OFF unless armed FROM THE THOR ITSELF (curl on localhost), for at most 2 minutes, for ONE clip: the
# page's 16 kHz microphone frames (the keyword spotter's input) for 5 s from the moment it sees the arm,
# so cue the person as you arm it. Written to /tmp on the Thor (mode 600) with a .json beside it (frames
# that arrived and in how long, the microphone), never anywhere else; whoever armed it deletes both once
# replayed. Arming lives in memory, so a dashboard restart disarms it.
#   curl -X POST localhost:8080/api/head/capture/arm -d '{"seconds": 90}'
capture = {"until": 0.0, "last": None, "processing": "on", "device": "default", "gain_max_db": 24}


@router.get("/api/head/capture")
def get_capture() -> dict:
    import time
    return {"armed": time.time() < capture["until"], "last": capture["last"],
            "mic_processing": capture["processing"], "mic_device": capture["device"],
            "spotter_gain_max_db": capture["gain_max_db"]}


def _from_thor(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1")


async def _field(request: Request, name: str):
    try:
        return json.loads((await request.body()) or b"{}").get(name)
    except (ValueError, AttributeError):
        return None


# Which of the tablet's microphones the face opens, for an A/B: "default", or the start of a device id
# (or a label) from the report's mic.inputs, e.g. "6626305d" for "Speakerphone". From the Thor only, in
# memory (a dashboard restart goes back to default). An id the tablet does not list falls back to the
# default, and the report says so (mic.device_error).
#   curl -X POST localhost:8080/api/head/mic_device -d '{"device": "6626305d"}'
@router.post("/api/head/mic_device")
async def set_mic_device(request: Request) -> dict:
    if not _from_thor(request):
        return {"ok": False, "error": "switch it from the Thor itself (localhost)"}
    want = await _field(request, "device")
    if not isinstance(want, str) or not 0 < len(want) <= 64:
        return {"ok": False, "error": 'device must be "default", a device id prefix, or a label'}
    capture["device"] = want
    return {"ok": True, "device": want}


# The most the wake word's gain stage may raise a quiet voice (kws_worker.js), in dB: 24 by default,
# 12 to 40. From the Thor only, in memory.
#   curl -X POST localhost:8080/api/head/spotter_gain -d '{"max_db": 36}'
@router.post("/api/head/spotter_gain")
async def set_spotter_gain(request: Request) -> dict:
    if not _from_thor(request):
        return {"ok": False, "error": "switch it from the Thor itself (localhost)"}
    want = await _field(request, "max_db")
    if isinstance(want, bool) or not isinstance(want, (int, float)) or not 12 <= want <= 40:
        return {"ok": False, "error": "max_db must be a number from 12 to 40"}
    capture["gain_max_db"] = float(want)
    return {"ok": True, "max_db": float(want)}


# The microphone's platform processing (echo cancellation, noise suppression, AGC) for an A/B: "on" (the
# default) or "off", switched FROM THE THOR ITSELF, in memory (a dashboard restart puts it back on). The
# face reopens its microphone within a couple of seconds; its health report says which it has.
#   curl -X POST localhost:8080/api/head/mic_processing -d '{"processing": "off"}'
@router.post("/api/head/mic_processing")
async def set_mic_processing(request: Request) -> dict:
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return {"ok": False, "error": "switch it from the Thor itself (localhost)"}
    try:
        want = json.loads((await request.body()) or b"{}").get("processing")
    except (ValueError, AttributeError):
        want = None
    if want not in ("on", "off"):
        return {"ok": False, "error": 'processing must be "on" or "off"'}
    capture["processing"] = want
    return {"ok": True, "processing": want}


@router.post("/api/head/capture/arm")
async def arm_capture(request: Request) -> dict:
    import time
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return {"ok": False, "error": "arm it from the Thor itself (localhost)"}
    try:
        secs = float(json.loads((await request.body()) or b"{}").get("seconds", 90))
    except (ValueError, AttributeError):
        secs = 90
    capture["until"] = time.time() + max(5.0, min(120.0, secs))
    return {"ok": True, "armed_s": round(capture["until"] - time.time())}


@router.post("/api/head/capture/clip")
async def post_capture_clip(request: Request) -> dict:
    import time
    import wave
    if time.time() >= capture["until"]:
        return {"ok": False, "error": "not armed"}
    capture["until"] = 0.0                                   # one clip per arming
    pcm = (await request.body())[:16000 * 2 * 8]             # 16 kHz s16le mono, 8 s at most
    path = f"/tmp/opendubs-wake-capture-{int(time.time())}.wav"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm[: len(pcm) & ~1])
    capture["last"] = {"path": path, "seconds": round(len(pcm) / 32000, 2), "at": time.time()}
    meta = request.headers.get("x-capture-meta", "")[:1000]
    try:
        meta_obj = json.loads(meta) if meta else {}
    except ValueError:
        meta_obj = {"unparsed": meta}
    fd = os.open(path[:-4] + ".json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(meta_obj, f)
    log.warning("wake capture written: %s (%.1f s)", path, len(pcm) / 32000)
    return {"ok": True}


# A touch anywhere on the robot's tablet, face or dashboard: the head stops at once (neck_follow.touched).
# Holding still is the safe direction, so any page may say it; the pages send it only from the tablet.
@router.post("/api/head/touch")
async def post_touch() -> dict:
    import neck_follow
    neck_follow.touched()
    return {"ok": True}


face_render: dict = {}


@router.post("/api/head/face_health")
async def post_face_health(request: Request) -> dict:
    """The face page's render health, every 2 s (engine/main.js): fps, the last frame's age, the
    canvas, whether the eyes' pixels are lit. Numbers only."""
    try:
        m = json.loads((await request.body())[:4000] or b"{}")
    except ValueError:
        return {"ok": False}
    if isinstance(m, dict):
        import time
        face_render.clear(); face_render.update(m, _rx=time.time())
    return {"ok": True}


def _aged(d: dict) -> dict | None:
    import time
    if not d:
        return None
    out = dict(d)
    out["received_age_s"] = round(time.time() - out.pop("_rx"), 1)
    return out


@router.get("/api/head/face_health")
def get_face_health() -> dict:
    return {"face": _aged(face_render)}


@router.get("/api/head/wake_hits")
def get_wake_hits() -> dict:
    import time
    hour = time.time() * 1000 - 3600 * 1000
    recent = [h for h in wake_hits if h[0] >= hour]
    health = dict(voice_health)
    if health:
        health["received_age_s"] = round(time.time() - health.pop("_rx"), 1)
    return {"listening": health or None, "face": _aged(face_render),
            "total": len(wake_hits), "accepted": sum(1 for h in wake_hits if h[1]),
            "last_hour": len(recent), "last_hour_accepted": sum(1 for h in recent if h[1]),
            "recent": [{"at": h[0], "accepted": h[1]} for h in wake_hits[-20:]]}


# Which dashboard process is answering. A deploy copies the face's files and only later restarts the
# dashboard, and that gap can outlast the page's 5 s settle: on 2026-10-20 the page reloaded into the old
# process, which then stopped mid-download. The page also reloads when this changes and holds (main.js).
BOOT = f"{os.getpid()}-{int(__import__('time').time())}"


@router.get("/api/face/version")
def get_face_version() -> dict:
    return {"version": face_version(), "boot": BOOT}

HOST = "127.0.0.1"
PORT = int(os.environ.get("OPENDUBS_FACE_DOOR_PORT", "4242"))
REPLY_TIMEOUT_S = 0.4

pages: dict = {}                 # connected face pages, oldest first
waiting: dict = {}               # line id -> future for the page's reply
_ids = itertools.count(1)
_transport = None


def newest():
    return next(reversed(pages), None)


async def carry(line: str) -> str:
    """One protocol line to the face page; its reply."""
    ws = newest()
    if ws is None:
        return ("PONG kind=tablet-face engine=none mount=unknown mode=face face=absent"
                if line == "PING" else "ERR face not showing")
    i = next(_ids)
    fut = asyncio.get_running_loop().create_future()
    waiting[i] = fut
    try:
        await ws.send_text(json.dumps({"id": i, "line": line}))
        return await asyncio.wait_for(fut, REPLY_TIMEOUT_S)
    except asyncio.TimeoutError:
        return "ERR face did not answer"
    except Exception:                               # the page went away mid-line
        return "ERR face not showing"
    finally:
        waiting.pop(i, None)


class _Door(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        asyncio.ensure_future(self._answer(data, addr))

    async def _answer(self, data, addr):
        line = data.split(b"\0", 1)[0].decode(errors="replace").strip()
        if line:
            self.t.sendto((await carry(line)).encode(), addr)


async def broadcast(msg: dict) -> None:
    """Something every face page should know now, e.g. changed head settings (head_settings.py)."""
    text = json.dumps(msg)
    for ws in list(pages):
        try:
            await ws.send_text(text)
        except Exception:                          # noqa: BLE001 — a page going away
            pages.pop(ws, None)


@router.websocket("/ws/face")
async def face_ws(socket: WebSocket):
    if await origin_guard.refuse_websocket(socket):
        return
    await socket.accept()
    pages[socket] = True
    try:                                            # its idle settings, before any line arrives
        import head_settings
        await socket.send_text(json.dumps({"settings": head_settings.load()}))
    except Exception as e:                          # noqa: BLE001 — a page without settings still works
        log.warning("face page: settings not sent: %s", e)
    try:                                            # and the robot's state now, so it is never grey
        import state_out                            # while it waits for the next 3 s refresh
        st = state_out.face_state()
        if st:
            await socket.send_text(json.dumps({"id": 0, "line": f"STATE {st}"}))
        batt = state_out.face_batt()                # and the pack's charge, for the gauge
        if batt:
            await socket.send_text(json.dumps({"id": 0, "line": batt}))
        cap = state_out.face_caption()              # and the state in plain words (state_words.py)
        if cap:
            await socket.send_text(json.dumps({"id": 0, "line": cap}))
    except Exception as e:                          # noqa: BLE001
        log.warning("face page: state not sent: %s", e)
    log.info("face page connected (%d showing)", len(pages))
    try:
        while True:
            m = await socket.receive()
            if m.get("type") == "websocket.disconnect":
                break
            if m.get("text") is None:                # a binary frame (the twin's picture): not here
                continue
            try:
                msg = json.loads(m["text"])
            except ValueError:
                continue
            if "face_health" in msg:                 # the face found itself blank and is reloading (main.js)
                log.warning("face page reloading itself: %s", str(msg["face_health"])[:200])
                continue
            if "track_status" in msg:                # what the face's tracker is doing (neck_follow.py)
                if pages and list(pages)[-1] is socket:
                    import neck_follow
                    neck_follow.note_status(msg["track_status"])
                continue
            if "track" in msg:                       # the tracker's target, for the neck (neck_follow.py)
                if pages and list(pages)[-1] is socket:     # the newest page only, as for every line
                    import neck_follow
                    neck_follow.note_target(msg["track"])
                continue
            fut = waiting.get(msg.get("id"))
            if fut is not None and not fut.done():
                fut.set_result(str(msg.get("reply", "ERR")))
    except WebSocketDisconnect:
        pass
    finally:
        pages.pop(socket, None)
        log.info("face page gone (%d showing)", len(pages))


async def start() -> None:
    """Open the UDP door, once. A port already taken (a face on 4242) is logged, not fatal."""
    global _transport
    if _transport is not None or os.environ.get("OPENDUBS_FACE_DOOR", "1") == "0":
        return
    try:
        _transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            _Door, local_addr=(HOST, PORT))
        log.info("face door on udp %s:%d", HOST, PORT)
    except OSError as e:
        log.warning("face door: cannot listen on udp %s:%d: %s", HOST, PORT, e)
