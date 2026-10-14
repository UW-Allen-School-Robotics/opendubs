"""The voice service's only line to the face: head-protocol lines over UDP to the face door.

The face door (dashboard backend/face_door.py) listens on 127.0.0.1:4242 and relays each line to the
newest face page. The show runner reaches the face the same way. Nothing here is new transport.

Defence in depth beside safe_tools: whatever calls this, only the verbs in VERBS leave, as one
printable ASCII line. `CONVO` (the conversation state, voice note §6.4) is **not yet agreed** with
the Tablet lane, so it is sent only when OPENDUBS_VOICE_CONVO=1; the page still learns the state
over /ws/voice (`state` messages) either way.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

log = logging.getLogger("voice.face")

VERBS = frozenset({"EMOTE", "MOVE", "CONVO"})
LINE = re.compile(r"^[A-Z]+( [A-Za-z0-9_]+){0,3}$")
REPLY_TIMEOUT_S = 0.4
CONVO_LEASE_MS = 3000          # renewed every second while not idle; a dead service relaxes the face
CONVO_RENEW_S = 1.0


class FaceRefused(ValueError):
    pass


def check(line: str) -> str:
    if not isinstance(line, str) or len(line) > 64 or not LINE.match(line):
        raise FaceRefused(f"not a single safe protocol line: {line!r}")
    if line.split(" ", 1)[0] not in VERBS:
        raise FaceRefused(f"verb not allowed from the voice service: {line!r}")
    return line


class _Reply(asyncio.DatagramProtocol):
    def __init__(self):
        self.fut = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr):
        if not self.fut.done():
            self.fut.set_result(data.decode("ascii", "replace").strip())

    def error_received(self, exc):
        if not self.fut.done():
            self.fut.set_result(f"ERR {exc}")


class FaceDoor:
    def __init__(self, host: str = "127.0.0.1", port: int | None = None, convo: bool | None = None):
        self.host = host
        self.port = port if port is not None else int(os.environ.get("OPENDUBS_FACE_DOOR_PORT", "4242"))
        self.convo_enabled = (os.environ.get("OPENDUBS_VOICE_CONVO") == "1") if convo is None else convo
        self.sent: list[str] = []        # the last lines, for the status view and tests
        self._renew: asyncio.Task | None = None
        self._order: asyncio.Lock | None = None    # convo() calls land in the order they were made

    async def send(self, line: str) -> str:
        """One line to the face; its reply, or "ERR ..." (never raises for a missing face)."""
        check(line)
        self.sent = (self.sent + [line])[-20:]
        loop = asyncio.get_running_loop()
        try:
            transport, proto = await loop.create_datagram_endpoint(
                _Reply, remote_addr=(self.host, self.port))
        except OSError as e:
            return f"ERR no face door: {e}"
        try:
            transport.sendto(line.encode("ascii"))
            return await asyncio.wait_for(proto.fut, REPLY_TIMEOUT_S)
        except asyncio.TimeoutError:
            return "ERR face door did not answer"
        finally:
            transport.close()

    async def convo(self, state: str) -> None:
        """Report the conversation state, and keep its lease alive until idle."""
        if not self.convo_enabled:
            return
        self._order = self._order or asyncio.Lock()
        async with self._order:
            if self._renew:
                self._renew.cancel()
                self._renew = None
            if state == "idle":
                await self.send("CONVO IDLE")
                return
            line = f"CONVO {state.upper()} {CONVO_LEASE_MS}"
            await self.send(line)
            self._renew = asyncio.create_task(self._keep(line))

    async def _keep(self, line: str) -> None:
        try:
            while True:
                await asyncio.sleep(CONVO_RENEW_S)
                await self.send(line)
        except asyncio.CancelledError:
            pass

    def close(self) -> None:
        if self._renew:
            self._renew.cancel()
            self._renew = None
