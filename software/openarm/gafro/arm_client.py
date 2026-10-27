"""The client of arm_service.py's command socket: `ctl` and the dashboard both use it. Standard library only (the dashboard has no numpy, no gafro).

One service per arm, one unix socket each, in a directory both the service and its clients can reach: `$OPENDUBS_ARM_SOCKET_DIR`, else `$XDG_RUNTIME_DIR`, else /tmp.
The dashboard runs as another user than the person who starts the service, so on the robot the directory is a shared one (the service's `--socket-mode`, 660, and the
directory's group). The protocol is one JSON line in, a reply of one or more lines until the server closes the connection.
"""
from __future__ import annotations

import json
import os
import socket

SIDES = ("left", "right")


def socket_dir() -> str:
    return os.environ.get("OPENDUBS_ARM_SOCKET_DIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def socket_for(side: str) -> str:
    return os.path.join(socket_dir(), f"opendubs-arm-service-{side}.sock")


def sides_served() -> list:
    """The arms that have a service socket now (a socket a dead service left behind counts: `send` says so)."""
    return [side for side in SIDES if os.path.exists(socket_for(side))]


def send(command: str, drop: bool = False, path: str | None = None, move_seconds: float | None = None, allow_large: bool = False, timeout: float = 30.0) -> str:
    """One command to a service, and its whole reply. Raises OSError if nothing answers on `path`."""
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall((json.dumps({"command": command, "drop": drop, "move_seconds": move_seconds, "allow_large": allow_large}) + "\n").encode())
        sock.shutdown(socket.SHUT_WR)
        return sock.makefile().read().rstrip("\n")       # the whole reply: `status` is two lines
