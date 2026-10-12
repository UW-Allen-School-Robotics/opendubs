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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import origin_guard

log = logging.getLogger("face_door")
router = APIRouter()

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


@router.websocket("/ws/face")
async def face_ws(socket: WebSocket):
    if await origin_guard.refuse_websocket(socket):
        return
    await socket.accept()
    pages[socket] = True
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
