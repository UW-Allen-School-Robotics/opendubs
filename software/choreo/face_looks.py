"""The face's looks as the show tools see them: the code skins (and, with the content split, the themes)
the face can show, from the head protocol's looks.py — the one implementation, which reads the core's
built-ins and the content folder's face/ (content.py). Shared by cue.py, library.py and show_sync.py.

Where the head protocol is not installed (an older robot), the built-ins fall back to the names below,
so a show that validated before still validates; content looks are then unknown, and a show naming one
is refused rather than run without it.
"""
from __future__ import annotations

import os
import sys

HEAD_PROTOCOL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "head-protocol")
# Used only without the head protocol: its CODE_SKINS as of the content split (head_protocol.py).
FALLBACK_CODE_SKINS = ("default", "ring", "fireworks")


def _import(name):
    if HEAD_PROTOCOL_DIR not in sys.path:
        sys.path.insert(0, HEAD_PROTOCOL_DIR)
    return __import__(name)


def builtin_code_skins() -> tuple:
    """The skins the face's ENGINE draws in code (head_protocol.CODE_SKINS), in its order."""
    try:
        return tuple(_import("head_protocol").CODE_SKINS)
    except Exception:  # noqa: BLE001  no head protocol here
        return FALLBACK_CODE_SKINS


def face_themes() -> tuple:
    """Every theme the face can show on this machine: the core's and the content folder's face/themes/
    (looks.theme_ids()), plus "none", the old name for "plain". Without the head protocol, "plain" only."""
    try:
        found = {t.lower() for t in _import("looks").theme_ids()}
    except Exception:  # noqa: BLE001  no head protocol here: the core's own theme
        found = set()
    return tuple(sorted(found | {"plain", "none"}))


def code_skins() -> set:
    """Every code skin the face can show on this machine: the engine's plus the content folder's
    face/skins/ (looks.code_skin_ids())."""
    try:
        return set(_import("looks").code_skin_ids())
    except Exception:  # noqa: BLE001  no head protocol here: the built-ins only
        return set(builtin_code_skins())
