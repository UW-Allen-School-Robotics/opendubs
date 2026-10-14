"""/ws/voice: the face page's microphone stream, relayed to the voice service on this Thor.

The voice service (software/voice, the opendubs-voice container) listens on loopback only, at
OPENDUBS_VOICE_URL (ws://127.0.0.1:8093). The tablet's face page is served over HTTPS from this
dashboard, so it may only open a wss:// socket on this same origin; this route is that socket, as
/rosbridge is for ROS. Every frame is relayed unchanged both ways: audio as binary, control as JSON
text. The contract between the page and the service is software/voice/wire.py; nothing here
interprets it.

What this route adds, and only this:

- **The origin guard.** A page from anywhere else on the LAN may not open the robot's microphone
  stream (origin_guard.refuse_websocket, before accept).
- **A frame cap.** A frame over MAX_FRAME closes the socket (1009): no legitimate audio frame is
  near it.
- **A truthful "voice is down".** If the voice service is not running, the page gets one JSON
  `error` with code VOICE_SERVICE_DOWN and the socket closes with 1013 (try again later), so the
  page can grey out Talk instead of inviting speech nobody will hear.

When either side goes, the other is closed, so a page that leaves always ends its turn in the
service, and the service's own rules (one conversation, voice off during shows) apply.

Owned by the Voice lane (CLAUDE.md "Lanes"); app.py includes the router.
"""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, WebSocket

import origin_guard

router = APIRouter()

VOICE_URL = os.environ.get("OPENDUBS_VOICE_URL", "ws://127.0.0.1:8093")
MAX_FRAME = 64 * 1024


async def _connect(url: str):
    """The upstream connection. A seam: the tests replace it with an in-memory service."""
    import websockets
    return await websockets.connect(url, max_size=MAX_FRAME, open_timeout=3)


@router.websocket("/ws/voice")
async def voice_relay(socket: WebSocket) -> None:
    if await origin_guard.refuse_websocket(socket):     # a foreign page may not open the microphone stream
        return
    await socket.accept()
    try:
        up = await _connect(VOICE_URL)
    except Exception as e:
        await socket.send_text(json.dumps({"type": "error", "code": "VOICE_SERVICE_DOWN",
                                           "message": f"the voice service is not running ({type(e).__name__})"}))
        await socket.close(code=1013)
        return

    async def to_page():
        """The service's frames to the page; when the service ends the stream, close the page."""
        try:
            async for msg in up:
                if isinstance(msg, bytes):
                    await socket.send_bytes(msg)
                else:
                    await socket.send_text(msg)
        except Exception:
            pass
        try:
            await socket.close()
        except Exception:
            pass

    pump = asyncio.create_task(to_page())
    try:
        while True:                                   # the page's frames to the service
            m = await socket.receive()
            if m["type"] == "websocket.disconnect":
                break
            data = m.get("bytes") if m.get("bytes") is not None else m.get("text")
            if data is None:
                continue
            if len(data) > MAX_FRAME:
                await socket.close(code=1009)
                break
            await up.send(data)
    except Exception:        # the page went (WebSocketDisconnect), or the service did: the pump closes the page
        pass
    finally:
        pump.cancel()
        try:
            await up.close()
        except Exception:
            pass
