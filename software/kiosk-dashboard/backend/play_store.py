"""Play to robot: audio someone sends from a phone, kept on the robot until it is played.

The "Play to robot" page (frontend/play/) lets anyone on the robot's network send a sound to the
face tablet's speaker: upload a file from the phone, or give a link that the robot fetches. This
module only STORES and SERVES those files; playing them is the show-audio path's job
(/api/choreo/ambient, the Choreography lane's), which stands down for a show as ambient music does.

  POST   /api/play/upload?name=<file name>   the raw file as the request body (no form encoding)
  POST   /api/play/url      {"url": ..., "title"?: ...}   the robot fetches it (http/https only)
  GET    /api/play/files    what is here, newest first
  GET    /api/play/files/<id>.<ext>          the file itself, same origin as the tablet's page
  DELETE /api/play/files/<id>

Files live in a robot-local scratch folder (OPENDUBS_PLAY_DIR, default /var/lib/opendubs/play), never
in the repo, capped per file (OPENDUBS_PLAY_MAX_MB, default 50) and in total: the oldest go first.
A file is accepted only if its first bytes are an audio format the tablet's browser plays; the name
it arrived with is only a title.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

ROOT = os.environ.get("OPENDUBS_PLAY_DIR") or "/var/lib/opendubs/play"
MAX_BYTES = int(float(os.environ.get("OPENDUBS_PLAY_MAX_MB", "50")) * 1024 * 1024)
KEEP_FILES = 30
KEEP_BYTES = 500 * 1024 * 1024
FETCH_TIMEOUT_S = 20
NAME_RE = re.compile(r"^[0-9a-f]{16}\.(mp3|m4a|wav|ogg|flac|webm)$")
MEDIA = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg",
         "flac": "audio/flac", "webm": "audio/webm"}


def sniff(head: bytes):
    """The audio format these first bytes are, as a file extension, or None."""
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"                                   # Ogg Vorbis and Opus
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "m4a"                                   # AAC / ALAC in an MP4 container
    if head[:4] == b"\x1aE\xdf\xa3":
        return "webm"
    return None


class Unwritable(Exception):
    pass


def _root() -> str:
    try:
        os.makedirs(ROOT, exist_ok=True)
        if not os.access(ROOT, os.W_OK):
            raise PermissionError(13, "Permission denied")
    except OSError as e:
        raise Unwritable(f"the robot can't keep sounds in {ROOT} ({e.strerror}); provision.sh creates that folder") from e
    return ROOT


def _guarded(fn):
    """A store that can't be written answers 503 with the reason, never a bare 500."""
    import functools
    import inspect
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except Unwritable as e:
                return JSONResponse({"error": str(e)}, status_code=503)
    else:
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except Unwritable as e:
                return JSONResponse({"error": str(e)}, status_code=503)
    return wrapper


def _title(name: str) -> str:
    base = os.path.basename(name or "").rsplit(".", 1)[0]
    return re.sub(r"[\x00-\x1f]", "", base)[:120] or "untitled"


def _store(tmp: str, title: str, source: str):
    """Move a finished download into place under its content id. Returns the entry, or an error."""
    with open(tmp, "rb") as fh:
        ext = sniff(fh.read(16))
    if ext is None:
        os.unlink(tmp)
        return None, (415, "that isn't an audio file the tablet can play (mp3, m4a, wav, ogg, flac, webm)")
    h = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    fid = h.hexdigest()[:16]
    name = f"{fid}.{ext}"
    os.replace(tmp, os.path.join(_root(), name))
    entry = {"id": fid, "file": name, "title": title, "bytes": os.path.getsize(os.path.join(ROOT, name)),
             "source": source, "added": time.time(), "url": f"/api/play/files/{name}"}
    with open(os.path.join(ROOT, f"{fid}.json"), "w", encoding="utf-8") as fh:
        json.dump(entry, fh)
    _prune()
    return entry, None


def entries() -> list:
    out = []
    for f in os.listdir(_root()):
        if f.endswith(".json"):
            try:
                with open(os.path.join(ROOT, f), encoding="utf-8") as fh:
                    e = json.load(fh)
                if os.path.exists(os.path.join(ROOT, e["file"])):
                    out.append(e)
            except Exception:                          # noqa: BLE001 — a damaged sidecar: skip it
                continue
    return sorted(out, key=lambda e: e["added"], reverse=True)


def _remove(fid: str) -> bool:
    gone = False
    for f in os.listdir(_root()):
        if f.split(".", 1)[0] == fid:
            os.unlink(os.path.join(ROOT, f)); gone = True
    return gone


def _prune() -> None:
    """Keep the newest KEEP_FILES files and at most KEEP_BYTES in all; the oldest go first."""
    es, total = entries(), 0
    for i, e in enumerate(es):
        total += e["bytes"]
        if i >= KEEP_FILES or total > KEEP_BYTES:
            _remove(e["id"])


def _tmp() -> str:
    fd, path = tempfile.mkstemp(dir=_root(), prefix=".incoming-")
    os.close(fd)
    return path


@router.post("/api/play/upload")
@_guarded
async def upload(request: Request, name: str = "") -> JSONResponse:
    size = int(request.headers.get("content-length") or 0)
    if size > MAX_BYTES:
        return JSONResponse({"error": f"too big: the limit is {MAX_BYTES // (1024 * 1024)} MB"}, status_code=413)
    tmp, n = _tmp(), 0
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                n += len(chunk)
                if n > MAX_BYTES:
                    raise OverflowError
                fh.write(chunk)
    except OverflowError:
        os.unlink(tmp)
        return JSONResponse({"error": f"too big: the limit is {MAX_BYTES // (1024 * 1024)} MB"}, status_code=413)
    if n == 0:
        os.unlink(tmp)
        return JSONResponse({"error": "no file was sent"}, status_code=400)
    entry, err = _store(tmp, _title(name), "upload")
    return JSONResponse(entry) if entry else JSONResponse({"error": err[1]}, status_code=err[0])


def _fetch(url: str, tmp: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "OpenDubs-robot/1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r, open(tmp, "wb") as fh:
        n = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            n += len(chunk)
            if n > MAX_BYTES:
                raise OverflowError
            fh.write(chunk)


@router.post("/api/play/url")
@_guarded
async def from_url(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = None
    url = (body or {}).get("url") if isinstance(body, dict) else None
    if not isinstance(url, str) or not re.match(r"^https?://", url, re.I):
        return JSONResponse({"error": "give an http:// or https:// link to an audio file"}, status_code=400)
    tmp = _tmp()
    try:
        await asyncio.to_thread(_fetch, url, tmp)
    except OverflowError:
        os.unlink(tmp)
        return JSONResponse({"error": f"too big: the limit is {MAX_BYTES // (1024 * 1024)} MB"}, status_code=413)
    except Exception as e:                             # noqa: BLE001 — unreachable, 404, timeout...
        os.unlink(tmp)
        return JSONResponse({"error": f"the robot couldn't fetch it: {e}"}, status_code=502)
    entry, err = _store(tmp, _title(body.get("title") or url.split("?", 1)[0]), "url")
    return JSONResponse(entry) if entry else JSONResponse({"error": err[1]}, status_code=err[0])


@router.get("/api/play/files")
@_guarded
def list_files() -> JSONResponse:
    return JSONResponse({"files": entries(), "max_mb": MAX_BYTES // (1024 * 1024)})


@router.get("/api/play/files/{name}")
@_guarded
def get_file(name: str):
    if not NAME_RE.match(name) or not os.path.exists(os.path.join(_root(), name)):
        return JSONResponse({"error": "no such file"}, status_code=404)
    return FileResponse(os.path.join(ROOT, name), media_type=MEDIA[name.rsplit(".", 1)[1]],
                        headers={"Cache-Control": "no-cache"})


@router.delete("/api/play/files/{fid}")
@_guarded
def delete_file(fid: str) -> JSONResponse:
    if not re.match(r"^[0-9a-f]{16}$", fid):
        return JSONResponse({"error": "no such file"}, status_code=404)
    return JSONResponse({"deleted": _remove(fid)}, status_code=200)
