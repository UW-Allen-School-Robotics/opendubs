"""The tablet face's door on the Thor: a UDP line in, to the face page, and its reply back out."""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import face_door


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _client(monkeypatch, tmp_path=None):
    import tempfile
    monkeypatch.setenv("OPENDUBS_HEAD_SETTINGS", os.path.join(tempfile.mkdtemp(), "head.json"))
    import head_settings
    monkeypatch.setattr(head_settings, "PATH", os.environ["OPENDUBS_HEAD_SETTINGS"])
    monkeypatch.setattr(face_door, "PORT", _free_udp_port())
    monkeypatch.setattr(face_door, "_transport", None)
    face_door.pages.clear()
    app = FastAPI()
    app.include_router(face_door.router)
    app.router.on_startup.append(face_door.start)
    return TestClient(app)


def _ask(line: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(line.encode(), ("127.0.0.1", face_door.PORT))
    try:
        return s.recv(512).decode()
    finally:
        s.close()


def _stop():
    if face_door._transport is not None:
        face_door._transport.close()
        face_door._transport = None


def test_with_no_page_ping_says_the_face_is_absent(monkeypatch):
    with _client(monkeypatch):
        try:
            assert "kind=tablet-face" in _ask("PING") and "face=absent" in _ask("PING")
            assert _ask("EMOTE SOFT") == "ERR face not showing"
        finally:
            _stop()


def test_a_line_reaches_the_page_and_its_reply_comes_back(monkeypatch):
    import threading
    with _client(monkeypatch) as c, c.websocket_connect("/ws/face") as page:
        try:
            got = {}
            t = threading.Thread(target=lambda: got.update(reply=_ask("SKIN cookie")))
            t.start()
            assert "settings" in page.receive_json()      # first, the head's idle settings
            msg = page.receive_json()
            assert msg["line"] == "SKIN cookie"
            page.send_json({"id": msg["id"], "reply": "OK skin cookie"})
            t.join(3)
            assert got["reply"] == "OK skin cookie"
        finally:
            _stop()


def test_a_page_that_does_not_answer_times_out(monkeypatch):
    with _client(monkeypatch) as c, c.websocket_connect("/ws/face") as page:
        try:
            import threading
            got = {}
            t = threading.Thread(target=lambda: got.update(reply=_ask("MOVE wink_l")))
            t.start()
            page.receive_json()                    # the settings
            page.receive_json()                    # heard, never answered
            t.join(3)
            assert got["reply"] == "ERR face did not answer"
        finally:
            _stop()


def test_the_newest_page_is_the_one_asked(monkeypatch):
    import threading
    with _client(monkeypatch) as c, c.websocket_connect("/ws/face"), \
            c.websocket_connect("/ws/face") as newer:
        try:
            got = {}
            t = threading.Thread(target=lambda: got.update(reply=_ask("EMOTE ALERT")))
            t.start()
            newer.receive_json()                   # the settings
            msg = newer.receive_json()
            newer.send_json({"id": msg["id"], "reply": "OK emote ALERT"})
            t.join(3)
            assert got["reply"] == "OK emote ALERT"
        finally:
            _stop()


def test_another_sites_page_cannot_be_the_face(monkeypatch):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with _client(monkeypatch) as c:
        try:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect("/ws/face", headers={"Origin": "http://evil.example",
                                                             "Host": "10.42.2.10:8080"}):
                    pass
            assert not face_door.pages
        finally:
            _stop()


def test_the_door_can_be_kept_closed(monkeypatch):
    monkeypatch.setenv("OPENDUBS_FACE_DOOR", "0")
    with _client(monkeypatch):
        assert face_door._transport is None
