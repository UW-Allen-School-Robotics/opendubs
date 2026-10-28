"""The ARCHITECT tab's proxy: read-only forwarding of four optional loopback services, and a 503 that
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
    architect_proxy.RUNNER = architect_proxy.LISTENER = architect_proxy.CAMERA = architect_proxy.DAY = url
    app = FastAPI()
    app.include_router(architect_proxy.router)
    return TestClient(app), srv


def test_the_three_services_are_forwarded():
    c, srv = _client()
    try:
        assert c.get("/api/architect/runner").json()["state"] == "ARMED"
        assert c.get("/api/architect/day").status_code == 200        # the same /api/state path
        assert c.get("/api/architect/listener?after=7").json()["asked"] == "/api/feed?after=7"
        r = c.get("/api/architect/frame.jpg")
        assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    finally:
        srv.shutdown()


def test_a_service_that_is_down_is_a_503_that_says_which():
    c, srv = _client(up=False)
    try:
        for path, what in (("/api/architect/runner", "runner"), ("/api/architect/listener", "listener"),
                           ("/api/architect/frame.jpg", "camera"), ("/api/architect/day", "coordinator")):
            r = c.get(path)
            assert r.status_code == 503 and what in r.json()["error"]
    finally:
        srv.shutdown()


def test_nothing_can_be_posted():
    c, srv = _client()
    try:
        for path in ("/api/architect/runner", "/api/architect/listener", "/api/architect/frame.jpg", "/api/architect/day"):
            assert c.post(path).status_code == 405
    finally:
        srv.shutdown()


# ---- /api/architect/state, the face's Halloween ghost ---------------------------------------------
# summarise's two tests are suzy/halloween's (tests/test_architect_proxy.py there), kept as they were.

def test_a_runner_that_is_not_answering_is_unavailable(monkeypatch):
    async def down(url):
        return 503, b"", ""
    monkeypatch.setattr(architect_proxy, "_fetch", down)
    app = FastAPI()
    app.include_router(architect_proxy.router)
    assert TestClient(app).get("/api/architect/state").json() == {"available": False}


def test_the_last_change_and_a_recent_refusal_are_worked_out_on_the_runners_clock():
    st = {"state": "ARMED", "since_s": 1.4, "runs": 3, "events": [
        {"t": 100.0, "state": "ARMED", "event": "run_started", "to": "RUNNING"},
        {"t": 108.0, "state": "RUNNING", "event": "run_ended", "to": "ARMED"},
        {"t": 108.5, "state": "ARMED", "event": "trigger_refused", "why": "not armed"},
        {"t": 108.6, "state": "ARMED", "event": "ignored", "kind": "speech"},
    ]}
    got = architect_proxy.summarise(st, now=110.0)
    assert got == {"available": True, "state": "ARMED", "since_s": 1.4, "runs": 3,
                   "last": "run_ended", "refused_s": 1.5}
    assert architect_proxy.summarise({"state": "IDLE", "events": []}, now=0)["last"] is None
    assert architect_proxy.summarise({"error": "?"}, now=0) == {"available": False}


def test_show_mode_eek_then_happy_and_an_end_already_there_is_old_news():
    """The Halloween demo runs in show mode: no demo runner, the listener starts a palette routine."""
    s = architect_proxy.summarise_show
    seen = {}
    feed = {"status": {"state": "ARMED", "show": "architect-x"}, "events": []}
    old_end = {"running": None, "last": {"id": "architect-x", "status": "COMPLETED", "code": None, "cause": None}}
    assert s(feed, old_end, 100.0, seen)["last"] is None              # ended before we looked: no HAPPY
    feed["events"].append({"t": 105.0, "event": "show_started", "routine": "architect-x"})
    got = s(feed, {"running": "architect-x", "last": old_end["last"]}, 106.0, seen)
    assert (got["state"], got["since_s"]) == ("RUNNING", 1.0)         # EEK!, then FOCUS, by since_s
    got = s(feed, {"running": None, "last": {"id": "architect-x", "status": "REFUSED", "code": "DEADMAN"}}, 109.0, seen)
    assert (got["state"], got["last"], got["since_s"]) == ("ARMED", "run_failed", 0.0)   # PUZZLED
    feed["events"].append({"t": 120.0, "event": "held"})
    assert s(feed, {"running": None}, 121.0, seen)["refused_s"] == 1.0
    assert s({"status": {"state": "ARMED", "show": None}}, None, 0, {}) == {"available": False}   # not show mode
