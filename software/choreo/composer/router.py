"""How the dashboard would mount the composer later — ONE include, not wired in this PR.

    # software/kiosk-dashboard/backend/app.py, some future day, BEFORE the static "/" mount:
    #   from router import make_router, static_app
    #   app.include_router(make_router(Composer(SHOWS_DIR, LIMITS)), prefix="/composer")
    #   app.mount("/composer/static", static_app(), name="composer-static")

Not wired because (a) this PR must not touch the dashboard, and (b) the dashboard has no authN
yet. The composer's endpoints cannot move anything, but they can write show files, and an
unauthenticated write surface on the robot's operator UI should be a deliberate decision, not a
side effect of a refactor. The frontend uses relative URLs, so it works unchanged under a prefix.

Requires fastapi (the dashboard already depends on it). The standalone server does not.
"""
from __future__ import annotations

import os

from api import STATIC_DIR, ApiError, Composer  # noqa: F401  (Composer re-exported for callers)


def make_router(comp: "Composer"):
    from fastapi import APIRouter, Body, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse

    r = APIRouter()

    def _wrap(fn, *a):
        try:
            return fn(*a)
        except ApiError as e:
            raise HTTPException(status_code=e.status, detail=str(e)) from e

    @r.get("/", response_class=HTMLResponse)
    def index():
        with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
            return fh.read()

    @r.get("/api/info")
    def info():
        return comp.info()

    @r.get("/api/shows")
    def shows():
        return comp.list_shows()

    @r.get("/api/show")
    def show(path: str):
        return _wrap(comp.load, path)

    @r.post("/api/analyse")
    def analyse(b: dict = Body(...)):
        return _wrap(comp.analyse, b.get("path"), b.get("text") or "")

    @r.post("/api/edit")
    def edit(b: dict = Body(...)):
        return _wrap(comp.edit, b.get("path"), b.get("text") or "", b.get("edits"))

    @r.post("/api/save")
    def save(b: dict = Body(...)):
        return _wrap(comp.save, b.get("path"), b.get("text") or "")

    @r.post("/api/capture")
    def capture(b: dict = Body(...)):
        return _wrap(comp.capture, b.get("side"), b.get("joint_state"))

    @r.get("/audio/{rel:path}")
    def audio(rel: str):
        p, ctype = _wrap(comp.audio, rel)
        return FileResponse(p, media_type=ctype)

    return r


def static_app():
    from fastapi.staticfiles import StaticFiles
    return StaticFiles(directory=STATIC_DIR)
