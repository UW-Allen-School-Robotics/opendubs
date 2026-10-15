"""A charging robot is SHOWN as charging (state_out.py), without touching the FSM: the head gets STATE
CHARGING, the PRISM breathes purple, and the face gets the pack's charge as BATT."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robot_state
import state_out as so


def _rs(**kw):
    base = dict(booting=False, estop_sources=[], authority="OFF", deadman=False, link=True, faults=[])
    base.update(kw)
    return robot_state.derive(**base)


def test_charging_is_shown_only_where_the_robot_would_show_safe_idle():
    assert so.present(_rs(charging=True)) == "CHARGING"
    assert so.present(_rs(charging=False)) == "SAFE_IDLE"
    assert so.present(_rs(charging=True, estop_sources=["BUTTON"])) == "E_STOP_LATCH"     # e-stop wins
    assert so.present(_rs(charging=True, faults=["mast"])) == "DEGRADED"                   # a fault wins
    assert so.present(_rs(charging=True, booting=True)) == "INIT"
    assert so.present({}) is None


def test_the_fsm_itself_is_untouched():
    r = _rs(charging=True)
    assert r["state"] == "SAFE_IDLE" and r["reason"] == "charging — drive inhibited"
    assert so._MAP["CHARGING"][0] == "CHARGING"                                            # the face's word


def test_the_prism_breathes_purple_in_slow_steps():
    (rgb, _), seen = so._prism_target("CHARGING", now=0.0), set()
    assert rgb == (150, 50, 255)
    writes, last = 0, None
    for i in range(400):                                    # 0.3 s passes over two breaths (8 s) ...
        c = so._prism_target("CHARGING", now=i * 0.02)      # ... sampled every 20 ms here
        seen.add(c[1])
        if c != last:
            writes, last = writes + 1, c
    assert min(seen) >= round(70 * so.BREATH_FLOOR) and max(seen) == 70                    # never dark
    assert len(seen) >= 4                                                                  # it does breathe
    assert writes <= 8 / so.BREATH_PERIOD_S * 8 + 1                                        # at most 2 writes/s
    assert so._prism_target("SAFE_IDLE", now=1.3) == ((30, 210, 70), 70)                   # others stay still


def test_a_shows_light_still_wins_over_charging(monkeypatch):
    import show_light

    class Layer:
        def current(self):
            return ((10, 20, 30), 50, 0.0)

        def clock(self):
            return 1.0
    monkeypatch.setattr(show_light, "layer", Layer())
    assert so._prism_target("CHARGING", now=0.0) == ((10, 20, 30), 50)


def test_batt_says_the_charge_and_whether_current_flows_in():
    assert so.batt_line({"present": True, "soc": 73.6}, {"charging": True}) == "BATT 74 1"
    assert so.batt_line({"present": True, "soc": 100}, {"charging": False}) == "BATT 100 0"   # full: not charging
    assert so.batt_line({"present": False, "soc": 50}, {"charging": None}) == "BATT - 0"      # no BMS: unknown
    assert so.batt_line({"present": True, "soc": "x"}, {}) == "BATT - 0"


def test_batt_is_sent_on_a_change_and_as_a_refresh(monkeypatch):
    sent = []
    monkeypatch.setattr(so, "_tab5_write", sent.append)
    monkeypatch.setattr(so, "_batt_line", None)
    monkeypatch.setattr(so, "_batt_at", 0.0)
    st = {"pack": {"present": True, "soc": 50}, "charger": {"charging": True}}

    class Status:
        @staticmethod
        def _pack():
            return st["pack"]

        @staticmethod
        def _charger():
            return st["charger"]
    so._push_batt(Status, 100.0)
    so._push_batt(Status, 101.0)                       # nothing changed: nothing sent
    st["pack"] = {"present": True, "soc": 51}
    so._push_batt(Status, 102.0)                       # a whole percent: sent
    so._push_batt(Status, 102.0 + so._BATT_REFRESH_S)  # the refresh
    assert sent == ["BATT 50 1", "BATT 51 1", "BATT 51 1"]
    assert so.face_batt() == "BATT 51 1"


def test_a_face_page_gets_the_charge_as_it_connects(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import face_door
    import head_settings
    monkeypatch.setattr(head_settings, "load", lambda: dict(head_settings.DEFAULTS))
    monkeypatch.setattr(so, "_face_state", "CHARGING")
    monkeypatch.setattr(so, "_batt_line", "BATT 64 1")
    face_door.pages.clear()
    app = FastAPI()
    app.include_router(face_door.router)
    with TestClient(app) as c, c.websocket_connect("/ws/face") as page:
        assert "settings" in page.receive_json()
        assert page.receive_json()["line"] == "STATE CHARGING"
        assert page.receive_json()["line"] == "BATT 64 1"
