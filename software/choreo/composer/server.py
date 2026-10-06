#!/usr/bin/env python3
"""The composer, standalone: a tiny HTTP server over api.py and the static editor.

    python3 server.py                          # http://127.0.0.1:8091, shows from ../shows
    python3 server.py --limits <arm software>/hw_validation/joint_limits.yaml
    python3 server.py --host 0.0.0.0           # reachable from a tablet on the same network

Standard library + PyYAML. No build step, nothing loaded from a CDN — the robot's LAN may have no
internet, and the editor must work there.

PREVIEW ONLY. This server reads and writes show files and nothing else. It cannot move the robot:
there is no robot-facing code path in it, by design (workplan 5.4: the default output sink is the
preview; live output is a separately armed future binding).

--host 0.0.0.0 has NO authentication — anyone who can reach the port can edit the show files in
the shows directory (and nothing else). That is a smaller exposure than the dashboard's, which
can move things, but it is still an exposure: bind to localhost unless a tablet needs it.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from api import STATIC_DIR, ApiError, Composer  # noqa: E402

STATIC = STATIC_DIR
MAX_BODY = 2 * 1024 * 1024


def make_handler(comp: Composer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "opendubs-composer/1"

        def log_message(self, fmt, *args):          # quieter than the default
            if "/api/" in (args[0] if args else ""):
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
                if u.path == "/api/show":
                    return self._json(comp.load((q.get("path") or [""])[0]))
                if u.path.startswith("/audio/"):
                    p, ctype = comp.audio(unquote(u.path[len("/audio/"):]))
                    return self._file(p, ctype)
                raise ApiError("not found", 404)
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)

        def do_POST(self):
            u = urlparse(self.path)
            try:
                b = self._body()
                if u.path == "/api/analyse":
                    return self._json(comp.analyse(b.get("path"), b.get("text") or ""))
                if u.path == "/api/edit":
                    return self._json(comp.edit(b.get("path"), b.get("text") or "", b.get("edits")))
                if u.path == "/api/save":
                    return self._json(comp.save(b.get("path"), b.get("text") or ""))
                if u.path == "/api/capture":
                    return self._json(comp.capture(b.get("side"), b.get("joint_state")))
                raise ApiError("not found", 404)
            except ApiError as e:
                return self._json({"error": str(e)}, e.status)

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shows", default=os.path.join(os.path.dirname(HERE), "shows"))
    ap.add_argument("--limits", default=None,
                    help="the arm software's joint_limits.yaml; default: the container path if "
                         "present, else the TEST FIXTURE (and the editor says so)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8091)
    a = ap.parse_args()

    comp = Composer(a.shows, a.limits)
    print(f"composer: shows from {comp.shows_dir}")
    print(f"composer: limits {comp.limits_path}"
          + ("   <-- TEST FIXTURE, not the robot's file" if comp.fixture else ""))
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("composer: WARNING listening beyond localhost with no authentication — anyone who "
              "can reach this port can edit the show files (it cannot move the robot)")
    print(f"composer: preview only — open http://{a.host}:{a.port}/")
    ThreadingHTTPServer((a.host, a.port), make_handler(comp)).serve_forever()


if __name__ == "__main__":
    main()
