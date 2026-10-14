"""AirPlay's relay: shairport's FIFOs on the Thor → the face page, surviving the receiver's restarts."""
import asyncio
import base64
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import airplay_relay as ar


def _writer(path, data, hold_s=0.0):
    """Open the FIFO for writing (as the receiver does), write, keep it open a moment, close."""
    def run():
        with open(path, "wb", buffering=0) as fh:
            fh.write(data)
            time.sleep(hold_s)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_the_reader_survives_the_receiver_stopping_and_starting(tmp_path):
    """The receiver's unit removes its FIFO directory on stop and makes a NEW FIFO on start."""
    d = tmp_path / "airplay"
    path = str(d / "audio.pcm")
    stop = threading.Event()
    got = []

    def read():
        for chunk in ar.fifo_chunks(path, 4096, stop=stop.is_set):
            if chunk:
                got.append(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    time.sleep(0.3)                                     # receiver off: nothing there, no hang
    d.mkdir(); os.mkfifo(path)
    _writer(path, b"A" * 1000, 0.2).join()
    time.sleep(1.2)
    os.unlink(path); os.rmdir(d)                        # the unit stops
    time.sleep(0.5)
    d.mkdir(); os.mkfifo(path)                          # ... and starts again: a new FIFO
    time.sleep(1.5)                                     # the reader finds it
    _writer(path, b"B" * 1000, 0.2).join()
    time.sleep(1.2)
    stop.set()
    data = b"".join(got)
    assert data.count(b"A") == 1000 and data.count(b"B") == 1000, (data.count(b"A"), data.count(b"B"))


def _item(t, c, value=None):
    x = f"<item><type>{t.encode().hex()}</type><code>{c.encode().hex()}</code><length>{len(value or b'')}</length>"
    if value is not None:
        x += f"\n<data encoding=\"base64\">\n{base64.b64encode(value).decode()}</data>"
    return (x + "</item>\n").encode()


def test_what_is_playing_comes_from_the_metadata(monkeypatch):
    monkeypatch.setattr(ar, "state", {"active": True, "title": None, "artist": None, "last_audio": 0, "bytes": 0})
    stream = _item("ssnc", "pbeg") + _item("core", "minm", "Clair de Lune".encode()) + \
        _item("core", "asar", "Debussy".encode()) + b"<item><type>636f7265</type>"     # a half item
    items, rest = ar.parse_items(stream)
    assert [(t, c) for t, c, _ in items] == [("ssnc", "pbeg"), ("core", "minm"), ("core", "asar")]
    assert rest.strip().startswith(b"<item>")            # the half item is kept for the next read
    assert any([ar.apply_item(*it) for it in items])
    assert (ar.state["title"], ar.state["artist"]) == ("Clair de Lune", "Debussy")
    ar.apply_item("ssnc", "pend", b"")
    assert ar.state["title"] is None


def test_a_session_streams_to_the_page_and_is_reported_to_the_hub(tmp_path, monkeypatch):
    import show_audio
    reports = []

    async def report(active, title, artist):
        reports.append(active)
    monkeypatch.setattr(show_audio.hub, "airplay_report", report, raising=False)
    d = tmp_path / "airplay"; d.mkdir()
    path = str(d / "audio.pcm"); os.mkfifo(path)
    monkeypatch.setattr(ar, "AUDIO", path)
    monkeypatch.setattr(ar, "META", str(d / "meta"))
    monkeypatch.setattr(ar, "IDLE_S", 0.5)
    monkeypatch.setattr(ar, "_started", False)
    ar.clients.clear()
    ar.state.update(active=False, title=None, artist=None)
    app = FastAPI()
    app.include_router(ar.router)
    app.router.on_startup.append(ar.start)
    with TestClient(app) as c, c.websocket_connect("/ws/airplay") as page:
        assert json.loads(page.receive_text()) == ar.FORMAT
        assert json.loads(page.receive_text())["now"]["active"] is False      # what is playing: nothing
        time.sleep(1.2)                                   # the reader has the (empty) FIFO open
        pcm = bytes(range(256)) * 64                      # 16 KB
        _writer(path, pcm, 0.1)
        got, nows = b"", []
        while len(got) < len(pcm):
            m = page.receive()
            if m.get("bytes") is not None:
                got += m["bytes"]                         # audio
            elif m.get("text"):
                nows.append(json.loads(m["text"])["now"]) # what is playing, interleaved
        assert got == pcm
        assert nows and nows[0]["active"] is True
        time.sleep(1.5)                                   # silence: the session ends
    assert reports[:2] == [True, False], reports
