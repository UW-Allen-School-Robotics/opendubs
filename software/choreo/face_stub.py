#!/usr/bin/env python3
"""A stand-in for the face, until the twin can listen (Tab5 plan TW1).

    python3 face_stub.py                 # listens on 127.0.0.1:4242, prints every line

It answers the head's line protocol the way the face twin will, so the bridge can be built and
tested now: STATE, PING and the new verbs (EMOTE, THEME, CUE) reply OK and print; anything else
replies `ERR unknown`, as the head does. VOLUME and BRIGHT (head protocol v5) are simulated: the stub
keeps the level, clamps a brightness to the head's floor (BRIGHT_MIN) and answers with the level it
applied, `OK volume 0.6` / `OK bright 0.15` — IDLE hands back its own (DEFAULTS). SKIN IDLE and
THEME IDLE answer as a face with no idle settings does: `OK skin idle default`, `OK theme idle plain`.
Localhost only. It is not the face; it only shows what the
face would be told.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time

KNOWN = ("PING", "STATE", "EMOTE", "THEME", "CUE", "CFG", "READY", "ARMED", "SKIN")   # ARMED: READY's v10 alias
IDLE_REPLY = {"SKIN": "OK skin idle default", "THEME": "OK theme idle plain"}   # no idle settings: defaults
BRIGHT_MIN = 0.15                              # the head's floor: it never dims below this
DEFAULTS = {"VOLUME": 0.7, "BRIGHT": 0.8}      # its own levels, which IDLE hands back


def device(line: str, levels: dict) -> str:
    """VOLUME / BRIGHT <0..1> [fade_s] | IDLE -> the head's reply, carrying the level it applied."""
    verb, *args = line.split()
    verb = verb.upper()
    if not args:
        return "ERR args"
    if args[0].upper() == "IDLE":
        levels[verb] = DEFAULTS[verb]
    else:
        try:
            v = float(args[0])
            fade = float(args[1]) if len(args) > 1 else 0.0
        except ValueError:
            return "ERR args"
        if not (0 <= v <= 1 and 0 <= fade <= 30):
            return "ERR range"
        levels[verb] = max(BRIGHT_MIN, v) if verb == "BRIGHT" else v
    return f"OK {'volume' if verb == 'VOLUME' else 'bright'} {levels[verb]:g}"


def answer(line: str, levels: dict) -> str:
    words = line.split()
    verb = words[0].upper()
    if verb in IDLE_REPLY and len(words) == 2 and words[1].upper() == "IDLE":
        return IDLE_REPLY[verb]
    return ("PONG stub" if verb == "PING" else device(line, levels) if verb in ("VOLUME", "BRIGHT")
            else "OK" if verb in KNOWN else "ERR unknown")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=4242)
    a = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", a.port))
    print(f"face stub: listening on 127.0.0.1:{a.port} (a stand-in, not the face)", flush=True)
    t0, levels = time.monotonic(), dict(DEFAULTS)
    while True:
        data, peer = s.recvfrom(512)
        for line in data.decode(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            reply = answer(line, levels)
            print(f"{time.monotonic() - t0:8.2f}s  {line:28s} -> {reply}", flush=True)
            s.sendto((reply + "\n").encode(), peer)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
