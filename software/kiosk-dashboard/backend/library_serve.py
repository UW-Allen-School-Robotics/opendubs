"""The show library, served to the tablet's cache (tablet plan 5.3; face-engine.md §D).

The library lives on the Thor and is written only by the sync (software/choreo/library.py, the
Choreography lane's). This module only READS it, and reads it through that same module, so there is
one validator and one hash rule:

  GET /api/library/index
      The index as JSON (the page has no YAML parser): a list of entries. Entries that fail
      `library.entry_problems` are left out and logged once per index version, so a bad entry is
      visible on the Thor rather than as a skin that silently never arrives. The ETag is the
      sha256 of index.yaml, which the sync replaces atomically, so a poll that finds nothing new
      is a 304 with no body.

  GET /api/library/item/<sha256>
      An item by its content hash (`library.item_hash`). A file is served as is; a folder (svg/riv,
      later) as its listing, one `relative/path<TAB>sha256` line per file, the same lines its hash is
      made from. Immutable: a given hash is always the same bytes, so the tablet caches it forever.

  GET /api/library/item/<sha256>/<relative/path>
      One file inside a folder item.

An item is served only if it is in the served index AND its bytes still hash to what the index says.
A file changed by hand behind the sync's back would otherwise be served under the old hash, and the
tablet would cache the wrong thing forever. The check runs once per change of the file's size and
mtime, not per request.

The library's own working folders (`.tool/`, `.incoming/`) are never in the index and never served:
nothing here walks the library folder, and a path through a dot-folder is refused.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import sys

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

log = logging.getLogger("library_serve")
router = APIRouter()

# software/choreo beside software/kiosk-dashboard in the repo; /opt/opendubs/choreo beside
# /opt/opendubs/kiosk-dashboard on the Thor (deploy.sh). The same relative path in both.
CHOREO_DIR = os.environ.get("OPENDUBS_CHOREO_DIR") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "choreo"))

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE = "public, max-age=31536000, immutable"
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("image/svg+xml", ".svg")


def _library():
    """software/choreo/library.py, or None if this robot has no show runner deployed."""
    if CHOREO_DIR not in sys.path:
        sys.path.insert(0, CHOREO_DIR)
    try:
        import library
        return library
    except Exception as e:                       # noqa: BLE001 — absent or broken: say so, don't crash
        log.warning("library module unavailable from %s: %s", CHOREO_DIR, e)
        return None


def _root(lib) -> str:
    return os.path.realpath(lib.root())


def _inside(root: str, rel: str) -> str | None:
    """`rel` resolved under `root`, or None if it leaves it or passes through a dot-folder."""
    if not rel or rel.startswith("/") or any(part.startswith(".") for part in rel.split("/")):
        return None
    full = os.path.realpath(os.path.join(root, rel))
    return full if full.startswith(root + os.sep) else None


# ---- the index ---------------------------------------------------------------------------------

_cache = {"key": None, "etag": None, "entries": [], "by_hash": {}}


def _load(lib):
    """(etag, served entries, {sha256: entry}), re-read only when index.yaml's bytes change."""
    path = os.path.join(_root(lib), lib.INDEX)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raw = b""
    etag = '"' + hashlib.sha256(raw).hexdigest() + '"'
    if (path, etag) == _cache["key"]:
        return _cache["etag"], _cache["entries"], _cache["by_hash"]
    entries = []
    try:
        listed = (lib._yaml().safe_load(raw) or []) if raw else []
    except Exception as e:                       # noqa: BLE001
        log.warning("library index unreadable: %s", e)
        listed = []
    for e in listed if isinstance(listed, list) else []:
        if not isinstance(e, dict):
            continue
        problems = lib.entry_problems(e)
        if not problems and _inside(_root(lib), str(e.get("file", ""))) is None:
            problems = [f"{e.get('id')!r}: file {e.get('file')!r} is outside the library"]
        if not problems and not SHA_RE.match(str(e.get("sha256", ""))):
            problems = [f"{e.get('id')!r}: sha256 is not a lowercase hex digest"]
        if problems:
            for p in problems:
                log.warning("library entry not served: %s", p)
            continue
        entries.append(e)
    _cache.update(key=(path, etag), etag=etag, entries=entries, by_hash={e["sha256"]: e for e in entries})
    return etag, entries, _cache["by_hash"]


@router.get("/api/library/index")
def library_index(request: Request) -> Response:
    lib = _library()
    if lib is None:
        return JSONResponse({"error": "no show library on this robot (software/choreo not deployed)"},
                            status_code=503)
    etag, entries, _ = _load(lib)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(json.dumps(entries, default=str), media_type="application/json", headers=headers)


# ---- the items ---------------------------------------------------------------------------------

_verified: dict = {}          # path -> (signature, item_hash) — re-hashed only when the files change


def _signature(path: str):
    if os.path.isfile(path):
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    sig = []
    for d, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            st = os.stat(os.path.join(d, f))
            sig.append((os.path.relpath(os.path.join(d, f), path), st.st_size, st.st_mtime_ns))
    return tuple(sig)


def _hash_now(lib, path: str) -> str | None:
    try:
        sig = _signature(path)
    except OSError:
        return None
    hit = _verified.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    h = lib.item_hash(path)
    _verified[path] = (sig, h)
    return h


def _item(sha: str):
    """(library, the entry, its path) for a served, still-intact item — or an error response."""
    lib = _library()
    if lib is None:
        return None, JSONResponse({"error": "no show library on this robot"}, status_code=503)
    if not SHA_RE.match(sha):
        return None, JSONResponse({"error": "not a sha256"}, status_code=400)
    _, _, by_hash = _load(lib)
    e = by_hash.get(sha)
    if e is None:
        return None, JSONResponse({"error": "no such item in the library index"}, status_code=404)
    path = _inside(_root(lib), str(e["file"]))
    if path is None or not os.path.exists(path):
        return None, JSONResponse({"error": f"{e['id']}: its file is missing"}, status_code=404)
    if _hash_now(lib, path) != sha:
        log.warning("library item %s changed on disk without the sync: not served", e["id"])
        return None, JSONResponse({"error": f"{e['id']}: its file no longer matches its hash"},
                                  status_code=409)
    return (lib, e, path), None


@router.get("/api/library/item/{sha}")
def library_item(sha: str) -> Response:
    found, err = _item(sha)
    if err:
        return err
    lib, e, path = found
    if os.path.isfile(path):
        media = mimetypes.guess_type(path)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media, headers={"Cache-Control": IMMUTABLE})
    lines = []
    for d, dirs, files in os.walk(path):
        for f in files:
            full = os.path.join(d, f)
            lines.append(f"{os.path.relpath(full, path).replace(os.sep, '/')}\t{lib.item_hash(full)}")
    return Response("\n".join(sorted(lines)), media_type="text/plain",
                    headers={"Cache-Control": IMMUTABLE})


@router.get("/api/library/item/{sha}/{rel:path}")
def library_item_file(sha: str, rel: str) -> Response:
    found, err = _item(sha)
    if err:
        return err
    _, e, path = found
    if os.path.isfile(path):
        return JSONResponse({"error": f"{e['id']} is one file, not a folder"}, status_code=404)
    full = _inside(path, rel)
    if full is None or not os.path.isfile(full):
        return JSONResponse({"error": "no such file in this item"}, status_code=404)
    media = mimetypes.guess_type(full)[0] or "application/octet-stream"
    return FileResponse(full, media_type=media, headers={"Cache-Control": IMMUTABLE})
