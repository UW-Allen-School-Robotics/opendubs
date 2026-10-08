#!/usr/bin/env python3
"""A stand-in for the face, until the twin can listen (Tab5 plan TW1).

    python3 face_stub.py                 # listens on 127.0.0.1:4242, prints every line

It answers the head's line protocol the way the face twin will, so the bridge can be built and
tested now: STATE, PING and the new verbs (EMOTE, THEME, CUE) reply OK and print; anything else
replies `ERR unknown`, as the head does. Localhost only. It is not the face; it only shows what the
face would be told.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

KNOWN = ("PING", "STATE", "EMOTE", "THEME", "CUE", "CFG")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=4242)
    a = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", a.port))
    print(f"face stub: listening on 127.0.0.1:{a.port} (a stand-in, not the face)", flush=True)
    t0 = time.monotonic()
    while True:
        data, peer = s.recvfrom(512)
        for line in data.decode(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            verb = line.split()[0].upper()
            reply = "PONG stub" if verb == "PING" else ("OK" if verb in KNOWN else "ERR unknown")
            print(f"{time.monotonic() - t0:8.2f}s  {line:28s} -> {reply}", flush=True)
            s.sendto((reply + "\n").encode(), peer)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
