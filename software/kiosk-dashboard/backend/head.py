"""Where the robot's head is — by ROLE, not by device (workplan-tablet.md item 0.1).

A head has two halves:

  - the FACE: what shows. It takes STATE (and a show's EMOTE, THEME, CUE) and answers PING.
  - the NECK: what moves. It takes pan/tilt (the YOLO tracker's frames, the StamPLC's RC aiming),
    and the StamPLC is told where it is (TAB5IP).

With the Tab5 fitted they are the same box, so both default to the same address and nothing
changes. With the tablet as the face, the neck is a separate controller (plan DT-7).

Per-robot settings, kept on the robot in /etc/opendubs/robot.env, never in the repo:

  OPENDUBS_HEAD_FACE_HOST   default: OPENDUBS_TAB5_HOST if set (the old name), else opendubs-tab5.local
  OPENDUBS_HEAD_NECK_HOST   default: the face's host
"""
from __future__ import annotations

import os
import sys

DEFAULT_HOST = "opendubs-tab5.local"
_OLD_NAME = "OPENDUBS_TAB5_HOST"
_warned = False


def _setting(env, name: str) -> str:
    return (env.get(name) or "").strip()


def face_host(env=os.environ) -> str:
    global _warned
    host = _setting(env, "OPENDUBS_HEAD_FACE_HOST")
    if host:
        return host
    old = _setting(env, _OLD_NAME)
    if old:
        if not _warned:
            sys.stderr.write(f"HEAD: {_OLD_NAME} is the old name for OPENDUBS_HEAD_FACE_HOST; "
                             "it still works\n")
            _warned = True
        return old
    return DEFAULT_HOST


def neck_host(env=os.environ) -> str:
    return _setting(env, "OPENDUBS_HEAD_NECK_HOST") or face_host(env)
