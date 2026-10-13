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
    # /api/choreo/robot is the dashboard's own read-only answer, not a forward to the runner
    # ... and the show-audio routes (show_audio.py), which the dashboard serves itself
    assert paths == {"/api/choreo/routines", "/api/choreo/palette", "/api/choreo/run/{rid}", "/api/choreo/stop",
                     "/api/choreo/robot", "/ws/show-audio", "/api/choreo/audio", "/api/choreo/media/{rel:path}",
                     "/api/choreo/ambient"}


# ---- /api/choreo/robot: the runner's view of the robot (plan 4.4c) -----------------------------
def _rs(**inputs):
    base = {"authority": "AUTONOMOUS", "deadman": True, "estopped": False, "faults": [], "charging": False}
    return {"state": "AUTONOMOUS_CONTROL", "inputs": {**base, **inputs}}


FRESH = {"fresh": True, "motion_enabled": True}


def test_a_healthy_robot_reads_as_the_runner_names_it():
    v = choreo_proxy.robot_view(_rs(), FRESH, {"fresh": True}, "active", True)
    assert (v["estop"], v["ch5"], v["deadman"], v["fault"], v["arms"], v["tab5"]) == \
        (False, "AUTO", True, None, "active", True)


def test_ch5_uses_the_formats_names():
    for auth, name in (("OFF", "STANDBY"), ("RC_MANUAL", "MANUAL"), ("AUTONOMOUS", "AUTO"), (None, None)):
        assert choreo_proxy.robot_view(_rs(authority=auth), FRESH, {"fresh": True})["ch5"] == name


def test_a_lost_rc_link_makes_ch5_and_the_deadman_unknown_not_false():
    v = choreo_proxy.robot_view(_rs(), FRESH, {"fresh": False})
    assert v["ch5"] is None and v["deadman"] is None


def test_a_stale_stamplc_makes_the_estop_unknown():
    v = choreo_proxy.robot_view(_rs(estopped=True), {"fresh": False}, {"fresh": True})
    assert v["estop"] is None and v["motion_power"] is None


def test_faults_starting_up_and_charging_all_refuse():
    assert "WELDED" in choreo_proxy.robot_view(_rs(faults=["CONTACTOR WELDED"]), FRESH, {"fresh": True})["fault"]
    assert "starting up" in choreo_proxy.robot_view({**_rs(), "state": "INIT"}, FRESH, {"fresh": True})["fault"]
    assert "charging" in choreo_proxy.robot_view(_rs(charging=True), FRESH, {"fresh": True})["fault"]


def test_arms_are_active_only_when_every_motor_is_enabled_and_clean():
    f = choreo_proxy.arms_from_summary
    assert f("enabled=16 disabled=0 faults=0 missing=0", None) == "active"
    assert f("enabled=8 disabled=8 faults=0 missing=0", None) == "inactive"
    assert f("enabled=16 disabled=0 faults=1 missing=0", None) == "inactive"
    assert f("enabled=0 disabled=0 faults=0 missing=16", None) == "inactive"
    assert f("", "failed") is None and f("garbage", None) is None


def test_a_run_is_forwarded_without_a_live_mark():
    # Since 2026-10-08 (PR #71) the runner decides LIVE itself: a runner with the arms drives them on
    # every run. The dashboard forwards a run as is, and a stale ?live=1 from an old page is dropped.
    seen = []

    class Rec(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(self.path)
            b = b'{"started": "reach"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    srv = HTTPServer(("127.0.0.1", 0), Rec)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    choreo_proxy.RUNNER_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(choreo_proxy.router)
        c = TestClient(app)
        c.post("/api/choreo/run/reach")
        c.post("/api/choreo/run/reach?live=1")
        assert seen == ["/api/run/reach", "/api/run/reach"]
    finally:
        srv.shutdown()
