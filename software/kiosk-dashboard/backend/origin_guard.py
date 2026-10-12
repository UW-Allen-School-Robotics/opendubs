"""Refuse state-changing requests that another web page sent.

NOT A LOGIN. The robot's network is the first protection; this closes one hole inside it: any web
page open in any browser on the robot's LAN could otherwise POST to this dashboard without anyone
seeing — clear an e-stop, move the lift, shut the robot down — because a browser sends a cross-site
POST without asking. A browser always names the page that sent it (`Origin`, or failing that
`Referer`), so a request that names a page which is not this dashboard is refused.

A request that names NO page is let through: curl, scripts and the other services on the Thor and on
a laptop send none, and direct access for debugging is kept on purpose.

What counts as this dashboard: a page on the same host as the request, on the request's own port or
on the dashboard's pair (HTTP 8080, HTTPS 8443). Plus `OPENDUBS_DASH_ALLOWED_ORIGINS` (comma
separated), which by default holds the kiosk app's own asset origin, for the tablet face served
from inside the app.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from refusals import refuse

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
DASH_PORTS = {8080, 8443}
DEFAULT_ALLOWED = "https://appassets.androidplatform.net"


def _allowed_extra() -> set[str]:
    raw = os.environ.get("OPENDUBS_DASH_ALLOWED_ORIGINS", DEFAULT_ALLOWED)
    return {o.strip().rstrip("/").lower() for o in raw.split(",") if o.strip()}


def _origin_of(url: str) -> str | None:
    """scheme://host[:port] of a Referer, or None if it names no page."""
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else None


def is_own(origin: str, host_header: str, request_port: int | None) -> bool:
    if origin.rstrip("/").lower() in _allowed_extra():
        return True
    o = urlsplit(origin)
    if not o.scheme or not o.hostname:          # "null" (a sandboxed or file page), or junk
        return False
    req = urlsplit(f"//{host_header}")
    if not req.hostname or o.hostname.lower() != req.hostname.lower():
        return False
    try:
        o_port = o.port or {"http": 80, "https": 443}.get(o.scheme)
    except ValueError:
        return False
    return o_port == (req.port or request_port) or o_port in DASH_PORTS


async def refuse_websocket(ws) -> bool:
    """The same rule for a WebSocket handshake, which HTTP middleware never sees. A browser sends
    Origin on the handshake but does not enforce same-origin on it, so without this any page on the
    LAN could open the dashboard's sockets — /rosbridge among them, which can publish. Call before
    accept(); True means it was refused (closed with 1008, policy violation) and the handler returns.
    A handshake with no Origin (a script, a tool) passes, as for POSTs."""
    origin = ws.headers.get("origin")
    if origin is not None and not is_own(origin, ws.headers.get("host", ""), ws.url.port):
        await ws.close(code=1008)
        return True
    return False


def install(app) -> None:
    @app.middleware("http")
    async def origin_guard(request, call_next):
        if request.method in UNSAFE:
            sent_by = request.headers.get("origin")
            if sent_by is None and request.headers.get("referer"):
                sent_by = _origin_of(request.headers["referer"])
            if sent_by is not None and not is_own(sent_by, request.headers.get("host", ""), request.url.port):
                return refuse("LINK_FOREIGN_ORIGIN",
                              f"refused: this request came from a page at {sent_by}, not from this robot's "
                              "dashboard", 403)
        return await call_next(request)
