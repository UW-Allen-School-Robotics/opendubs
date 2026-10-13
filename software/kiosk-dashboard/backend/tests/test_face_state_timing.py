"""The face's STATE never waits on the neck, and a face page that connects is told the state at once.

At the robot on 2026-10-13 the face went grey mid-show: the neck's name did not resolve, each lookup
took 5 s, and the lookup shared the thread that sends the face its STATE.
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import face_door
import head_settings
import state_out


def test_a_slow_neck_does_not_delay_the_face(monkeypatch):
    face = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    face.bind(("127.0.0.1", 0))
    face.settimeout(3)
    real = socket.gethostbyname

    def lookup(host):
        if host == "absent-neck.local":
            time.sleep(5)                          # what an absent head's mDNS lookup costs
            raise OSError("not found")
        return real(host)

    monkeypatch.setattr(socket, "gethostbyname", lookup)
    monkeypatch.setattr(state_out, "_TAB5_UDP_PORT", face.getsockname()[1])
    monkeypatch.setattr(state_out, "_face", state_out._Peer("127.0.0.1"))
    monkeypatch.setattr(state_out, "_neck", state_out._Peer("absent-neck.local"))
    monkeypatch.setattr(state_out, "_peers_last_intro", -1e9)
    monkeypatch.setattr(state_out, "_tab5_serial", lambda line: None)

    # Only the FACE's thread: it must never touch the neck. (The neck's own thread is tested below.)
    threading.Thread(target=state_out._tab5_sender, daemon=True).start()
    # Past the sender's 1 s queue wait, so that if the neck shared its thread (as it did) the thread
    # would be inside the 5 s lookup when the line is written.
    time.sleep(1.5)
    t0 = time.monotonic()
    state_out._tab5_write("STATE SAFE_IDLE")
    data = face.recv(64)
    assert data.startswith(b"STATE SAFE_IDLE")
    assert time.monotonic() - t0 < 1.0             # not after the neck's 5 s


def test_a_face_page_is_told_the_state_as_it_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "head.json"))
    monkeypatch.setattr(state_out, "_face_state", "AUTONOMOUS")
    face_door.pages.clear()
    app = FastAPI()
    app.include_router(face_door.router)
    with TestClient(app) as c, c.websocket_connect("/ws/face") as page:
        assert "settings" in page.receive_json()
        msg = page.receive_json()
        assert msg["line"] == "STATE AUTONOMOUS" and msg["id"] == 0    # id 0: no one awaits the reply


def test_a_neck_that_appears_later_is_found_and_introduced(monkeypatch, capsys):
    """The M5Dial neck may be switched on after the dashboard starts: it is said to be absent ONCE,
    then found, and the StamPLC is told where it is (TAB5IP, the StamPLC's own word)."""
    import types
    sent = []
    monkeypatch.setitem(sys.modules, "stamplc", types.SimpleNamespace(reader=types.SimpleNamespace(send=sent.append)))
    up = {"now": False}

    def lookup(host):
        if host == "opendubs-neck.local" and up["now"]:
            return "10.42.2.31"
        raise OSError("not found")

    monkeypatch.setattr(socket, "gethostbyname", lookup)
    monkeypatch.setattr(state_out, "_neck", state_out._Peer("opendubs-neck.local", retry_s=0.05))
    monkeypatch.setattr(state_out, "_neck_seen", None)
    for _ in range(3):                                # absent, three tries
        monkeypatch.setattr(state_out, "_peers_last_intro", -1e9)
        state_out._introduce_peers()
        time.sleep(0.06)
    up["now"] = True
    monkeypatch.setattr(state_out, "_peers_last_intro", -1e9)
    state_out._introduce_peers()
    err = capsys.readouterr().err
    assert err.count("not found") == 1, err           # said once, not on every retry
    assert "NECK: opendubs-neck.local at 10.42.2.31" in err
    assert sent == ["TAB5IP 10.42.2.31"]
