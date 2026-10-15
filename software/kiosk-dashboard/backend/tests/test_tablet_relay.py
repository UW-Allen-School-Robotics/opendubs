"""The robot tablet's sensors into ROS (tablet_relay.py): only from the kiosk, stamped on the Thor's
clock, published through rosbridge, and never a stale position."""
import asyncio
import json
import math
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import tablet_relay as tr


def test_a_stamp_is_the_thor_clock_less_the_age_and_half_the_round_trip():
    s = tr.stamp(200, 40, now=1000.0)                      # 1000 - 0.2 - 0.02
    assert s["sec"] == 999 and abs(s["nanosec"] - 780_000_000) < 1000


def test_light_and_battery_become_their_messages():
    [(topic, m)] = tr.to_messages({"kind": "light", "lux": 312.5, "age_ms": 100}, None, now=50.0)
    assert topic == "/tablet/illuminance" and m["illuminance"] == 312.5 and m["header"]["frame_id"] == "tablet_light_link"
    [(topic, b)] = tr.to_messages({"kind": "battery", "level": 80, "charging": True, "plugged": "usb", "battery_c": 31.5}, None)
    assert topic == "/tablet/battery" and b["percentage"] == 0.8 and b["power_supply_status"] == tr.CHARGING
    assert b["temperature"] == 31.5 and math.isnan(b["voltage"])           # unmeasured: NaN, as the type says
    [(_, b)] = tr.to_messages({"kind": "battery", "level": 80, "charging": False, "plugged": "usb"}, None)
    assert b["power_supply_status"] == tr.NOT_CHARGING                      # a charge limit: plugged, not charging
    [(_, b)] = tr.to_messages({"kind": "battery", "level": 60, "charging": False, "plugged": None}, None)
    assert b["power_supply_status"] == tr.DISCHARGING


def test_a_fix_carries_its_accuracy_and_no_fix_is_said_plainly():
    [(topic, f)] = tr.to_messages({"kind": "fix", "lat": 47.6535, "lon": -122.3056, "acc": 4.0, "alt": 30.0,
                                   "alt_acc": 6.0, "age_ms": 300}, 10)
    assert topic == "/tablet/fix" and f["status"]["status"] == 0 and f["header"]["frame_id"] == "tablet_gps_link"
    assert f["position_covariance"][0] == 16.0 and f["position_covariance"][8] == 36.0
    assert f["position_covariance_type"] == 2
    for reading in ({"kind": "fix", "age_ms": 0},                                        # none
                    {"kind": "fix", "lat": 47.6, "lon": -122.3, "acc": 4.0, "age_ms": 9000},  # stale
                    {"kind": "fix", "lat": 147.6, "lon": -122.3, "acc": 4.0}):          # nonsense
        [(_, f)] = tr.to_messages(reading, None)
        assert f["status"]["status"] == -1 and math.isnan(f["latitude"]), reading


def test_malformed_readings_publish_nothing():
    for bad in ({"kind": "light", "lux": "bright"}, {"kind": "battery", "level": 400}, {"kind": "sonar"}, {}):
        assert tr.to_messages(bad, None) == [], bad


class _Recorder:
    """Stands in for the rosbridge client: records what would be published."""
    def __init__(self):
        self.got, self.ws = [], object()
    def start(self):
        pass
    async def publish(self, topic, msg):
        self.got.append((topic, msg))
        return True


def test_only_the_kiosk_is_heard_and_the_newest_page_wins(monkeypatch):
    rec = _Recorder()
    c = _client(monkeypatch, None, ros=rec)
    with c.websocket_connect("/ws/tablet") as laptop:
        laptop.send_json({"hello": {"kiosk": False}})
        assert "only from the kiosk app" in laptop.receive_json()["error"]
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        assert page.receive_json() == {"ok": True}
        page.send_json({"ping": 12.5})
        assert page.receive_json() == {"pong": 12.5}
        page.send_json({"kind": "light", "lux": 80, "age_ms": 0})
        page.send_json({"kind": "battery", "level": 50, "charging": False, "plugged": None, "thermal": "light"})
        page.send_json({"ping": 1})
        page.receive_json()
        assert [t for t, _ in rec.got] == ["/tablet/illuminance", "/tablet/battery"]
        assert abs(rec.got[0][1]["header"]["stamp"]["sec"] - time.time()) < 5       # the Thor's clock
        with c.websocket_connect("/ws/tablet") as reloaded:                          # the page reloads
            reloaded.send_json({"hello": {"kiosk": True}})
            assert reloaded.receive_json() == {"ok": True}
            reloaded.send_json({"kind": "light", "lux": 5})
            reloaded.send_json({"ping": 2})
            reloaded.receive_json()
        status = c.get("/api/tablet/sensors").json()
        assert status["published"] == 3 and status["thermal"] == "light"


def _fake_rosbridge():
    """A websocket server that records what it is sent."""
    import pytest
    websockets = pytest.importorskip("websockets")      # CI installs no websockets: the stub test covers it
    got, ready = [], threading.Event()
    port = {}

    async def handler(ws):
        async for m in ws:
            got.append(json.loads(m))

    def run():
        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as srv:
                port["n"] = srv.sockets[0].getsockname()[1]
                ready.set()
                await asyncio.Future()
        asyncio.run(main())
    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    return f"ws://127.0.0.1:{port['n']}", got


def _client(monkeypatch, url, ros=None):
    monkeypatch.setattr(tr, "ros", ros or tr.Rosbridge(url))
    tr.state.update(page=None, rtt_ms=None, published=0, dropped=0, last={}, thermal=None)
    app = FastAPI()
    app.include_router(tr.router)
    return TestClient(app)


def _wait(cond, s=5):
    t = time.monotonic()
    while not cond() and time.monotonic() - t < s:
        time.sleep(0.05)
    return cond()


def test_readings_reach_a_real_rosbridge_socket(monkeypatch):
    url, got = _fake_rosbridge()
    c = _client(monkeypatch, url)
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        assert page.receive_json() == {"ok": True}
        assert _wait(lambda: sum(m.get("op") == "advertise" for m in got) == 3)
        assert {m["topic"] for m in got if m["op"] == "advertise"} == {"/tablet/illuminance", "/tablet/battery", "/tablet/fix"}
        page.send_json({"ping": 12.5})
        assert page.receive_json() == {"pong": 12.5}
        page.send_json({"kind": "light", "lux": 80, "age_ms": 0})
        page.send_json({"kind": "battery", "level": 50, "charging": False, "plugged": None, "thermal": "light"})
        assert _wait(lambda: sum(m.get("op") == "publish" for m in got) == 2)
        pub = [m for m in got if m["op"] == "publish"]
        assert pub[0]["topic"] == "/tablet/illuminance" and pub[0]["msg"]["illuminance"] == 80
        assert abs(pub[0]["msg"]["header"]["stamp"]["sec"] - time.time()) < 5          # the Thor's clock
        status = c.get("/api/tablet/sensors").json()
        assert status["page"] and status["published"] == 2


def test_with_rosbridge_down_readings_are_dropped_not_queued(monkeypatch):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()   # nothing listens
    c = _client(monkeypatch, f"ws://127.0.0.1:{port}")
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json()
        page.send_json({"kind": "light", "lux": 80})
        page.send_json({"ping": 1})
        page.receive_json()                                  # the light reading has been handled
        assert c.get("/api/tablet/sensors").json()["dropped"] == 1
