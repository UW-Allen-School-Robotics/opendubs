"""The chest light shows what the PRISM shows (state_out.py, head protocol CHEST): the PRISM's winning
colour, as the pulse whose hue is nearest on the light's chart, sent to the NECK — and never with no
separate neck."""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import state_out as so


def _us(state, now=0.0):
    line = so.chest_line(so._prism_target(state, now=now))
    return line if line == "CHEST OFF" else int(line.split()[1])


def test_each_state_lands_on_its_own_colour_on_the_chart():
    assert _us("E_STOP_LATCH") == 1130                   # red, held clear of OFF (below 1100)
    assert 1180 <= _us("DEGRADED") <= 1240               # orange
    assert 1480 <= _us("SAFE_IDLE") <= 1540              # green
    assert 1570 <= _us("AUTONOMOUS_CONTROL") <= 1650     # azure
    assert 1740 <= _us("CHARGING") <= 1880               # blue-violet, toward indigo
    assert _us("MANUAL_CONTROL") == _us("INIT") == 2500  # too pale for a hue: white


def test_coloured_pulses_keep_their_margins_from_off_and_white():
    for rgb in ((255, 0, 0), (255, 0, 40), (255, 0, 200), (180, 0, 255), (120, 0, 255)):
        us = int(so.chest_line((rgb, 90)).split()[1])
        assert 1130 <= us <= 1880, (rgb, us)


def test_charging_is_solid_purple_through_every_breath():
    assert len({_us("CHARGING", now=i * 0.25) for i in range(32)}) == 1


def test_brightness_never_picks_another_colour_but_a_blackout_is_off():
    assert so.chest_line(((30, 210, 70), 100)) == so.chest_line(((30, 210, 70), 20))
    assert so.chest_line(((30, 210, 70), 0)) == "CHEST OFF"
    assert so.chest_line(((0, 0, 0), 80)) == "CHEST OFF"
    assert so.chest_line(None) is None


def test_a_shows_light_drives_it_as_it_drives_the_prism(monkeypatch):
    import show_light
    lay = show_light.Layer()
    lay.hold((255, 230, 0), 80, 1.0)                     # a show's yellow
    monkeypatch.setattr(show_light, "layer", lay)
    assert 1270 <= _us("SAFE_IDLE") <= 1330
    assert _us("E_STOP_LATCH") == 1130                   # a reserved state is always its own
    lay.release()
    assert 1480 <= _us("SAFE_IDLE") <= 1540


class _Neck:
    def __init__(self, addr="10.0.0.9"):
        self.host, self.addr = "opendubs-neck.local", addr


def _sends(monkeypatch, neck, face=None):
    sent = []
    monkeypatch.setattr(so, "_neck", neck)
    monkeypatch.setattr(so, "_face", face if face is not None else object())
    monkeypatch.setattr(so, "_neck_write", sent.append)
    for k, v in (("_chest_line", None), ("_chest_at", 0.0), ("_chest_addr", None)):
        monkeypatch.setattr(so, k, v)
    return sent


def test_sent_on_a_change_a_reappearance_and_a_refresh(monkeypatch):
    neck = _Neck()
    sent = _sends(monkeypatch, neck)
    green, red = ((30, 210, 70), 70), ((255, 35, 35), 95)
    so._push_chest(green, 100.0)
    so._push_chest(((30, 210, 70), 40), 100.3)            # a dimmer green: the same pulse, nothing sent
    so._push_chest(red, 100.6)                            # a change
    neck.addr = None
    so._push_chest(red, 101.0)                            # the neck is gone: queued, dropped by the sender
    neck.addr = "10.0.0.9"
    so._push_chest(red, 101.3)                            # ... and it is back: sent again at once
    so._push_chest(red, 101.3 + so._CHEST_REFRESH_S)      # the refresh
    g = so.chest_line(green)
    assert sent == [g, "CHEST 1130", "CHEST 1130", "CHEST 1130", "CHEST 1130"]


def test_nothing_with_no_neck_or_with_the_tab5(monkeypatch):
    sent = _sends(monkeypatch, None)
    so._push_chest(((30, 210, 70), 70), 1.0)
    neck = _Neck()
    sent += _sends(monkeypatch, neck, face=neck)          # the Tab5: one box, no chest light
    so._push_chest(((30, 210, 70), 70), 2.0)
    assert sent == []


def test_the_line_reaches_the_neck_over_udp(monkeypatch):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(3.0)
    monkeypatch.setattr(so, "_TAB5_UDP_PORT", rx.getsockname()[1])
    monkeypatch.setattr(so, "_neck", so._Peer("127.0.0.1"))
    threading.Thread(target=so._neck_tx, daemon=True).start()
    so._neck_write("CHEST 1500")
    try:
        assert rx.recv(64) == b"CHEST 1500\n"
    finally:
        rx.close()
