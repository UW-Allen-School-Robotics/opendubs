"""The face looks this robot has, for the Head & face pickers and the face page (content split).

  GET /api/head/looks            {"themes": [...], "skins": [...], "problems": [...], "content_dir"}:
                                 the head protocol's discovery (software/head-protocol/looks.py: the core
                                 built-ins plus the content folder's face/themes and face/skins) and the
                                 show library's image and video skins (library_serve.py), in one list.
                                 STABLE ORDER, so the pickers and the face agree on it: the built-ins in
                                 the core's own order, then the content folder's by id, then the
                                 library's by id. Every skin has `kind`: "code", "image" or "video".
  GET /face/content/{themes|skins}/<file>   one file of the content folder's face/, read-only, so the face
                                 page can fetch a theme or import a code skin from its own origin.

Registered before the /face static mount (app.py), which would otherwise answer /face/content itself.
"""
from __future__ import annotations

import os
import re
import sys

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()
# software/head-protocol beside software/kiosk-dashboard, in the repository and on the robot alike
# (/opt/opendubs/head-protocol, deployed by deploy-all.sh).
_HP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "head-protocol")
_FILE = re.compile(r"^[a-z0-9_]{1,40}\.(json|js)$")
_TYPES = {".json": "application/json", ".js": "text/javascript"}


def _looks():
    if _HP not in sys.path:
        sys.path.insert(0, _HP)
    import looks
    return looks


def _library_skins() -> list:
    try:
        import library_serve
        lib = library_serve._library()
        if lib is None:
            return []
        _, entries, _ = library_serve._load(lib)
    except Exception:                                 # noqa: BLE001 — no library: no library skins
        return []
    return sorted(({"id": e["id"], "label": e.get("title") or e["id"], "kind": e.get("type"), "source": "library"}
                   for e in entries if e.get("kind") == "skin" and e.get("type") in ("image", "video")),
                  key=lambda s: s["id"])


@router.get("/api/head/looks")
def head_looks() -> JSONResponse:
    try:
        d = _looks().discover()
    except ImportError:
        return JSONResponse({"error": "the head protocol's files are not on this robot (software/head-protocol)"},
                            status_code=503)
    seen = {s["id"] for s in d["skins"]}
    d["skins"] += [s for s in _library_skins() if s["id"] not in seen]
    return JSONResponse(d)


@router.get("/face/content/{kind}/{name}")
def face_content(kind: str, name: str):
    if kind not in ("themes", "skins") or not _FILE.match(name):
        return JSONResponse({"error": "not a face look"}, status_code=404)
    try:
        root = _looks().content_dir()
    except ImportError:
        return JSONResponse({"error": "the head protocol's files are not on this robot"}, status_code=503)
    path = root / "face" / kind / name
    if not path.is_file():
        return JSONResponse({"error": f"no {kind[:-1]} {name} in this robot's content folder"}, status_code=404)
    return FileResponse(str(path), media_type=_TYPES[os.path.splitext(name)[1]],
                        headers={"Cache-Control": "no-cache"})
