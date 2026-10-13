#!/usr/bin/env python3
"""The OpenDubs voice service: a WebSocket on loopback that the dashboard's /ws/voice relays to.

    python3 software/voice/server.py              # on a desk: VOICE_STT=fake VOICE_TTS=fake VOICE_LLM=fake
    python3 software/voice/server.py --check      # build every backend from the environment and exit

Loopback only (127.0.0.1). The dashboard is the only door from the tablet: it applies the origin
guard and serves the page over HTTPS, so nothing else on the LAN can open a microphone stream here.
A busy port is refused with its number, never shared (CLAUDE.md lanes, rule 6).

Configuration, all from the environment (on a robot: /etc/opendubs/voice.env, root-owned, 0600):

  OPENDUBS_VOICE_PORT     8093
  OPENDUBS_VOICE          1; 0 keeps voice off without stopping the service
  OPENDUBS_DASHBOARD_URL  http://127.0.0.1:8080   the show interlock and robot_status read it
  OPENDUBS_FACE_DOOR_PORT 4242                    the face door, UDP on loopback
  OPENDUBS_VOICE_CONVO    0; 1 sends CONVO lines to the face (only once the Tablet lane ships it)
  OPENDUBS_VOICE_PERSONA  a per-robot persona file; default persona.txt beside this file
  VOICE_STT               fake | parakeet           (parakeet: not packaged yet)
  VOICE_TTS               fake | kokoro             (kokoro: not packaged yet)
  VOICE_BRAIN             local | cloud | auto
  VOICE_LLM               local | fake              the local brain: an OpenAI-compatible server, or canned
  VOICE_LLM_URL           http://127.0.0.1:8000     the local model server (opendubs-llm)
  VOICE_LLM_MODEL         the model id that server serves
  VOICE_CLOUD_MODEL       claude-haiku-5-5
  ANTHROPIC_API_KEY       only for cloud/auto; never in the repo, the image or the browser
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import brains  # noqa: E402
import speech  # noqa: E402
import wire  # noqa: E402
from conversation import Voice  # noqa: E402
from face import FaceDoor  # noqa: E402
from gate import Gate  # noqa: E402

log = logging.getLogger("voice")

HOST = "127.0.0.1"
MAX_FRAME = 64 * 1024


def persona() -> str:
    path = os.environ.get("OPENDUBS_VOICE_PERSONA") or os.path.join(HERE, "persona.txt")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def build(gate: Gate | None = None) -> Voice:
    """Every backend from the environment. Raises with a readable reason if one cannot start."""
    stt = speech.make_stt(os.environ.get("VOICE_STT", "fake"))
    tts = speech.make_tts(os.environ.get("VOICE_TTS", "fake"))
    brain = brains.make_router()
    return Voice(stt, tts, brain, FaceDoor(), gate or Gate(), persona())


class _Link:
    """The websockets connection as conversation.Page expects it."""

    def __init__(self, ws):
        self.ws = ws

    async def send_text(self, s: str) -> None:
        await self.ws.send(s)

    async def send_bytes(self, b: bytes) -> None:
        await self.ws.send(b)


async def handle(voice: Voice, ws) -> None:
    page = await voice.join(_Link(ws))
    log.info("page %d connected", page.id)
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                await voice.on_bytes(page, msg)
            else:
                await voice.on_text(page, msg)
    except Exception as e:                      # a dropped socket is normal; log anything else
        if type(e).__name__ not in ("ConnectionClosedError", "ConnectionClosedOK"):
            log.warning("page %d: %s", page.id, e)
    finally:
        await voice.leave(page)
        log.info("page %d gone", page.id)


async def serve(voice: Voice, port: int, ready: asyncio.Future | None = None):
    from websockets.asyncio.server import serve as ws_serve
    gate_task = None
    try:
        async with ws_serve(lambda ws: handle(voice, ws), HOST, port, max_size=MAX_FRAME,
                            ping_interval=10, ping_timeout=10) as server:
            gate_task = asyncio.create_task(voice.gate.run())     # only once the port is ours
            if ready is not None:
                ready.set_result(server.sockets[0].getsockname()[1])
            await asyncio.get_running_loop().create_future()      # forever
    finally:
        if gate_task:
            gate_task.cancel()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("OPENDUBS_VOICE_PORT", "8093")))
    ap.add_argument("--check", action="store_true", help="build the backends and exit")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        if a.check:
            build()
            print("voice: backends OK", file=sys.stderr)
            return 0

        async def _main():
            v = build()
            log.info("voice on 127.0.0.1:%d, brain %s, protocol %d", a.port, v.brain.mode, wire.PROTOCOL)
            await serve(v, a.port)
        asyncio.run(_main())
    except (speech.BackendUnavailable, brains.BrainOffline, ValueError) as e:
        print(f"voice: cannot start: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"voice: port {a.port} on {HOST} is already in use by another process; "
                  "not sharing it (choose OPENDUBS_VOICE_PORT)", file=sys.stderr)
            return 3
        raise
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
