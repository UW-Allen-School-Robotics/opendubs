"""The head follows the person the face sees (neck_follow.py): only under the rules, within limits."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import neck_follow as nf

CFG = {"pan_sign": -1.0, "tilt_sign": -1.0, "rate": 10.0,
       "pan_range": 30.0, "tilt_range": 18.0}
STILL = {"pan": 90.0, "tilt": 90.0, "moving": False, "owner": "none"}


def test_the_config_is_robot_env_with_safe_bounds(monkeypatch):
    assert nf.config() == {"pan_sign": -1.0, "tilt_sign": -1.0, "rate": 10.0, "pan_range": 30.0, "tilt_range": 18.0}
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_PAN_SIGN", "1")
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_RATE_HZ", "70")           # out of range: clamped
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_PAN_RANGE", "80")         # never past the Tab5's envelope
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_TILT_RANGE", "tall")      # nonsense: the default
    c = nf.config()
    assert c["pan_sign"] == 1.0 and c["rate"] == 10.0 and c["pan_range"] == 30.0 and c["tilt_range"] == 18.0


def test_the_necks_status_line_is_read():
    n = nf.parse_neck("OK neck pan=101.5 tilt=88.0 target=101.5,88.0 moving=1 owner=gaze")
    assert n == {"pan": 101.5, "tilt": 88.0, "moving": True, "owner": "gaze"}
    assert nf.parse_neck("ERR neck no pan/tilt fitted") is None and nf.parse_neck("") is None


def test_the_settings_and_the_neck_gate_it(tmp_path, monkeypatch):
    import head
    import head_settings
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "s.json"))
    head_settings.save({**head_settings.DEFAULTS, "tracking": "off"})
    assert nf.why_not_now() == "tracking is not set to the tablet"
    head_settings.save({**head_settings.DEFAULTS, "tracking": "tablet"})
    monkeypatch.setattr(head, "neck_host", lambda env=None: None)
    assert nf.why_not_now() == "no neck is fitted"
    monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
    monkeypatch.setattr(nf, "robot_state", lambda: "SAFE_IDLE")
    import kiosk_update
    monkeypatch.setattr(kiosk_update, "show_busy", lambda: "the show runner's state is unknown (500)")
    assert "unknown" in nf.why_not_now()                                # unknown counts as busy
    monkeypatch.setattr(kiosk_update, "show_busy", lambda: None)
    assert nf.why_not_now() is None


def test_the_door_takes_targets_from_the_newest_page_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import face_door
    import head_settings
    monkeypatch.setattr(head_settings, "load", lambda: dict(head_settings.DEFAULTS))
    face_door.pages.clear()
    nf.note_target(None)
    app = FastAPI()
    app.include_router(face_door.router)
    c = TestClient(app)
    with c.websocket_connect("/ws/face") as old, c.websocket_connect("/ws/face") as new:
        old.receive_json(); new.receive_json()
        new.send_json({"track": {"ix": 0.8, "iy": 0.4, "conf": 0.7}})
        old.send_json({"track": {"ix": 0.1, "iy": 0.5, "conf": 0.9}})     # last, but not the newest page
        for _ in range(50):
            if nf.state["target"]:
                break
            time.sleep(0.02)
        time.sleep(0.1)
        assert nf.state["target"] == (0.8, 0.4, 0.7)


def test_the_trackers_report_is_kept_cleanly_and_served():
    nf.note_status({"state": "running", "hz": 10.4, "found_pct": 3, "frame": [640, 480], "last_found_s": 2.5,
                    "junk": "x" * 999, "hz2": True})
    st = nf.status()["tracker"]
    assert st["state"] == "running" and st["hz"] == 10.4 and st["frame"] == [640, 480] and st["last_found_s"] == 2.5
    assert "junk" not in st and st["age_s"] < 1
    nf.note_status({"state": "failed: camera: NotAllowedError", "frame": ["a", 1], "hz": True})
    st = nf.status()["tracker"]
    assert st["state"].startswith("failed: camera") and "frame" not in st and "hz" not in st
    nf.note_status("nonsense")                                            # ignored
    assert nf.status()["tracker"]["state"].startswith("failed")


def test_the_door_takes_the_report_from_the_newest_page_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import face_door
    import head_settings
    monkeypatch.setattr(head_settings, "load", lambda: dict(head_settings.DEFAULTS))
    face_door.pages.clear()
    nf.state["tracker"] = None
    app = FastAPI()
    app.include_router(face_door.router)
    with TestClient(app) as c, c.websocket_connect("/ws/face") as old, c.websocket_connect("/ws/face") as new:
        old.receive_json(); new.receive_json()
        new.send_json({"track_status": {"state": "running", "hz": 9}})
        old.send_json({"track_status": {"state": "off"}})                 # last, but not the newest page
        for _ in range(50):
            if nf.state["tracker"]:
                break
            time.sleep(0.02)
        time.sleep(0.1)
        assert nf.state["tracker"] == {"state": "running", "hz": 9}


def test_the_targets_kind_and_the_trackers_winner_are_kept():
    nf.note_target({"ix": 0.7, "iy": 0.4, "conf": 0.65, "kind": "person"})
    assert nf.status()["kind"] == "person"
    nf.note_target({"ix": 0.7, "iy": 0.4, "conf": 0.65, "kind": "laser"})        # unknown: a face, as before
    assert nf.status()["kind"] == "face"
    nf.note_status({"state": "running", "kind": "motion", "confidence": 0.7, "cues": {"motion": 3, "person": 1, "x": 9},
                    "person_model": "failed to load"})
    t = nf.status()["tracker"]
    assert t["kind"] == "motion" and t["confidence"] == 0.7 and t["cues"] == {"motion": 3, "person": 1}
    assert t["person_model"] == "failed to load"


def test_the_head_never_follows_in_a_reserved_state_and_resumes_by_itself(tmp_path, monkeypatch):
    import head
    import head_settings
    import kiosk_update
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "s.json"))
    head_settings.save({**head_settings.DEFAULTS, "tracking": "tablet"})
    monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
    monkeypatch.setattr(kiosk_update, "show_busy", lambda: None)
    rs = {"now": "DEGRADED"}
    monkeypatch.setattr(nf, "robot_state", lambda: rs["now"])
    for reserved in ("DEGRADED", "E_STOP_LATCH"):
        rs["now"] = reserved
        assert f"the robot is {reserved}" in nf.why_not_now()
    rs["now"] = None
    assert "unknown" in nf.why_not_now()                               # unreadable counts as reserved

    def broken():
        raise RuntimeError("no StamPLC")
    monkeypatch.setattr(nf, "robot_state", broken)
    assert "unknown" in nf.why_not_now()
    for fine in ("SAFE_IDLE", "MANUAL_CONTROL", "AUTONOMOUS_CONTROL", "INIT"):
        rs["now"] = fine
        monkeypatch.setattr(nf, "robot_state", lambda: rs["now"])
        assert nf.why_not_now() is None                                 # not latched: it resumes


# ---- the Tab5's law -----------------------------------------------------------------------------------

def test_the_head_angle_is_the_tab5s_law_of_where_the_person_appears():
    (pan, tilt), why = nf.aim((0.9, 0.5, 0.9), CFG)                        # right of centre: the robot's right
    assert why is None and pan == 90 - 0.8 * 30 and tilt == 90.0
    (pan, tilt), _ = nf.aim((0.2, 0.95, 0.9), CFG)                         # left, and low
    assert pan == 90 + 0.6 * 30 and tilt == round(90 - 0.9 * 18, 1)
    (pan, tilt), _ = nf.aim((0.0, 0.0, 1.0), CFG)                          # a corner: the envelope's corner
    assert (pan, tilt) == (120.0, 108.0)


def test_a_person_standing_still_counts_from_045():
    """#329: a person standing in front scored ~0.5 and the head never turned at 0.6."""
    assert nf.aim((0.9, 0.5, 0.5), CFG, "person")[1] is None
    assert nf.aim((0.9, 0.5, 0.5), CFG, "motion")[0] == (90.0, 90.0)


def _sends(monkeypatch, why=None, neck="OK neck pan=90.0 tilt=90.0 target=90.0,90.0 moving=0 owner=none"):
    sent = []

    def ask(host, line, timeout=0.3):
        sent.append(line)
        return neck if line == "NECK" else "OK gaze"
    monkeypatch.setattr(nf, "_ask", ask)
    monkeypatch.setattr(nf, "why_not_now", lambda: why)
    import head
    monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
    nf.state.update(limit_since=None)
    return sent


def test_one_step_sends_a_leased_gaze(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG) is None
    assert sent == ["NECK", "GAZE 66.0 90.0 600"]


def test_no_one_seen_sends_it_home_but_not_allowed_sends_nothing(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target(None)
    assert nf.step(CFG).startswith("nobody seen") and sent[-1] == "GAZE 90.0 90.0 600"
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    nf.state["target_at"] = time.monotonic() - 5                          # stale is nobody
    assert nf.step(CFG).startswith("nobody seen") and sent[-1] == "GAZE 90.0 90.0 600"
    sent = _sends(monkeypatch, why="a show is loaded (wave, RUNNING)")
    assert nf.step(CFG) == "a show is loaded (wave, RUNNING)" and sent == []
    sent = _sends(monkeypatch, neck="OK neck pan=90.0 tilt=90.0 target=0,0 moving=0 owner=rc")
    assert "held" in nf.step(CFG) and sent == ["NECK"]                    # a person on the stick has it
    nf.note_target({"ix": 9, "iy": 0.5, "conf": 0.9})                     # nonsense is ignored
    assert nf.state["target"][0] == 0.9


def test_a_target_out_of_reach_is_held_not_chased(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9})                   # at the frame's edge: the envelope's
    assert nf.step(CFG) is None
    nf.state["limit_since"] = time.monotonic() - 3
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG).startswith("holding")
    assert sum(1 for s in sent if s.startswith("GAZE")) == 1


# ---- a regression that happened, 2026-10-16 ------------------------------------------------------

def test_a_held_sighting_repeats_one_target_and_never_accumulates(monkeypatch):
    """The robot, 2026-10-16: one sighting (x 0.27) repeated for ~2 s ran the old code 100 -> 112 -> 120."""
    sent = _sends(monkeypatch, neck="OK neck pan=100.0 tilt=90.0 target=0,0 moving=0 owner=none")
    for _ in range(20):
        nf.note_target({"ix": 0.27, "iy": 0.5, "conf": 0.68, "kind": "face"})
        nf.step(CFG)
    gazes = {s for s in sent if s.startswith("GAZE")}
    assert gazes == {"GAZE 103.8 90.0 600"}, gazes


def test_an_absent_neck_is_not_looked_up_every_update_but_is_found_again(monkeypatch):
    """Offline readiness: an absent neck's mDNS lookup takes ~5 s; at 10 Hz it starved the loop. And a
    neck that comes back (or moves address) must be found again without a restart."""
    calls, answer, clock = [], {"addr": None}, {"t": 100.0}

    def lookup(host):
        calls.append(host)
        if answer["addr"] is None:
            raise OSError("not found")
        return answer["addr"]
    monkeypatch.setattr(nf.socket, "gethostbyname", lookup)
    monkeypatch.setattr(nf.time, "monotonic", lambda: clock["t"])
    nf._resolved.clear()
    for _ in range(20):
        assert nf._resolve("opendubs-neck.local") is None
    assert len(calls) == 1
    answer["addr"], clock["t"] = "10.42.2.12", clock["t"] + nf._RESOLVE_RETRY_S
    assert nf._resolve("opendubs-neck.local") == "10.42.2.12" and len(calls) == 2


def test_the_head_holds_still_while_someone_is_talking(monkeypatch):
    """The user's idea, 2026-10-19: the neck's servos must not move while the voice service listens."""
    import types
    sent = _sends(monkeypatch)
    talking = {"now": True}
    monkeypatch.setitem(sys.modules, "voice_proxy", types.SimpleNamespace(listening=lambda: talking["now"]))
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG) == "holding still: someone is talking to the robot"
    assert sent == []                                                   # not even a NECK read
    talking["now"] = False
    assert nf.step(CFG) is None and sent[-1].startswith("GAZE")         # moves again once it stops listening
