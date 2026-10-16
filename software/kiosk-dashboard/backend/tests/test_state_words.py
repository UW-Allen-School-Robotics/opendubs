"""The robot's state in plain words on the face (state_words.py, state_out.py, head protocol CAPTION)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robot_state
import state_out as so
import state_words as sw


def _rs(**kw):
    base = dict(booting=False, estop_sources=[], authority="OFF", deadman=False, link=True, faults=[])
    base.update(kw)
    return robot_state.derive(**base)


def _say(mode="always", soc=None, **kw):
    rsd = _rs(**kw)
    return sw.caption(so.present(rsd), rsd, soc, mode)


def test_every_state_has_its_words():
    assert _say() == "Ready"
    assert _say(booting=True) == "Starting up"
    assert _say(authority="RC_MANUAL", deadman=True) == "Manual control"
    assert _say(authority="AUTONOMOUS") == "Running a show"
    assert _say(estop_sources=["BUTTON"]) == "Emergency stop"
    assert _say(charging=True, soc=94.6) == "Charging 95%"
    assert _say(charging=True, soc=None) == "Charging"                 # no BMS reading: no number


def test_a_problem_says_what_is_wrong_in_plain_words():
    assert _say(authority=None, link=False) == "Problem: RC transmitter not connected"
    assert _say(faults=["lift module not responding"]) == "Problem: lift not responding"
    assert _say(faults=["OVERTEMP"]) == "Problem: running too hot"
    assert _say(faults=["PDH brownout", "OVERTEMP"]) == "Problem: battery voltage dropped, running too hot"
    assert _say(faults=["SOMETHING NEW"]) == "Problem: needs a check"  # never a maintainer's raw name
    assert _say(faults=["OVERTEMP", "OVERTEMP"]) == "Problem: running too hot"


def test_never_longer_than_the_protocol_allows():
    every = list(sw.FAULTS)
    text = _say(faults=every)
    assert text.startswith("Problem: ") and len(text) <= sw.MAX_LEN
    for f in every:
        assert len(_say(faults=[f])) <= sw.MAX_LEN


def test_by_default_not_while_driven_or_running_but_a_problem_is_always_said():
    d = "hide_auto_manual"
    assert sw.DEFAULT_MODE == d
    assert _say(d, authority="RC_MANUAL", deadman=True) is None
    assert _say(d, authority="AUTONOMOUS") is None
    assert _say(d) == "Ready"
    assert _say(d, estop_sources=["BUTTON"]) == "Emergency stop"
    assert _say(d, authority=None, link=False).startswith("Problem: ")
    assert _say(d, charging=True, soc=50) == "Charging 50%"


def test_off_says_nothing_at_all():
    for kw in ({}, {"estop_sources": ["BUTTON"]}, {"faults": ["OVERTEMP"]}, {"booting": True}):
        assert _say("off", **kw) is None
    assert sw.line(None) == "CAPTION OFF"
    assert sw.line("Ready") == "CAPTION Ready"
    assert sw.caption("SAFE_IDLE", {}, None, "nonsense") == "Ready"     # a bad mode is the default


def test_the_setting_is_checked():
    import head_settings
    assert head_settings.DEFAULTS["state_words"] == "hide_auto_manual"
    for m in sw.MODES:
        assert head_settings._check("state_words", m) is None
    assert head_settings._check("state_words", "sometimes")
    assert head_settings._check("state_words", True)


class _Status:
    pack = {"present": True, "soc": 80}

    @classmethod
    def _pack(cls):
        return cls.pack


def test_sent_with_every_state_line_and_at_once_on_a_change(monkeypatch):
    import head_settings
    sent, settings = [], dict(head_settings.DEFAULTS)
    monkeypatch.setattr(so, "_tab5_write", sent.append)
    monkeypatch.setattr(so, "_caption_line", None)
    monkeypatch.setattr(head_settings, "load", lambda: settings)
    idle = _rs()
    so._push_caption("SAFE_IDLE", idle, _Status, True)
    so._push_caption("SAFE_IDLE", idle, _Status, False)                # nothing changed: nothing sent
    so._push_caption("SAFE_IDLE", idle, _Status, True)                 # the refresh, with STATE
    settings["state_words"] = "off"                                    # the setting, live
    so._push_caption("SAFE_IDLE", idle, _Status, False)
    settings["state_words"] = "always"
    lost = _rs(authority=None, link=False)
    so._push_caption("DEGRADED", lost, _Status, True)
    assert sent == ["CAPTION Ready", "CAPTION Ready", "CAPTION OFF", "CAPTION Problem: RC transmitter not connected"]
    assert so.face_caption() == "CAPTION Problem: RC transmitter not connected"


def test_a_face_page_gets_the_words_as_it_connects(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import face_door
    import head_settings
    monkeypatch.setattr(head_settings, "load", lambda: dict(head_settings.DEFAULTS))
    monkeypatch.setattr(so, "_face_state", "SAFE_IDLE")
    monkeypatch.setattr(so, "_batt_line", None)
    monkeypatch.setattr(so, "_caption_line", "CAPTION Ready")
    face_door.pages.clear()
    app = FastAPI()
    app.include_router(face_door.router)
    with TestClient(app) as c, c.websocket_connect("/ws/face") as page:
        assert "settings" in page.receive_json()
        assert page.receive_json()["line"] == "STATE SAFE_IDLE"
        assert page.receive_json()["line"] == "CAPTION Ready"
