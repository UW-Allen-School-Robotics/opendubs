#!/usr/bin/env python3
"""The composer, standalone: a tiny HTTP server over api.py and the static editor.

    python3 server.py                          # http://127.0.0.1:8091, shows from ../shows
    python3 server.py --limits <arm software>/hw_validation/joint_limits.yaml
    python3 server.py --host 0.0.0.0           # reachable from a tablet on the same network
    python3 server.py --library ~/opendubs-library   # where songs are added (default: OPENDUBS_LIBRARY,
                                                     # else /var/lib/opendubs/library)

Standard library + PyYAML. No build step, nothing loaded from a CDN — the robot's LAN may have no
internet, and the editor must work there.

PREVIEW ONLY. This server reads and writes show files and nothing else. It cannot move the robot:
there is no robot-facing code path in it, by design (workplan 5.4: the default output sink is the
preview; live output is a separately armed future binding).

--host 0.0.0.0 has NO authentication — anyone who can reach the port can edit the show files in
the shows directory, and add or delete songs in the library (and nothing else). Uploads are limited
to song types, checked by content, and to api.MAX_SONG_BYTES. That is a smaller exposure than the dashboard's, which
can move things, but it is still an exposure: bind to localhost unless a tablet needs it.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from api import MAX_SONG_BYTES, STATIC_DIR, ApiError, Composer  # noqa: E402

STATIC = STATIC_DIR
MAX_BODY = 2 * 1024 * 1024


def make_handler(comp: Composer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "opendubs-composer/1"

        def log_message(self, fmt, *args):          # quieter than the default
            line = str(args[0] if args else "")       # send_error passes an HTTPStatus here
            # the twins poll /api/twin many times a second: logging those would bury everything else
            if "/api/" in line and "/api/twin" not in line and "/api/playhead" not in line:
                sys.stderr.write("%s\n" % (fmt % args))

        # ---- helpers ----
        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ApiError("request too large", 413)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError as e:
                raise ApiError(f"bad JSON: {e}") from e

        def _file(self, path, ctype=None):
            with open(path, "rb") as fh:
                data = fh.read()
            self._send(200, data, ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")

        # ---- routes ----
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
                if u.path.startswith("/static/"):
                    p = os.path.realpath(os.path.join(STATIC, unquote(u.path[len("/static/"):])))
                    if not p.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(p):
                        raise ApiError("not found", 404)
                    return self._file(p)
                if u.path == "/api/info":
                    return self._json(comp.info())
                if u.path == "/api/shows":
                    return self._json(comp.list_shows())
                if u.path == "/api/twin":
                    return self._json(comp.twin())
                if u.path == "/api/twin/timeline":
                    return self._json(comp.twin_timeline())
                if u.path == "/api/show":
                    return self._json(comp.load((q.get("path") or [""])[0]))
                if u.path.startswith("/audio/"):
                    p, ctype = comp.audio(unquote(u.path[len("/audio/"):]))
                    return self._file(p, ctype)
                if u.path == "/api/library/songs":
                    return self._json(comp.songs())
                if u.path == "/api/show/disk":
                    return self._json(comp.disk((q.get("path") or [""])[0]))
                if u.path == "/api/history":
                    return self._json(comp.history((q.get("path") or [""])[0]))
                if u.path == "/api/notes/prompt":
                    return self._json(comp.notes_prompt((q.get("path") or [""])[0]))
                if u.path.startswith("/library/song/"):
                    p, ctype = comp.song_audio(unquote(u.path[len("/library/song/"):]))
                    return self._file(p, ctype)
                raise ApiError("not found", 404)
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)
            except Exception as e:  # noqa: BLE001
                return self._crash(e)

        def _upload(self) -> bytes:
            """A raw upload body: by its declared length (refused before a byte is read when that is
            over the limit), or chunked, read up to the limit."""
            limit = f"over the {MAX_SONG_BYTES // (1024 * 1024)} MB limit for a song"
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                out = bytearray()
                while True:
                    n = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if n == 0:
                        while self.rfile.readline().strip():     # trailers, then the blank line
                            pass
                        return bytes(out)
                    if len(out) + n > MAX_SONG_BYTES:
                        self.close_connection = True
                        raise ApiError(limit, 413)
                    out += self.rfile.read(n)
                    self.rfile.readline()
            if self.headers.get("Content-Length") is None:
                raise ApiError("the upload came with no length — the browser sent no Content-Length", 411)
            n = int(self.headers["Content-Length"])
            if n > MAX_SONG_BYTES:
                self.close_connection = True
                raise ApiError(limit, 413)
            return self.rfile.read(n)

        def _crash(self, e: Exception):
            """Anything unexpected comes back as a 500 the editor can show, never a dropped connection
            (which a browser reports only as "Load failed" / "Failed to fetch")."""
            traceback.print_exc()
            try:
                self._json({"error": f"the composer hit an error: {type(e).__name__}: {e} (its log has the details)"}, 500)
            except OSError:
                pass

        def do_POST(self):
            u = urlparse(self.path)
            try:
                if u.path == "/api/library/songs":
                    # the file is the body; what describes it is in the query string
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    data = self._upload()
                    return self._json(comp.add_song(q.get("filename", ""), data, q.get("id"), q.get("source"),
                                                    q.get("licence"), q.get("duration_s")))
                b = self._body()
                if u.path == "/api/history/read":
                    return self._json(comp.history_read(b.get("path"), b.get("name"), b.get("text") or ""))
                if u.path == "/api/diff":
                    return self._json(comp.diff(b.get("a") or "", b.get("b") or ""))
                if u.path == "/api/library/songs/delete":
                    return self._json(comp.delete_song(b.get("id"), b.get("confirm")))
                if u.path == "/api/analyse":
                    return self._json(comp.analyse(b.get("path"), b.get("text") or ""))
                if u.path == "/api/edit":
                    return self._json(comp.edit(b.get("path"), b.get("text") or "", b.get("edits")))
                if u.path == "/api/save":
                    return self._json(comp.save(b.get("path"), b.get("text") or ""))
                if u.path == "/api/playhead":
                    return self._json(comp.playhead(b.get("t"), b.get("playing")))
                if u.path == "/api/earliest":
                    return self._json(comp.earliest(b.get("path"), b.get("text") or "", b.get("slot"),
                                                    b.get("joints"), b.get("t"), b.get("index")))
                if u.path == "/api/capture":
                    return self._json(comp.capture(b.get("side"), b.get("joint_state")))
                raise ApiError("not found", 404)
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)
            except Exception as e:  # noqa: BLE001
                return self._crash(e)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shows", default=os.path.join(os.path.dirname(HERE), "shows"))
    ap.add_argument("--limits", default=None,
                    help="the arm software's joint_limits.yaml; default: the container path if "
                         "present, else the TEST FIXTURE (and the editor says so)")
    ap.add_argument("--library", default=None,
                    help="the show library songs are added to (default: OPENDUBS_LIBRARY, else "
                         "/var/lib/opendubs/library) — outside the repo, never committed")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8091)
    a = ap.parse_args()

    comp = Composer(a.shows, a.limits, a.library)
    print(f"composer: shows from {comp.shows_dir}")
    print(f"composer: library {comp.library_dir}"
          + ("" if comp.info()["library_exists"] else "   (empty: the first song added creates it)"))
    print(f"composer: limits {comp.limits_path}"
          + ("   <-- TEST FIXTURE, not the robot's file" if comp.fixture else ""))
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("composer: WARNING listening beyond localhost with no authentication — anyone who "
              "can reach this port can edit the show files (it cannot move the robot)")
    print(f"composer: preview only — open http://{a.host}:{a.port}/")
    ThreadingHTTPServer((a.host, a.port), make_handler(comp)).serve_forever()


if __name__ == "__main__":
    main()
