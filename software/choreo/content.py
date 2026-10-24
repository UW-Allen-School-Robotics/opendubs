"""The CONTENT FOLDER: one folder outside the repository for everything that is a USE of the engine
rather than the engine — shows, clip libraries, face themes and skins, and tool packs
(notes/hygiene-content-split.md). The repository keeps the engine, the generic interfaces and a few
example shows; a robot or a studio points this at its own content.

Where it is, in this order (the same order as the head protocol's looks.py, so the runner, the
dashboard and the face all find the same folder):
    $OPENDUBS_SHOWS_DIR
    the old $OPENDUBS_LOCAL_SHOWS
    /var/lib/opendubs/shows, if it exists (a robot's show folder, which show_sync.py fills)
    ~/opendubs-shows

Layout (every part optional):
    <dir>/*.yaml                            shows
    <dir>/moves/*.clips.yaml                clip libraries (pasted into a show's `clips:` block)
    <dir>/face/themes/, <dir>/face/skins/   face themes and skins (head protocol looks.py)
    <dir>/tools/<pack>/<id>.yaml            a tool pack: tool types for the effector registry,
    <dir>/tools/<pack>/<id>.gaits.json          their recorded gaits, and
    <dir>/tools/<pack>/drivers/<output>.py      the drivers they name in `output:`
"""
from __future__ import annotations

import os

ROBOT_DIR = "/var/lib/opendubs/shows"
DEFAULT_DIR = "~/opendubs-shows"


def shows_dir() -> str:
    """The content folder (it need not exist)."""
    d = os.environ.get("OPENDUBS_SHOWS_DIR") or os.environ.get("OPENDUBS_LOCAL_SHOWS")
    if not d:
        d = ROBOT_DIR if os.path.isdir(ROBOT_DIR) else DEFAULT_DIR
    return os.path.abspath(os.path.expanduser(d))


def tool_packs() -> list[str]:
    """The tool-pack folders under <dir>/tools/, sorted: each one is searched for tool types."""
    d = os.path.join(shows_dir(), "tools")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, p) for p in sorted(os.listdir(d))
            if not p.startswith(".") and os.path.isdir(os.path.join(d, p))]
