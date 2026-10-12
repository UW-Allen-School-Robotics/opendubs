"""Show light: a running show's colour on the PRISM strip, as a LAYER over the robot's state colour
(choreography plan, the LIGHT output). Nothing here writes to the strip: state_out.py stays its one
writer, and asks `winning()` which colour to push.

  * POST /api/choreo/light {rgb: [r, g, b], br, lease_ms}  — the show runner, on this machine only:
    hold this colour while the lease is renewed (it lapses by itself if the runner stops renewing).
  * POST /api/choreo/light {release: true}                 — the show is over: the state colour now.

The rules, in order (winning()):
  1. A RESERVED state (DEGRADED, E_STOP_LATCH) always shows its own colour: the show layer is ignored
     — never dimmed, never mixed — for as long as the state lasts.
  2. With no fresh lease, the state colour.
  3. With a fresh lease, the show's colour — except, while the robot is AUTONOMOUS and the AUTO pulse
     is on, a brief flash of the AUTO colour every AUTO_PULSE_EVERY_S, so the strip still says the
     robot is under autonomy (default ON until the team decides; OPENDUBS_SHOW_LIGHT_AUTO_PULSE=0
     turns it off).

RATE. The strip is written over the StamPLC's I2C, which the drive ESCs share. state_out.py calls
winning() from its existing 0.3 s loop and writes only when the answer CHANGES, so a show can never
write faster than that loop, whatever the runner sends. The runner also merges colour changes closer
than ~0.33 s before they get here.

The show validator already refuses colours that read as the reserved states' (red, amber).
"""
from __future__ import annotations

import ipaddress
import os
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

RESERVED = ("DEGRADED", "E_STOP_LATCH")
LEASE_MAX_S = 2.0                     # a runner asking for longer is held to this
AUTO_STATE = "AUTONOMOUS_CONTROL"
AUTO_PULSE_EVERY_S = 3.0
AUTO_PULSE_ON_S = 0.45                # > the 0.3 s loop, so every pulse is seen at least once


def auto_pulse_on() -> bool:
    return os.environ.get("OPENDUBS_SHOW_LIGHT_AUTO_PULSE", "1").strip().lower() not in ("0", "off", "false", "no")


class Layer:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.rgb, self.br, self.until, self.since = None, None, 0.0, 0.0

    def hold(self, rgb, br, lease_s):
        now = self.clock()
        with self.lock:
            if self.rgb is None or self.until <= now:
                self.since = now                  # a new lease: the AUTO pulse counts from here
            self.rgb, self.br = tuple(int(v) for v in rgb), int(br)
            self.until = now + min(max(float(lease_s), 0.0), LEASE_MAX_S)

    def release(self):
        with self.lock:
            self.rgb, self.br, self.until = None, None, 0.0

    def current(self):
        """((r, g, b), br, since) while the lease is fresh, else None."""
        with self.lock:
            if self.rgb is None or self.clock() >= self.until:
                return None
            return self.rgb, self.br, self.since


layer = Layer()


def winning(state: str, state_colour, now=None, pulse=None, lay=None):
    """The colour the strip should show now: `state_colour` ((r, g, b), br) or the show's."""
    lay = lay or layer
    if state in RESERVED:
        return state_colour
    cur = lay.current()
    if cur is None:
        return state_colour
    rgb, br, since = cur
    now = lay.clock() if now is None else now
    if state == AUTO_STATE and (auto_pulse_on() if pulse is None else pulse):
        if (now - since) % AUTO_PULSE_EVERY_S < AUTO_PULSE_ON_S:
            return state_colour
    return rgb, br


def _local(request: Request) -> bool:
    try:
        return ipaddress.ip_address((request.client.host or "").split("%")[0]).is_loopback
    except ValueError:
        return False


@router.post("/api/choreo/light")
async def light(request: Request):
    if not _local(request):
        return JSONResponse({"error": "the show light is set by the show runner on this machine only"},
                            status_code=403)
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(msg, dict):
        return JSONResponse({"error": "send {rgb, br, lease_ms} or {release: true}"}, status_code=400)
    if msg.get("release"):
        layer.release()
        return JSONResponse({"ok": True, "released": True})
    rgb, br, lease_ms = msg.get("rgb"), msg.get("br", 100), msg.get("lease_ms", 500)
    ok = (isinstance(rgb, list) and len(rgb) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in rgb)
          and isinstance(br, int) and 0 <= br <= 100 and isinstance(lease_ms, (int, float)) and lease_ms > 0)
    if not ok:
        return JSONResponse({"error": "rgb: three 0..255 integers; br: 0..100; lease_ms > 0"}, status_code=400)
    layer.hold(rgb, br, lease_ms / 1000.0)
    return JSONResponse({"ok": True, "lease_s": min(lease_ms / 1000.0, LEASE_MAX_S)})
