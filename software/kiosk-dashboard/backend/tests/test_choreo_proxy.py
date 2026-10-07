import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import choreo_proxy


class _FakeRunner(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/routines":
            return self._send(200, [{"id": "reach", "label": "Reach", "valid": True}])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/run/reach":
            return self._send(409, {"error": "'celebrate' is still running"})
        self._send(404, {"error": "not found"})


def _serve():
    srv = HTTPServer(("127.0.0.1", 0), _FakeRunner)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_forwards_the_runners_answer():
    srv = _serve()
    choreo_proxy.RUNNER_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert choreo_proxy._call("GET", "routines") == (200, [{"id": "reach", "label": "Reach", "valid": True}])
    finally:
        srv.shutdown()


def test_passes_on_the_runners_refusal_in_its_own_words():
    srv = _serve()
    choreo_proxy.RUNNER_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, body = choreo_proxy._call("POST", "run/reach")
        assert code == 409 and "still running" in body["error"]
    finally:
        srv.shutdown()


def test_a_missing_runner_is_a_503_that_says_so():
    choreo_proxy.RUNNER_URL = "http://127.0.0.1:9"      # nothing listens on the discard port
    code, body = choreo_proxy._call("GET", "palette")
    assert code == 503 and "not running" in body["error"]


def test_the_fake_robot_switches_are_not_forwarded():
    paths = {r.path for r in choreo_proxy.router.routes}
    assert not any("sim" in p for p in paths)
    assert paths == {"/api/choreo/routines", "/api/choreo/palette", "/api/choreo/run/{rid}", "/api/choreo/stop"}
