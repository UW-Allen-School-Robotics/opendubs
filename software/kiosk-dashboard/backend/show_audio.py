"""Show audio: the one device that plays a show's music and sound files (choreography plan 4.2).

The show runner keeps the show clock on the Thor; the music plays on a dashboard client — the tablet
by default. This module is the meeting point, inside the dashboard so a page only ever talks to one
origin (standing rule 12):

  * /ws/show-audio — a page that may play show audio connects here. Over it the page measures the
    Thor's clock (NTP-style pings: the page sends its time, the Thor answers with its own monotonic
    receive and send times), claims the role, and receives the runner's commands.
  * POST /api/choreo/audio — the runner's commands: load these files, start at this Thor time, stop
    with a fade, or cut. Relayed to the ONE device holding the claim, never broadcast.
  * GET /api/choreo/audio — who holds it, and whether what was last loaded is ready.
  * GET /api/choreo/media/<path> — the show's audio files, from the shows directory's audio/ and
    sounds/ only; and `library/songs/<file>`, a song from the show library (plan 4.6).

Decided (plan 4.2): the device downloads the whole file and starts it on cue, rather than streaming;
the last device to press "Play show audio here" holds the role, and the kiosk tablet claims it on
start-up when nobody holds it; if no device is ready, the routine runs silently with a warning.

AMBIENT MUSIC (the "Head & face" page): songs from the library played on the same device while the
robot is not running a show. GET/POST /api/choreo/ambient (the page's API); the hub keeps the state
(persisted, so it survives a restart) and tells the role's holder. A show TAKES OVER the channel: the
device fades ambient out on the runner's `load` and resumes it, from the next song, when the show
ends (`stop` or `cut`). Songs tagged `ambient` in the library (library.py tag ID ambient) are the
default list; any library song may be chosen.

PLAY TO ROBOT (the Tablet lane's /play/ page, agreed 2026-10-14): `{op: "play_file", url, title}`
plays one uploaded file ONCE on the same device, at the ambient volume. The url must be the
dashboard's own `/api/play/files/...` (the Tablet lane's store; a URL someone gave is fetched by the
Thor first), so the device never loads another origin. It interrupts ambient; when it ends, fails or
is stopped (`stop_play`), ambient resumes if it is on. While a show is loaded or running it is refused
(SHOW_AUDIO_BUSY); a show that starts during it cuts it, and it does not come back. `stop` (ambient
off) ends a play too.

AIRPLAY (agreed with the Tablet lane 2026-10-14): the Tablet lane's relay streams AirPlay (shairport-sync
on the Thor) to the face page and reports here, in-process, `hub.airplay_report(active, title, artist)`.
This hub alone decides when it is heard, by sending the holder `{op: "airplay_gate", gate}`:
  * a show (loaded, armed or running) mutes it for the whole show; it comes back after;
  * while AirPlay plays, ambient pauses, and resumes when it ends;
  * a file and AirPlay: the latest wins, both ways — a file sent during AirPlay mutes AirPlay until the
    file ends; AirPlay starting during a file stops the file.
  * (later) voice ducks it.

Nothing here moves the robot. A page that loses this connection stops its own audio (DEC-7: a
tablet drop stops the music and nothing else).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

SHOWS_DIR = os.environ.get("OPENDUBS_CHOREO_SHOWS", "/opt/opendubs/choreo/shows")
MEDIA_SUBDIRS = ("audio", "sounds")
# The show library (software/choreo/library.py): only its songs are served, under media/library/.
LIBRARY_DIR = os.environ.get("OPENDUBS_LIBRARY") or "/var/lib/opendubs/library"
LIBRARY_MEDIA_SUBDIRS = ("songs",)
OPS = ("load", "start", "stop", "cut")
AMBIENT_OPS = ("play", "stop", "volume", "next", "play_file", "stop_play")
PLAY_URL_PREFIX = "/api/play/files/"      # the Tablet lane's store, same origin
PLAY_TITLE_MAX = 120
AMBIENT_STATE = os.environ.get("OPENDUBS_AMBIENT_STATE") or os.path.expanduser("~/.local/state/opendubs/ambient.json")
AMBIENT_DEFAULT = {"playing": False, "songs": [], "shuffle": False, "volume": 0.5}

router = APIRouter()


def thor_ms() -> float:
    """The Thor's monotonic clock in ms — the same CLOCK_MONOTONIC the runner keeps the show on."""
    return time.monotonic() * 1000.0


class Hub:
    """Connected pages, the one holding the show-audio role, and the last load's readiness. Pure
    bookkeeping plus sends, so it is tested without a network."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.clients: dict[int, tuple] = {}     # id -> (websocket, label)
        self.holder: int | None = None
        self.load_id = None                      # the runner's last load
        self.ready_id = None                     # the holder's last "ready"
        self.last_load = None                    # resent to a device that claims mid-show
        self.lock = asyncio.Lock()
        self.show_active = False                 # between the runner's load and its stop/cut
        self.ambient = load_ambient()
        self.device = {"now_playing": None, "blocked": False, "source": None, "error": None}   # the holder's report
        self.airplay = {"active": False, "title": None, "artist": None}   # the Tablet lane's relay reports it
        self.file = None                         # a play_file: None | "sent" | "playing" (seen in a report)
        self.gate_sent = None                    # the last airplay_gate the holder was sent

    def view(self) -> dict:
        lbl = self.clients[self.holder][1] if self.holder in self.clients else None
        return {"holder": lbl, "connected": len(self.clients),
                "load": self.load_id, "ready": self.load_id is not None and self.ready_id == self.load_id}

    async def _send(self, cid, msg):
        ws = self.clients.get(cid, (None,))[0]
        if ws is None:
            return False
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # noqa: BLE001  a dead socket is cleaned up by its own handler
            return False

    async def _announce(self):
        v = self.view()
        for cid in list(self.clients):
            await self._send(cid, {"holder": v["holder"], "you_hold": cid == self.holder})

    async def join(self, ws, label) -> int:
        cid = next(self.ids)
        self.clients[cid] = (ws, label)
        await self._send(cid, {"holder": self.view()["holder"], "you_hold": False})
        return cid

    async def leave(self, cid):
        self.clients.pop(cid, None)
        if self.holder == cid:
            self.holder, self.ready_id = None, None
            await self._announce()

    async def claim(self, cid, label, only_if_free=False) -> bool:
        if only_if_free and self.holder in self.clients:
            return False
        ws = self.clients[cid][0]
        self.clients[cid] = (ws, label or self.clients[cid][1])
        if self.holder != cid:
            old, self.holder, self.ready_id = self.holder, cid, None
            if old in self.clients:
                await self._send(old, {"op": "cut"})     # the old holder stops what it plays
        await self._announce()
        if self.last_load is not None and self.show_active:
            await self._send(cid, self.last_load)        # a new holder gets the current show's files
        if self.ambient["playing"]:
            await self._send(cid, self.ambient_msg())    # ... and the ambient music, if it is on
        self.file = None
        await self._send_gate(force=True)                # ... and whether AirPlay may be heard
        return True

    # ---- AirPlay --------------------------------------------------------------------------------
    def airplay_gate(self) -> str:
        if self.show_active:
            return "mute"
        if self.airplay["active"] and self.file:
            return "mute"                                # a file sent after AirPlay began: latest wins
        return "on"

    async def _send_gate(self, force=False):
        """Only while AirPlay plays: the device's AirPlay player starts muted, and AirPlay beginning
        always sends the gate in force, so with no AirPlay nothing extra goes to the device."""
        if not self.airplay["active"]:
            self.gate_sent = None
            return
        g = self.airplay_gate()
        if self.holder in self.clients and (force or g != self.gate_sent):
            if await self._send(self.holder, {"op": "airplay_gate", "gate": g}):
                self.gate_sent = g

    async def airplay_report(self, active: bool, title: str | None = None, artist: str | None = None):
        """The relay: AirPlay began or ended playing, or its track changed. In-process, never HTTP."""
        was = self.airplay["active"]
        self.airplay = {"active": bool(active), "title": title, "artist": artist}
        if active and not was and self.file and self.holder in self.clients:
            await self._send(self.holder, {"op": "stop_play"})   # AirPlay is the newer: the file stops
            self.file = None
        await self._send_gate(force=bool(active) and not was)
        if bool(active) != was and self.holder in self.clients:
            await self._send(self.holder, self.ambient_msg())    # ambient pauses for it, resumes after

    # ---- ambient music ------------------------------------------------------------------------
    def ambient_msg(self) -> dict:
        songs = {x["id"]: x for x in library_songs()}
        items = [{"id": i, "url": songs[i]["url"], "title": songs[i]["title"]}
                 for i in self.ambient["songs"] if i in songs]
        return {"op": "ambient", **self.ambient, "items": items, "show_active": self.show_active,
                "airplay": self.airplay["active"]}

    async def ambient_command(self, msg) -> dict:
        op = msg.get("op")
        if op in ("play_file", "stop_play"):
            return await self.play_command(msg)
        a = dict(self.ambient)
        if op == "play":
            all_songs = library_songs()
            ids = msg.get("songs")
            if ids is None:
                ids = [x["id"] for x in all_songs if x["ambient"]] or [x["id"] for x in all_songs]
            known = {x["id"] for x in all_songs}
            unknown = [i for i in ids if i not in known]
            if unknown or not ids:
                return {"error": f"not songs in the library: {', '.join(unknown)}" if unknown
                        else "the library has no songs"}
            a.update(playing=True, songs=list(ids), shuffle=bool(msg.get("shuffle", a["shuffle"])))
        elif op == "stop":
            a["playing"] = False
        elif op == "next":
            pass                                  # the device skips; nothing to store
        if op in ("play", "volume") and msg.get("volume") is not None:
            v = msg["volume"]
            if not isinstance(v, (int, float)) or not 0.0 <= v <= 1.0:
                return {"error": "volume is 0..1"}
            a["volume"] = float(v)
        self.ambient = a
        save_ambient(a)
        out = self.ambient_msg()
        if op == "next":
            out = {**out, "next": True}
        if self.holder in self.clients:
            if op == "stop":
                await self._send(self.holder, {"op": "stop_play"})   # ambient off ends a play too
                self.file = None
                await self._send_gate()
            await self._send(self.holder, out)
        return self.ambient_view()

    async def play_command(self, msg) -> dict:
        """Play to robot: one file, once. {error, code?} when refused (the endpoint makes the refusal)."""
        if msg["op"] == "stop_play":
            if self.holder in self.clients:
                await self._send(self.holder, {"op": "stop_play"})
            self.file = None
            await self._send_gate()
            return self.ambient_view()
        url, title = msg.get("url"), msg.get("title", "")
        if not play_url_ok(url):
            return {"error": f"url must be the dashboard's own {PLAY_URL_PREFIX}<file>"}
        if not isinstance(title, str):
            return {"error": "title is text"}
        title = "".join(ch for ch in title if ch.isprintable())[:PLAY_TITLE_MAX] or url.rsplit("/", 1)[-1]
        if self.show_active:
            return {"error": "a show is loaded or running: it has the speaker — try again after it",
                    "code": "SHOW_AUDIO_BUSY"}
        if self.holder not in self.clients:
            return {"error": "no device is playing show audio (the robot's speaker is not connected)",
                    "code": "SHOW_AUDIO_NO_DEVICE"}
        await self._send(self.holder, {"op": "play_file", "url": url, "title": title,
                                       "volume": self.ambient["volume"]})
        self.file = "sent"
        await self._send_gate()                              # AirPlay, if on, is muted for the file
        return self.ambient_view()

    def ambient_view(self) -> dict:
        return {"songs": [{k: x[k] for k in ("id", "title", "duration_s", "ambient")} for x in library_songs()],
                "state": {**self.ambient, "now_playing": self.device["now_playing"],
                          "playing_from": self.device["source"] if self.device["now_playing"] else None},
                "show_active": self.show_active,
                "airplay": {**self.airplay, "gate": self.airplay_gate()},
                "device": {"holder": self.view()["holder"], "blocked": bool(self.device["blocked"]),
                           "error": self.device["error"]}}

    async def device_report(self, cid, report):
        """The holder's report: `now` (an ambient song's id, or a play's title), `source` (ambient |
        play), whether the browser blocked starting it, and the last play that failed. A file seen
        playing and then no longer has ended: AirPlay, muted for it, is heard again."""
        if cid == self.holder and isinstance(report, dict):
            src = report.get("source")
            self.device = {"now_playing": report.get("now"), "blocked": bool(report.get("blocked")),
                           "source": src if src in ("ambient", "play") else ("ambient" if report.get("now") else None),
                           "error": report.get("error")}
            if self.device["source"] == "play" and self.device["now_playing"] and self.file == "sent":
                self.file = "playing"
            elif self.device["source"] != "play" and self.file == "playing":
                self.file = None
                await self._send_gate()

    def ready(self, cid, load_id):
        if cid == self.holder:
            self.ready_id = load_id

    async def command(self, msg) -> dict:
        if msg.get("op") == "load":
            self.load_id, self.ready_id, self.last_load = msg.get("id"), None, msg
            self.show_active = True               # the device fades ambient out for the show
            self.file = None                      # ... and cuts a play, which does not come back
        elif msg.get("op") in ("stop", "cut"):
            self.show_active = False              # ... and resumes it when the show ends
        delivered = await self._send(self.holder, msg) if self.holder in self.clients else False
        await self._send_gate()                   # a show mutes AirPlay; its end lets it back
        return {**self.view(), "delivered": delivered}


def play_url_ok(url) -> bool:
    """Only the dashboard's own play store, as a same-origin path: never a scheme, a host or a way out."""
    if not isinstance(url, str) or not url.startswith(PLAY_URL_PREFIX) or len(url) > 300:
        return False
    rest = url[len(PLAY_URL_PREFIX):]
    return bool(rest) and not rest.startswith("/") and ".." not in rest and "//" not in rest and "\\" not in rest \
        and all(ch.isascii() and (ch.isalnum() or ch in "._-/") for ch in rest)


def load_ambient() -> dict:
    try:
        with open(AMBIENT_STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return {**AMBIENT_DEFAULT, **{k: d[k] for k in AMBIENT_DEFAULT if k in d}}
    except (OSError, ValueError):
        return dict(AMBIENT_DEFAULT)


def save_ambient(a: dict) -> bool:
    """Kept across restarts, so "ambient on" survives a reboot. Best effort: unwritable is in memory only."""
    try:
        os.makedirs(os.path.dirname(AMBIENT_STATE), exist_ok=True)
        tmp = AMBIENT_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(a, fh)
        os.replace(tmp, AMBIENT_STATE)
        return True
    except OSError:
        return False


def library_songs() -> list:
    """Every song in the show library: {id, title, duration_s, ambient, url}. `ambient`: tagged so."""
    try:
        import library_serve
        lib = library_serve._library()
        idx = (lib.load_index(LIBRARY_DIR) if lib else None) or {}
    except Exception:  # noqa: BLE001  no library: no songs, never a crash
        idx = {}
    out = []
    for i, e in sorted(idx.items()):
        if e.get("kind") != "song" or not e.get("file"):
            continue
        out.append({"id": i, "title": e.get("title") or i.replace("_", " ").replace("-", " "),
                    "duration_s": e.get("duration_s"), "ambient": "ambient" in (e.get("tags") or []),
                    "url": "/api/choreo/media/library/" + e["file"]})
    return out


hub = Hub()


@router.websocket("/ws/show-audio")
async def show_audio_ws(ws: WebSocket):
    # A page on another origin may not claim the show-audio role (Hygiene S4); scripts and the
    # runner send no Origin and pass.
    import origin_guard
    if await origin_guard.refuse_websocket(ws):
        return
    await ws.accept()
    cid = await hub.join(ws, "a browser")
    try:
        while True:
            m = await ws.receive_json()
            if "ping" in m:      # clock: answered at once, with receive and send times
                t1 = thor_ms()
                await ws.send_json({"pong": m["ping"], "t1": t1, "t2": thor_ms()})
            elif "claim" in m:
                await hub.claim(cid, str(m.get("label") or "a browser")[:40], bool(m.get("only_if_free")))
            elif "ready" in m:
                hub.ready(cid, m["ready"])
            elif "ambient" in m:                 # the holder: what it is playing, and whether it may
                await hub.device_report(cid, m["ambient"])
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await hub.leave(cid)


@router.get("/api/choreo/audio")
async def audio_state():
    return JSONResponse(hub.view())


@router.get("/api/choreo/ambient")
async def ambient_state():
    return JSONResponse(hub.ambient_view())


@router.post("/api/choreo/ambient")
async def ambient_command(request: Request):
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(msg, dict) or msg.get("op") not in AMBIENT_OPS:
        return JSONResponse({"error": f"op must be one of {', '.join(AMBIENT_OPS)}"}, status_code=400)
    out = await hub.ambient_command(msg)
    from refusals import refuse
    if out.get("code") == "SHOW_AUDIO_BUSY":
        return refuse("SHOW_AUDIO_BUSY", out["error"], 409)
    if out.get("code") == "SHOW_AUDIO_NO_DEVICE":
        return refuse("SHOW_AUDIO_NO_DEVICE", out["error"], 503)
    return JSONResponse(out, status_code=400 if "error" in out else 200)


@router.post("/api/choreo/audio")
async def audio_command(request: Request):
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse({"error": "not JSON"}, status_code=400)
    if not isinstance(msg, dict) or msg.get("op") not in OPS:
        return JSONResponse({"error": f"op must be one of {', '.join(OPS)}"}, status_code=400)
    return JSONResponse(await hub.command(msg))


def media_path(rel: str) -> str | None:
    """A show's audio file, or None: only under the shows directory's audio/ or sounds/, or the
    library's songs/ (as `library/songs/...`)."""
    if rel.startswith("library/"):
        root = os.path.realpath(LIBRARY_DIR)
        full = os.path.realpath(os.path.join(root, rel[len("library/"):]))
        if not any(full.startswith(os.path.join(root, d) + os.sep) for d in LIBRARY_MEDIA_SUBDIRS):
            return None
        return full if os.path.isfile(full) else None
    root = os.path.realpath(SHOWS_DIR)
    full = os.path.realpath(os.path.join(root, rel))
    if not any(full.startswith(os.path.join(root, d) + os.sep) for d in MEDIA_SUBDIRS):
        return None
    return full if os.path.isfile(full) else None


@router.get("/api/choreo/media/{rel:path}")
async def media(rel: str):
    p = media_path(rel)
    if p is None:
        return JSONResponse({"error": "no such show audio file"}, status_code=404)
    return FileResponse(p)
