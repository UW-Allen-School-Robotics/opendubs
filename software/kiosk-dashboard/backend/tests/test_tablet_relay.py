"""The robot tablet's sensors into ROS (tablet_relay.py): only from the kiosk, stamped on the Thor's
clock, published through rosbridge, and never a stale position."""
import asyncio
import base64
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
    def sensors(self):
        """What was published, less /diagnostics (the thermal status, which also goes out on a timer)."""
        return [(t, m) for t, m in self.got if t != "/diagnostics"]
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
        assert "camera" in page.receive_json()              # the cameras' state: all off
        page.send_json({"ping": 12.5})
        assert page.receive_json() == {"pong": 12.5}
        page.send_json({"kind": "light", "lux": 80, "age_ms": 0})
        page.send_json({"kind": "battery", "level": 50, "charging": False, "plugged": None, "thermal": "light"})
        page.send_json({"ping": 1})
        page.receive_json()
        assert [t for t, _ in rec.sensors()] == ["/tablet/illuminance", "/tablet/battery"]
        assert abs(rec.sensors()[0][1]["header"]["stamp"]["sec"] - time.time()) < 5       # the Thor's clock
        with c.websocket_connect("/ws/tablet") as reloaded:                          # the page reloads
            reloaded.send_json({"hello": {"kiosk": True}})
            assert reloaded.receive_json() == {"ok": True}
            reloaded.receive_json()
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
    tr.state.update(page=None, rtt_ms=None, published=0, dropped=0, last={}, thermal=None,
                    camera={c: {"mode": "auto", "subscribed": False, "on": False, "frames": 0, "dropped": 0,
                                "busy": False, "error": None, "w": None, "h": None} for c in tr.CAMERAS})
    monkeypatch.setattr(tr, "CAMERA_TICK_S", 0.1)
    tr.state["demand"] = {"at": None, "counts": None}
    tr.power.update(readings=[], command=None)
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
        page.receive_json()                                  # the cameras' state
        assert _wait(lambda: sum(m.get("op") == "advertise" for m in got) == len(tr.TOPICS))
        assert {m["topic"] for m in got if m["op"] == "advertise"} == set(tr.TOPICS)
        assert {"/tablet/illuminance", "/tablet/battery", "/tablet/fix",
                "/tablet/camera/rear/image_raw/compressed"} <= set(tr.TOPICS)
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
        page.receive_json(); page.receive_json()
        page.send_json({"kind": "light", "lux": 80})
        page.send_json({"ping": 1})
        page.receive_json()                                  # the light reading has been handled
        assert c.get("/api/tablet/sensors").json()["dropped"] == 1


JPEG = b"\xff\xd8\xff\xe0" + b"x" * 200 + b"\xff\xd9"


def _frame(cam, w=640, h=480, jpeg=JPEG):
    head = json.dumps({"cam": cam, "w": w, "h": h, "age_ms": 20}).encode()
    return len(head).to_bytes(4, "big") + head + jpeg


def test_a_frame_is_parsed_and_anything_else_refused():
    hdr, jpeg = tr.parse_frame(_frame("rear"))
    assert hdr["cam"] == "rear" and jpeg == JPEG
    for bad in (b"", b"\x00\x00\x00\x05{}", _frame("side"), _frame("front", jpeg=b"PNG...."), b"\x00\x00\xff\xff{"):
        assert tr.parse_frame(bad) is None, bad


def _drain_until(page, pred, n=20):
    for _ in range(n):
        m = page.receive_json()
        if pred(m):
            return m
    raise AssertionError("not seen")


def test_a_camera_streams_only_while_something_subscribes(monkeypatch):
    rec = _Recorder()
    c = _client(monkeypatch, None, ros=rec)
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json()
        first = page.receive_json()["camera"]
        assert first["front"]["on"] is False and first["rear"]["on"] is False      # off until asked
        page.send_bytes(_frame("front"))                                              # unasked: dropped
        assert c.post("/api/tablet/camera/demand", json={"front": True, "rear": False,
                                                         "counts": {"front": 1, "rear": 0}}).status_code == 200
        m = _drain_until(page, lambda m: "camera" in m)
        assert m["camera"]["front"]["on"] is True and m["camera"]["rear"]["on"] is False
        assert m["camera"]["front"]["w"] == 640 and m["camera"]["front"]["fps"] == 10
        page.send_bytes(_frame("front"))
        assert _wait(lambda: len(rec.sensors()) == 2)
        (t1, img), (t2, info) = rec.sensors()
        assert t1 == "/tablet/camera/front/image_raw/compressed" and img["format"] == "jpeg"
        assert base64.b64decode(img["data"]) == JPEG and img["header"]["frame_id"] == "tablet_front_camera_optical_frame"
        assert t2 == "/tablet/camera/front/camera_info" and info["width"] == 640 and info["k"] == [0.0] * 9
        assert info["header"] == img["header"]                                       # the same instant
        c.post("/api/tablet/camera/demand", json={"front": False, "rear": False})     # the consumer goes
        m = _drain_until(page, lambda m: "camera" in m)
        assert m["camera"]["front"]["on"] is False
        st = c.get("/api/tablet/sensors").json()["camera"]["front"]
        assert st["frames"] == 1 and st["dropped"] == 1


def test_a_camera_can_be_switched_on_by_hand(monkeypatch):
    c = _client(monkeypatch, None, ros=_Recorder())
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json(); page.receive_json()
        r = c.put("/api/tablet/camera", json={"rear": "on"})
        assert r.status_code == 200 and r.json()["rear"] == {"mode": "on", "on": True}
        assert _drain_until(page, lambda m: "camera" in m)["camera"]["rear"]["on"] is True
        assert c.put("/api/tablet/camera", json={"rear": "sometimes"}).status_code == 400
        assert c.put("/api/tablet/camera", json={"side": "on"}).status_code == 400


def test_camera_info_carries_this_robots_calibration_when_it_has_one(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "CAMERA_DIR", str(tmp_path))
    (tmp_path / "camera_front.yaml").write_text(
        "image_width: 640\nimage_height: 480\ndistortion_model: plumb_bob\n"
        "camera_matrix: {rows: 3, cols: 3, data: [500, 0, 320, 0, 500, 240, 0, 0, 1]}\n"
        "distortion_coefficients: {rows: 1, cols: 5, data: [0.1, -0.2, 0, 0, 0]}\n"
        "rectification_matrix: {rows: 3, cols: 3, data: [1, 0, 0, 0, 1, 0, 0, 0, 1]}\n"
        "projection_matrix: {rows: 3, cols: 4, data: [500, 0, 320, 0, 0, 500, 240, 0, 0, 0, 1, 0]}\n")
    info = tr.camera_info("front", 640, 480, {})
    assert info["k"][0] == 500.0 and info["d"][:2] == [0.1, -0.2] and info["distortion_model"] == "plumb_bob"
    assert tr.camera_info("front", 1280, 720, {})["k"] == [0.0] * 9      # calibrated at another size: not used
    assert tr.camera_info("rear", 640, 480, {})["k"] == [0.0] * 9        # none kept: uncalibrated


def test_imu_is_acceleration_only_and_says_the_rest_is_absent():
    msgs = tr.to_messages({"kind": "imu", "samples": [{"ax": 0.1, "ay": 0.2, "az": 9.8, "age_ms": 30},
                                                      {"ax": "x", "ay": 0, "az": 0},           # malformed: skipped
                                                      {"ax": 0.0, "ay": 0.0, "az": 9.81, "age_ms": 10}]}, 4, now=100.0)
    assert [t for t, _ in msgs] == ["/tablet/imu", "/tablet/imu"]
    m = msgs[0][1]
    assert m["header"]["frame_id"] == "tablet_imu_link" and m["linear_acceleration"]["z"] == 9.8
    assert m["orientation_covariance"][0] == -1 and m["angular_velocity_covariance"][0] == -1   # no gyroscope
    assert msgs[0][1]["header"]["stamp"]["nanosec"] < msgs[1][1]["header"]["stamp"]["nanosec"]   # older sample, earlier


def _imu(ax, ay, az, t):
    sec = int(t)
    return {"header": {"stamp": {"sec": sec, "nanosec": int((t - sec) * 1e9)}}, "linear_acceleration": {"x": ax, "y": ay, "z": az}}


def test_tilt_only_while_still_and_impacts_counted_once():
    tr.state.update(tilt=None, impacts=[])
    tr.note_motion(_imu(0.0, 9.80665 * math.sin(math.radians(10)), 9.80665 * math.cos(math.radians(10)), 1.0))
    assert tr.state["tilt"]["pitch_deg"] == 10.0 and tr.state["tilt"]["roll_deg"] == 0.0
    tr.note_motion(_imu(0.0, 5.0, 12.0, 1.1))                  # 1.33 g: moving, the tilt is not updated
    assert tr.state["tilt"]["t"] == 1.0
    for t, g in ((2.00, 2.5), (2.02, 3.4), (2.04, 2.2), (3.0, 2.1)):   # one knock of three samples, then another
        tr.note_motion(_imu(0.0, 0.0, g * 9.80665, t))
    assert [i["peak_g"] for i in tr.state["impacts"]] == [3.4, 2.1]


def test_thermal_goes_to_diagnostics_with_the_agreed_levels():
    assert [tr.thermal_status(w, 1)["level"] for w in tr.THERMAL] == [0, 0, 1, 1, 2, 2, 2]
    st = tr.thermal_status("severe", 1)
    assert st["name"] == "tablet: thermal" and st["hardware_id"] == "galaxy-tab-a11" and st["message"] == "severe"
    assert st["values"] == [{"key": "thermal_status", "value": "3"}]
    assert tr.thermal_status("none", 11)["level"] == 3 and tr.thermal_status(None, None)["level"] == 3   # STALE


def test_thermal_is_published_on_change_and_not_every_reading(monkeypatch):
    rec = _Recorder()
    c = _client(monkeypatch, None, ros=rec)
    tr.state.update(thermal_sent=None, thermal_sent_at=0.0, thermal_at=None)
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json(); page.receive_json()
        for word in ("none", "none", "moderate"):
            page.send_json({"kind": "battery", "level": 50, "charging": False, "plugged": None, "thermal": word})
        page.send_json({"ping": 1}); page.receive_json()
        diags = [m["status"][0] for t, m in rec.got if t == "/diagnostics"]
        assert [d["message"] for d in diags][-2:] == ["none", "moderate"] and diags[-1]["level"] == 1
        assert sum(d["message"] == "none" for d in diags) == 1          # the same status twice: once


def test_the_kiosk_app_reports_its_version_and_updater(monkeypatch):
    rec = _Recorder()
    c = _client(monkeypatch, None, ros=rec)
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json(); page.receive_json()
        page.send_json({"kind": "app", "version": "0.3.0", "versionCode": 3, "update": {"state": "current"}})
        page.send_json({"ping": 1}); page.receive_json()
        app = c.get("/api/tablet/sensors").json()["app"]
        assert app == {"version": "0.3.0", "versionCode": 3, "update": {"state": "current"}}
        assert rec.sensors() == []                                  # not a ROS topic


def test_a_demand_that_is_not_renewed_turns_the_camera_off(monkeypatch):
    """FAIL OFF: the Thor's watcher re-sends every 2 s; if it stops, the camera must not stay on."""
    c = _client(monkeypatch, None, ros=_Recorder())
    monkeypatch.setattr(tr, "DEMAND_STALE_S", 0.4)
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json(); page.receive_json()
        c.post("/api/tablet/camera/demand", json={"front": False, "rear": True})
        assert _drain_until(page, lambda m: "camera" in m)["camera"]["rear"]["on"] is True
        m = _drain_until(page, lambda m: "camera" in m)                      # no renewal: off by itself
        assert m["camera"]["rear"]["on"] is False
    assert c.post("/api/tablet/camera/demand", json={"front": "yes"}).status_code == 400
    assert c.post("/api/tablet/camera/demand", content=b"nope").status_code == 400


def test_a_restart_is_refused_during_a_show_and_a_sleep_reaches_the_page_and_the_app(monkeypatch):
    import kiosk_update
    c = _client(monkeypatch, "ws://127.0.0.1:9")
    monkeypatch.setattr(kiosk_update, "show_busy", lambda: "a show is loaded (demo, armed)")
    r = c.post("/api/tablet/command", json={"cmd": "restart"})
    assert r.status_code == 409 and r.json()["code"] == "HEAD_RESTART_SHOW"
    assert c.get("/api/tablet/command").json() == {}                   # nothing for the app to run
    with c.websocket_connect("/ws/tablet") as page:
        page.send_json({"hello": {"kiosk": True}})
        page.receive_json(); page.receive_json()
        r = c.post("/api/tablet/command", json={"cmd": "sleep"})
        assert r.status_code == 200
        assert page.receive_json() == {"command": {"id": r.json()["id"], "cmd": "sleep"}}
    assert c.get("/api/tablet/command").json() == {"id": r.json()["id"], "cmd": "sleep"}
    monkeypatch.setattr(tr, "COMMAND_TTL_S", 0.0)
    assert c.get("/api/tablet/command").json() == {}                   # a tablet back later never acts on it


def test_the_charge_limit_is_not_a_charger_fault_but_a_falling_battery_is():
    tr.power.update(readings=[], command=None)
    tr.note_battery({"level": 80, "charging": False, "plugged": "usb"}, now=0.0)
    assert tr.power_status(now=1.0)["warnings"] == []                   # Battery protection: held at 80 %
    tr.note_battery({"level": 60, "charging": False, "plugged": "usb"}, now=2.0)
    assert [w["code"] for w in tr.power_status(now=3.0)["warnings"]] == ["HEAD_TABLET_NOT_CHARGING"]
    tr.power.update(readings=[])
    for i, level in enumerate((79, 78, 77, 76)):                        # "charging", yet falling: too weak
        tr.note_battery({"level": level, "charging": True, "plugged": "usb"}, now=i * 300.0)
    assert [w["code"] for w in tr.power_status(now=900.0)["warnings"]] == ["HEAD_TABLET_NOT_CHARGING"]
    assert tr.power_status(now=900.0 + tr.BATTERY_STALE_S + 1)["warnings"] == []   # asleep: no word either way
