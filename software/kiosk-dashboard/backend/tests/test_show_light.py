"""The show light layer (show_light.py) and its use in state_out.py; the operator lift count
(choreo_proxy.py). No robot: a fake clock and the in-process test client."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import show_light

STATE_AUTO = ((0, 150, 255), 80)
STATE_ESTOP = ((255, 35, 35), 95)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _layer():
    c = Clock()
    return show_light.Layer(clock=c), c


def test_no_lease_shows_the_state_colour():
    lay, _ = _layer()
    assert show_light.winning("MANUAL_CONTROL", ((235, 240, 248), 80), lay=lay) == ((235, 240, 248), 80)


def test_a_fresh_lease_shows_the_show_colour_and_lapses_by_itself():
    lay, c = _layer()
    lay.hold([0, 200, 80], 90, 0.6)
    assert show_light.winning("MANUAL_CONTROL", STATE_AUTO, lay=lay, pulse=False) == ((0, 200, 80), 90)
    c.t += 0.7                                       # the runner stopped renewing
    assert show_light.winning("MANUAL_CONTROL", STATE_AUTO, lay=lay, pulse=False) == STATE_AUTO


def test_a_reserved_state_always_wins_over_a_lease():
    lay, _ = _layer()
    lay.hold([0, 200, 80], 90, 1.0)
    for rs in show_light.RESERVED:
        assert show_light.winning(rs, STATE_ESTOP, lay=lay, pulse=False) == STATE_ESTOP


def test_release_returns_the_state_colour_at_once():
    lay, _ = _layer()
    lay.hold([0, 200, 80], 90, 1.0)
    lay.release()
    assert show_light.winning("AUTONOMOUS_CONTROL", STATE_AUTO, lay=lay, pulse=False) == STATE_AUTO


def test_the_auto_pulse_flashes_the_auto_colour_during_a_lease_only_in_auto():
    lay, c = _layer()
    lay.hold([0, 200, 80], 90, 2.0)
    show = ((0, 200, 80), 90)
    assert show_light.winning("AUTONOMOUS_CONTROL", STATE_AUTO, lay=lay, pulse=True) == STATE_AUTO   # t=0: on
    c.t += 0.5
    lay.hold([0, 200, 80], 90, 2.0)                  # renewed: the pulse keeps its phase
    assert show_light.winning("AUTONOMOUS_CONTROL", STATE_AUTO, lay=lay, pulse=True) == show
    c.t += 2.6                                       # 3.1 s into the lease: the next pulse
    lay.hold([0, 200, 80], 90, 2.0)
    assert show_light.winning("AUTONOMOUS_CONTROL", STATE_AUTO, lay=lay, pulse=True) == STATE_AUTO
    assert show_light.winning("AUTONOMOUS_CONTROL", STATE_AUTO, lay=lay, pulse=False) == show
    assert show_light.winning("MANUAL_CONTROL", STATE_AUTO, lay=lay, pulse=True) == show   # AUTO only


def test_every_pulse_is_seen_by_a_0_3_s_loop():
    assert show_light.AUTO_PULSE_ON_S > 0.3


def test_the_auto_pulse_switch_defaults_on(monkeypatch):
    monkeypatch.delenv("OPENDUBS_SHOW_LIGHT_AUTO_PULSE", raising=False)
    assert show_light.auto_pulse_on()
    monkeypatch.setenv("OPENDUBS_SHOW_LIGHT_AUTO_PULSE", "0")
    assert not show_light.auto_pulse_on()


def test_a_lease_is_held_to_its_maximum():
    lay, c = _layer()
    lay.hold([0, 200, 80], 90, 60.0)
    c.t += show_light.LEASE_MAX_S + 0.1
    assert lay.current() is None


def _client(local=True, monkeypatch=None):
    show_light.layer = show_light.Layer()
    if monkeypatch is not None:
        monkeypatch.setattr(show_light, "_local", lambda request: local)
    app = FastAPI()
    app.include_router(show_light.router)
    return TestClient(app)


def test_only_this_machine_may_set_the_light():
    c = _client()                                    # the test client is not a loopback address
    r = c.post("/api/choreo/light", json={"rgb": [0, 200, 80], "br": 90, "lease_ms": 500})
    assert r.status_code == 403 and show_light.layer.current() is None


def test_the_runner_holds_and_releases(monkeypatch):
    c = _client(monkeypatch=monkeypatch)
    assert c.post("/api/choreo/light", json={"rgb": [0, 200, 80], "br": 90, "lease_ms": 500}).json()["ok"]
    assert show_light.layer.current()[:2] == ((0, 200, 80), 90)
    assert c.post("/api/choreo/light", json={"release": True}).json()["released"]
    assert show_light.layer.current() is None


def test_bad_values_are_refused(monkeypatch):
    c = _client(monkeypatch=monkeypatch)
    for body in ({"rgb": [0, 300, 0]}, {"rgb": [0, 0]}, {"rgb": [0, 0, 0], "br": 101},
                 {"rgb": [0, 0, 0], "lease_ms": 0}):
        assert c.post("/api/choreo/light", json=body).status_code == 400


def test_state_out_pushes_the_winning_colour():
    import state_out
    show_light.layer = show_light.Layer()
    assert state_out._prism_target("SAFE_IDLE") == ((30, 210, 70), 70)
    show_light.layer.hold([0, 200, 80], 90, 1.0)
    assert state_out._prism_target("SAFE_IDLE") == ((0, 200, 80), 90)
    assert state_out._prism_target("E_STOP_LATCH") == ((255, 35, 35), 95)
    show_light.layer.release()


def test_an_operator_lift_command_is_counted_and_reported():
    import choreo_proxy
    before = choreo_proxy.operator_mast_seq()
    choreo_proxy.note_operator_mast()
    assert choreo_proxy.operator_mast_seq() == before + 1
    v = choreo_proxy.robot_view({"inputs": {}}, {"fresh": False}, {"fresh": False})
    assert v["mast_operator_seq"] == before + 1
