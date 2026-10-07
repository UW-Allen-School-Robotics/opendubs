#!/usr/bin/env python3
"""Serve the dashboard over HTTP on 8080 and, when the robot has a certificate, HTTPS on 8443 —
from ONE process (tablet-device.md TD-1; choreography plan 4.1c).

    python3 serve.py

Why one process: the backend owns the StamPLC's serial port, so a second backend would fight it
for the port. Why both ports: plain HTTP on 8080 stays exactly as it was, because on-robot tools
(the OTA flash scripts, the mDNS registration, the StamPLC and Tab5 bridges) call
http://127.0.0.1:8080. HTTPS on 8443 is for the tablet, whose microphone and wake lock need a
secure context.

The certificate is this robot's own, made on the robot by deploy/make-robot-ca.sh, and read from
$OPENDUBS_TLS_CERT / $OPENDUBS_TLS_KEY (default /etc/opendubs/tls/dashboard.{crt,key}). With no
certificate, HTTPS is skipped with a log line and HTTP serves as before.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

log = logging.getLogger("opendubs.serve")

DEFAULT_CERT = "/etc/opendubs/tls/dashboard.crt"
DEFAULT_KEY = "/etc/opendubs/tls/dashboard.key"


def configs(env=os.environ) -> list[uvicorn.Config]:
    """The servers to run. Pure, so it can be tested without binding anything."""
    app = env.get("OPENDUBS_ASGI_APP", "app:app")
    host = env.get("OPENDUBS_HOST", "0.0.0.0")
    http = uvicorn.Config(app, host=host, port=int(env.get("OPENDUBS_HTTP_PORT", "8080")),
                          lifespan="on")
    out = [http]
    cert = env.get("OPENDUBS_TLS_CERT", DEFAULT_CERT)
    key = env.get("OPENDUBS_TLS_KEY", DEFAULT_KEY)
    if os.path.isfile(cert) and os.path.isfile(key):
        # lifespan off: the app's startup hooks (state_out, the serial port) run once, on HTTP.
        out.append(uvicorn.Config(app, host=host, port=int(env.get("OPENDUBS_HTTPS_PORT", "8443")),
                                  ssl_certfile=cert, ssl_keyfile=key, lifespan="off"))
    else:
        log.warning("no TLS certificate at %s / %s: HTTPS off, HTTP only "
                    "(deploy/make-robot-ca.sh makes one)", cert, key)
    return out


async def serve_all(cfgs: list[uvicorn.Config]) -> None:
    servers = [uvicorn.Server(c) for c in cfgs]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    # When one stops (a signal, a bind failure), stop the rest instead of half-serving.
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:
        s.should_exit = True
    await asyncio.gather(*pending, return_exceptions=True)
    for t in done:
        t.result()          # surface a failure (e.g. a port in use) as the process's exit


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfgs = configs()
    for c in cfgs:
        log.info("serving %s on %s:%d", "HTTPS" if c.ssl_certfile else "HTTP", c.host, c.port)
    asyncio.run(serve_all(cfgs))


if __name__ == "__main__":
    main()
