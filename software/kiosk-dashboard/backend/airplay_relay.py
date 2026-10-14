"""AirPlay, live, from the Thor to the face tablet's speaker.

The receiver is shairport-sync (AirPlay 2) on the Thor, in the Thor lane's container. It writes the
audio it receives as raw PCM into a FIFO, and what is playing into a metadata pipe. This module,
inside the dashboard, is the relay from those pipes to the face page:

  AUDIO  OPENDUBS_AIRPLAY_AUDIO (default /run/opendubs/airplay/audio.pcm): s16le, 44100 Hz, stereo.
         Read ALL the time, listener or not, so the receiver never blocks on a full pipe.
  META   OPENDUBS_AIRPLAY_META (default /run/opendubs/airplay/meta): shairport's metadata items —
         play begin/end, title, artist.
  /ws/airplay   the face page's end (engine/airplay.js): one JSON line naming the format, then the
         PCM as binary frames, same origin as the page (origin_guard applies).

Whether the stream is HEARD is not decided here. The show-audio hub (show_audio.py, the Choreography
lane's) is the one arbiter of the speaker — a show mutes AirPlay, AirPlay pauses ambient — and it is
told of each session through `hub.airplay_report(active, title, artist)`. The face page plays only on
the device holding the show-audio role, and only when the hub's gate says so.

A session is ACTIVE while audio flows (the PCM is the truth; the metadata's begin/end can be missing)
and ends after IDLE_S without any. With no receiver installed, the pipes are simply absent: logged
once, looked for again every RETRY_S, and nothing else happens.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import origin_guard

log = logging.getLogger("airplay_relay")
router = APIRouter()

AUDIO = os.environ.get("OPENDUBS_AIRPLAY_AUDIO") or "/run/opendubs/airplay/audio.pcm"
META = os.environ.get("OPENDUBS_AIRPLAY_META") or "/run/opendubs/airplay/meta"
FORMAT = {"format": "s16le", "rate": 44100, "channels": 2}
CHUNK = 4096                    # ~23 ms of 44.1 kHz stereo s16
IDLE_S = 1.5                    # no audio this long: the session is over
RETRY_S = 5.0
CLIENT_QUEUE = 64               # ~1.5 s per listener; a slow one loses its oldest frames, never blocks

clients: set = set()
state = {"active": False, "title": None, "artist": None, "last_audio": 0.0, "bytes": 0}
_loop = None


# ---- the hub (the one arbiter of the speaker) ------------------------------------------------------

def _now() -> str:
    return json.dumps({"now": {"active": state["active"], "title": state["title"], "artist": state["artist"]}})


async def _report():
    """Tell the face pages what is playing (for the caption), and the show-audio hub."""
    _send_all(_now())
    # The hub, guarded: before it has airplay_report, AirPlay is never heard, because the face page's
    # gate starts muted and only the hub opens it.
    try:
        import show_audio
        fn = getattr(show_audio.hub, "airplay_report", None)
        if fn is not None:
            await fn(state["active"], state["title"], state["artist"])
    except Exception as e:                      # noqa: BLE001 — a hub problem must not stop the relay
        log.warning("airplay: hub report failed: %s", e)


def _from_thread(coro_fn):
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(coro_fn(), _loop)


# ---- audio: the FIFO, drained always -----------------------------------------------------------------

def _send_all(data: bytes):
    for q in list(clients):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(data)


def fifo_chunks(path: str, size: int, stop=lambda: False):
    """Yield what arrives on the FIFO at `path`, for ever, surviving the receiver's stops and starts.

    The receiver's unit makes the FIFO in a runtime directory that is REMOVED when it stops, and a new
    FIFO (a new inode) when it starts. So: never a blocking open (it would wait for ever on a FIFO
    that was just removed); open non-blocking, wait with select, and reopen whenever the path is gone,
    the writer has left (EOF), or the path now names a different file. Yields b"" while idle, so the
    caller's loop keeps turning."""
    import select
    fd = None
    while not stop():
        if fd is None:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except FileNotFoundError:
                yield None                              # receiver off
                continue
            except OSError as e:
                log.warning("airplay: %s: %s", path, e)
                yield None
                continue
        r, _, _ = select.select([fd], [], [], 1.0)
        try:
            same = os.stat(path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if not same:                                    # removed, or replaced by a new FIFO
            os.close(fd); fd = None
            continue
        if not r:
            yield b""
            continue
        try:
            data = os.read(fd, size)
        except BlockingIOError:
            yield b""
            continue
        if not data:                                    # no writer (between sessions): wait, re-check
            time.sleep(0.2)
            yield b""
            continue
        yield data
    if fd is not None:
        os.close(fd)


def _audio_reader():
    said_absent = False
    for data in fifo_chunks(AUDIO, CHUNK):
        if data is None:
            if not said_absent:
                log.info("airplay: no receiver (%s absent); looking again every %.0f s", AUDIO, RETRY_S)
                said_absent = True
            time.sleep(RETRY_S)
            continue
        said_absent = False
        if not data:
            continue
        state["last_audio"] = time.monotonic()
        state["bytes"] += len(data)
        if not state["active"]:
            state["active"] = True
            log.info("airplay: playing")
            _from_thread(_report)
        if _loop is not None and clients:
            _loop.call_soon_threadsafe(_send_all, data)


def _idle_watch():
    while True:
        time.sleep(0.25)
        if state["active"] and time.monotonic() - state["last_audio"] > IDLE_S:
            state["active"] = False
            log.info("airplay: stopped")
            _from_thread(_report)


# ---- metadata: what is playing --------------------------------------------------------------------

ITEM = re.compile(rb"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>"
                  rb"(?:\s*<data encoding=\"base64\">\s*([A-Za-z0-9+/=\s]*?)</data>)?\s*</item>", re.S)


def parse_items(buf: bytes):
    """Shairport's metadata stream → [(type, code, value bytes)], and what is left unparsed."""
    out, end = [], 0
    for m in ITEM.finditer(buf):
        t = bytes.fromhex(m.group(1).decode()).decode("latin-1")
        c = bytes.fromhex(m.group(2).decode()).decode("latin-1")
        v = base64.b64decode(re.sub(rb"\s", b"", m.group(4))) if m.group(4) else b""
        out.append((t, c, v))
        end = m.end()
    return out, buf[end:]


def apply_item(t: str, c: str, v: bytes) -> bool:
    """Update the state from one item; True if what is playing changed."""
    if t == "core" and c in ("minm", "asar"):
        key = "title" if c == "minm" else "artist"
        val = v.decode("utf-8", "replace").strip() or None
        if state[key] != val:
            state[key] = val
            return True
    if t == "ssnc" and c == "pend":
        state["title"] = state["artist"] = None
        return True
    return False


def _meta_reader():
    buf = b""
    for data in fifo_chunks(META, 4096):
        if data is None:
            buf = b""
            time.sleep(RETRY_S)
            continue
        if not data:
            continue
        buf += data
        items, buf = parse_items(buf)
        buf = buf[-65536:]                              # never grows without bound
        if any([apply_item(*it) for it in items]) and state["active"]:
            _from_thread(_report)


# ---- the face page's end -----------------------------------------------------------------------------

@router.websocket("/ws/airplay")
async def airplay_ws(socket: WebSocket):
    if await origin_guard.refuse_websocket(socket):
        return
    await socket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
    clients.add(q)
    try:
        await socket.send_text(json.dumps(FORMAT))
        await socket.send_text(_now())                    # what is playing, from the start
        while True:
            item = await q.get()
            if isinstance(item, str):
                await socket.send_text(item)              # what is playing changed
            else:
                await socket.send_bytes(item)             # audio
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        clients.discard(q)


def status() -> dict:
    return {"active": state["active"], "title": state["title"], "artist": state["artist"],
            "listeners": len(clients), "receiver": os.path.exists(AUDIO)}


_started = False


async def start() -> None:
    global _loop, _started
    if _started or os.environ.get("OPENDUBS_AIRPLAY", "1") == "0":
        return
    _started = True
    _loop = asyncio.get_running_loop()
    for fn, name in ((_audio_reader, "airplay_audio"), (_meta_reader, "airplay_meta"), (_idle_watch, "airplay_idle")):
        threading.Thread(target=fn, name=name, daemon=True).start()
