"""Whether voice is available right now. Voice is OFF during shows: a decision of the project team.

Read-only, through the dashboard on the same Thor, which already forwards the show runner:

  GET /api/choreo/palette   {"running": <routine id> | null, ...}   the runner's own status
                            503 when no runner is running: then no show can be running either

Fail closed: if the dashboard cannot be read, voice is off ("can't confirm no show is running").
A robot that answers in the middle of its own routine is a choreography fault, not a feature.

Also off when OPENDUBS_VOICE=0, which a robot's env file can set to keep the microphone closed
without stopping the service.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("voice.gate")

POLL_S = 1.0
TIMEOUT_S = 1.5


def http_get_json(url: str, timeout: float = TIMEOUT_S) -> tuple[int, object]:
    """(status, body). Status 0 means the server could not be reached at all."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def verdict(code: int, body) -> tuple[bool, str | None]:
    """(on, why-not) from the palette call. Pure, so the rule is tested without a dashboard."""
    if code == 200 and isinstance(body, dict):
        if body.get("running"):
            return False, f"a show is running ({body['running']})"
        return True, None
    if code == 503:
        return True, None          # no runner, so no show
    return False, "can't confirm no show is running (the dashboard did not answer)"


class Gate:
    def __init__(self, dashboard: str | None = None, enabled: bool | None = None, fetch=http_get_json):
        dashboard = dashboard or os.environ.get("OPENDUBS_DASHBOARD_URL", "http://127.0.0.1:8080")
        self.url = dashboard.rstrip("/") + "/api/choreo/palette"
        self.enabled = os.environ.get("OPENDUBS_VOICE", "1") != "0" if enabled is None else enabled
        self.fetch = fetch
        self.on, self.why = False, "starting"
        self.listeners: list = []          # async callbacks (on, why), called on every change

    async def check(self) -> tuple[bool, str | None]:
        if not self.enabled:
            on, why = False, "voice is switched off on this robot"
        else:
            code, body = await asyncio.get_running_loop().run_in_executor(None, self.fetch, self.url)
            on, why = verdict(code, body)
        await self._set(on, why)
        return on, why

    async def _set(self, on: bool, why: str | None) -> None:
        if (on, why) == (self.on, self.why):
            return
        log.info("voice %s%s", "on" if on else "off", f": {why}" if why else "")
        self.on, self.why = on, why
        for cb in list(self.listeners):
            await cb(on, why)

    async def run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:                       # never let the poll die: fail closed
                log.exception("gate check failed")
                await self._set(False, "the show check failed")
            await asyncio.sleep(POLL_S)
