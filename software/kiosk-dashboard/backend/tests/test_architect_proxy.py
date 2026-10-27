"""The ARCHITECT tab's proxy: read-only forwarding of three optional loopback services, and a 503 that
says which one is down, never an error page."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import architect_proxy  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fake jpeg\xff\xd9"


class _Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/state":
            return self._send(200, {"state": "ARMED", "code": "reset_robot()\n", "sha": "abc"})
        if self.path.startswith("/api/feed"):
            return self._send(200, {"seq": 3, "asked": self.path, "events": []})
        if self.path == "/frame.jpg":
            return self._send(200, JPEG, "image/jpeg")
        self._send(404, {"error": "not found"})


def _client(up=True):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}" if up else "http://127.0.0.1:9"   # 9: nobody listens
    architect_proxy.RUNNER = architect_proxy.LISTENER = architect_proxy.CAMERA = url
    app = FastAPI()
    app.include_router(architect_proxy.router)
    return TestClient(app), srv


def test_the_three_services_are_forwarded():
    c, srv = _client()
    try:
        assert c.get("/api/architect/runner").json()["state"] == "ARMED"
        assert c.get("/api/architect/listener?after=7").json()["asked"] == "/api/feed?after=7"
        r = c.get("/api/architect/frame.jpg")
        assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    finally:
        srv.shutdown()


def test_a_service_that_is_down_is_a_503_that_says_which():
    c, srv = _client(up=False)
    try:
        for path, what in (("/api/architect/runner", "runner"), ("/api/architect/listener", "listener"),
                           ("/api/architect/frame.jpg", "camera")):
            r = c.get(path)
            assert r.status_code == 503 and what in r.json()["error"]
    finally:
        srv.shutdown()


def test_nothing_can_be_posted():
    c, srv = _client()
    try:
        for path in ("/api/architect/runner", "/api/architect/listener", "/api/architect/frame.jpg"):
            assert c.post(path).status_code == 405
    finally:
        srv.shutdown()
