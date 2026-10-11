"""The dashboard refuses state-changing requests another web page sent, and nothing else."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import origin_guard


def _client(host="10.42.2.10:8080"):
    app = FastAPI()
    origin_guard.install(app)

    @app.post("/api/estop/clear")
    def clear():
        return {"ok": True}

    @app.get("/api/status")
    def status():
        return {"ok": True}

    return TestClient(app, base_url=f"http://{host}")


def test_no_origin_passes():
    """curl, scripts and the Thor's own services send no Origin: they must keep working."""
    assert _client().post("/api/estop/clear").status_code == 200


def test_the_dashboards_own_page_passes():
    c = _client()
    assert c.post("/api/estop/clear", headers={"Origin": "http://10.42.2.10:8080"}).status_code == 200


def test_the_https_page_of_the_same_robot_passes():
    c = _client("10.42.2.10:8443")
    assert c.post("/api/estop/clear", headers={"Origin": "https://10.42.2.10:8443"}).status_code == 200
    c = _client("10.42.2.10:8080")
    assert c.post("/api/estop/clear", headers={"Origin": "https://10.42.2.10:8443"}).status_code == 200


def test_a_name_and_its_address_are_compared_as_sent():
    c = _client("opendubs2.local:8443")
    assert c.post("/api/estop/clear", headers={"Origin": "https://opendubs2.local:8443"}).status_code == 200


def test_another_page_is_refused_with_its_code():
    r = _client().post("/api/estop/clear", headers={"Origin": "http://10.42.2.99:3000"})
    assert r.status_code == 403
    body = r.json()
    assert body["code"] == "LINK_FOREIGN_ORIGIN" and body["ok"] is False and "10.42.2.99:3000" in body["error"]


def test_same_host_other_port_is_refused():
    """A different service on the Thor itself is a different page."""
    r = _client().post("/api/estop/clear", headers={"Origin": "http://10.42.2.10:8097"})
    assert r.status_code == 403


def test_null_origin_is_refused():
    """A sandboxed or file page sends Origin: null — present, and not this dashboard."""
    assert _client().post("/api/estop/clear", headers={"Origin": "null"}).status_code == 403


def test_referer_is_used_when_origin_is_absent():
    c = _client()
    assert c.post("/api/estop/clear", headers={"Referer": "http://evil.example/page"}).status_code == 403
    assert c.post("/api/estop/clear", headers={"Referer": "http://10.42.2.10:8080/#shows"}).status_code == 200


def test_reads_are_never_refused():
    assert _client().get("/api/status", headers={"Origin": "http://evil.example"}).status_code == 200


def test_the_kiosk_asset_origin_passes_by_default(monkeypatch):
    monkeypatch.delenv("OPENDUBS_DASH_ALLOWED_ORIGINS", raising=False)
    r = _client().post("/api/estop/clear", headers={"Origin": "https://appassets.androidplatform.net"})
    assert r.status_code == 200


def test_extra_origins_from_the_robots_settings(monkeypatch):
    monkeypatch.setenv("OPENDUBS_DASH_ALLOWED_ORIGINS", "http://laptop.local:8097")
    assert _client().post("/api/estop/clear", headers={"Origin": "http://laptop.local:8097"}).status_code == 200
    assert _client().post("/api/estop/clear", headers={"Origin": "https://appassets.androidplatform.net"}).status_code == 403


def _ws_client(host="10.42.2.10:8080"):
    from fastapi import WebSocket
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        if await origin_guard.refuse_websocket(socket):
            return
        await socket.accept()
        await socket.send_text("hello")
        await socket.close()

    return TestClient(app, base_url=f"http://{host}")


def test_a_websocket_from_the_dashboard_connects():
    # TestClient's websocket_connect sends Host: testserver whatever the base URL, so set the Host a
    # browser on the robot would send.
    with _ws_client().websocket_connect("/ws", headers={"Origin": "http://10.42.2.10:8080",
                                                        "Host": "10.42.2.10:8080"}) as w:
        assert w.receive_text() == "hello"


def test_a_websocket_with_no_origin_connects():
    with _ws_client().websocket_connect("/ws") as w:
        assert w.receive_text() == "hello"


def test_a_websocket_from_another_page_is_refused():
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with pytest.raises(WebSocketDisconnect) as e:
        with _ws_client().websocket_connect("/ws", headers={"Origin": "http://evil.example"}) as w:
            w.receive_text()
    assert e.value.code == 1008


def test_the_dashboard_installs_it():
    src = open(os.path.join(os.path.dirname(__file__), "..", "app.py")).read()
    assert "origin_guard.install(app)" in src
    # both of the dashboard's own sockets check, before they accept
    assert src.count("await origin_guard.refuse_websocket(socket)") == 2
