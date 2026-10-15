"""The head follows the person the face sees (neck_follow.py): only under the rules, within limits."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import neck_follow as nf

CFG = {"pan_sign": 1.0, "tilt_sign": 1.0, "hfov": 60.0, "vfov": 40.0, "gain": 0.5, "rate": 5.0,
       "pan_range": 30.0, "tilt_range": 18.0}
STILL = {"pan": 90.0, "tilt": 90.0, "moving": False, "owner": "none"}


def test_a_person_off_to_the_side_turns_the_head_part_way():
    (pan, tilt), why = nf.aim(STILL, (0.9, 0.5, 0.9), CFG)            # right of centre, level
    assert why is None and pan == 90 + 0.8 * 30 * 0.5 and tilt == 90.0


def test_the_rules_that_keep_the_head_still():
    assert nf.aim(STILL, (0.9, 0.5, 0.5), CFG)[1] == "not confident enough"
    assert nf.aim(STILL, (0.6, 0.55, 0.9), CFG)[1] == "close enough: eyes only"     # a glance: eyes only
    assert "held" in nf.aim({**STILL, "owner": "rc"}, (0.9, 0.5, 0.9), CFG)[1]     # a person has it
    assert nf.aim({**STILL, "moving": True}, (0.9, 0.5, 0.9), CFG)[1] == "the neck is moving"


def test_never_past_the_envelope():
    (pan, tilt), _ = nf.aim({**STILL, "pan": 118.0, "tilt": 106.0}, (1.0, 1.0, 1.0), CFG)
    assert pan == 120.0 and tilt == 108.0                               # 90 ± 30, 90 ± 18
    (pan, _), _ = nf.aim(STILL, (0.0, 0.5, 1.0), {**CFG, "pan_sign": -1.0})       # the sign is config
    assert pan > 90


def test_the_config_is_robot_env_with_safe_bounds(monkeypatch):
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_PAN_SIGN", "-1")
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_GAIN", "7")               # out of range: clamped
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_PAN_RANGE", "80")         # never past the neck's own envelope
    monkeypatch.setenv("OPENDUBS_NECK_FOLLOW_HFOV_DEG", "wide")        # nonsense: the default
    c = nf.config()
    assert c["pan_sign"] == -1.0 and c["gain"] == 1.0 and c["pan_range"] == 30.0 and c["hfov"] == 60.0


def test_the_necks_status_line_is_read():
    n = nf.parse_neck("OK neck pan=101.5 tilt=88.0 target=101.5,88.0 moving=1 owner=gaze")
    assert n == {"pan": 101.5, "tilt": 88.0, "moving": True, "owner": "gaze"}
    assert nf.parse_neck("ERR neck no pan/tilt fitted") is None and nf.parse_neck("") is None


def _sends(monkeypatch, why=None, neck="OK neck pan=90.0 tilt=90.0 target=90.0,90.0 moving=0 owner=none"):
    sent = []

    def ask(host, line, timeout=0.3):
        sent.append(line)
        return neck if line == "NECK" else f"OK gaze {line.split()[1]} {line.split()[2]}"
    monkeypatch.setattr(nf, "_ask", ask)
    monkeypatch.setattr(nf, "why_not_now", lambda: why)
    import head
    monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
    return sent


def test_one_step_sends_a_leased_gaze(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG) is None
    assert sent == ["NECK", "GAZE 102.0 90.0 600"]


def test_nothing_is_sent_when_not_allowed_or_nobody_is_seen(monkeypatch):
    sent = _sends(monkeypatch, why="a show is loaded (wave, RUNNING)")
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG) == "a show is loaded (wave, RUNNING)" and sent == []
    sent = _sends(monkeypatch)
    nf.note_target(None)                                                # lost: the lease runs out
    assert nf.step(CFG) == "nobody seen" and sent == []
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    nf.state["target_at"] = time.monotonic() - 5                        # stale
    assert nf.step(CFG) == "nobody seen" and sent == []
    nf.note_target({"ix": 9, "iy": 0.5, "conf": 0.9})                   # nonsense is ignored
    assert nf.state["target"][0] == 0.9


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


def test_a_person_below_centre_lowers_the_head_by_default():
    """The neck's tilt rises with its degrees (measured 2026-10-15); the image's y grows downward."""
    cfg = nf.config()
    assert cfg["tilt_sign"] == -1.0
    (_, tilt), _ = nf.aim(STILL, (0.5, 0.95, 0.9), cfg)                 # a person low in the frame
    assert tilt < 90


def _closed_loop(monkeypatch, pan_sign, person_pan=105.0, steps=40, walk=0.0):
    """A simulated neck that goes where GAZE sends it, and a person standing at `person_pan` degrees.
    The camera rides the head, so the person's image offset depends on where the head points (with
    the real geometry: a person at a higher pan is right of the image's centre)."""
    neck = {"pan": 90.0, "tilt": 90.0}
    sent = []

    def ask(host, line, timeout=0.3):
        if line == "NECK":
            return f"OK neck pan={neck['pan']} tilt={neck['tilt']} target=0,0 moving=0 owner=none"
        p, t = float(line.split()[1]), float(line.split()[2])
        neck["pan"], neck["tilt"] = p, t
        sent.append(line)
        return f"OK gaze {p} {t}"
    monkeypatch.setattr(nf, "_ask", ask)
    monkeypatch.setattr(nf, "why_not_now", lambda: None)
    import head
    monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
    nf.state.update(stopped=None, history=[], limit_since=None)
    cfg = {**CFG, "pan_sign": pan_sign, "tilt_sign": -1.0}
    why = None
    for i in range(steps):
        person = person_pan + walk * i                                   # degrees per update (5 a second)
        ox = (person - neck["pan"]) / (cfg["hfov"] / 2)                  # where the person is in the image
        nf.note_target({"ix": min(1, max(0, 0.5 + ox / 2)), "iy": 0.5, "conf": 0.9})
        why = nf.step(cfg)
        if why and why.startswith("STOPPED"):
            break
    return neck, why, sent


def test_a_wrong_sign_is_caught_as_a_runaway_before_the_limit(monkeypatch):
    neck, why, sent = _closed_loop(monkeypatch, pan_sign=-1.0)
    assert why and why.startswith("STOPPED") and "PAN_SIGN" in why, why
    assert neck["pan"] > 90 - CFG["pan_range"]                            # stopped before the limit
    assert nf.step(CFG).startswith("STOPPED")                             # latched


def test_the_right_sign_settles_on_the_person(monkeypatch):
    neck, why, sent = _closed_loop(monkeypatch, pan_sign=1.0)
    assert not (why or "").startswith("STOPPED"), why
    # It closes in until the person is inside the eyes-only band, where the eyes take over by design.
    assert abs(neck["pan"] - 105.0) <= nf.EYE_ONLY * CFG["hfov"] / 2 + 0.01, neck


def test_changing_the_setting_clears_a_stop(tmp_path, monkeypatch):
    import head_settings
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "s.json"))
    head_settings.save({**head_settings.DEFAULTS, "tracking": "off"})
    nf.state["stopped"] = "STOPPED: test"
    nf.why_not_now()
    assert nf.state["stopped"] is None


def test_a_target_out_of_reach_is_held_not_chased(monkeypatch):
    sent = _sends(monkeypatch, neck="OK neck pan=119.0 tilt=90.0 target=0,0 moving=0 owner=none")
    nf.state.update(stopped=None, history=[], limit_since=None)
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9})                  # far right: the aim is the limit
    assert nf.step(CFG) is None                                            # sent, the clock starts
    nf.state["limit_since"] = time.monotonic() - 3
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9})
    assert nf.step(CFG).startswith("holding")
    assert sent.count("NECK") == 2 and sum(1 for s in sent if s.startswith("GAZE")) == 1


def test_the_neck_block_says_how_old_it_is():
    nf.state.update(neck={"pan": 90.0, "tilt": 90.0, "moving": False, "owner": "none"}, neck_at=time.monotonic() - 4)
    assert nf.status()["neck_age_s"] >= 4


def test_a_brisk_walker_with_the_right_sign_is_not_mistaken_for_a_runaway(monkeypatch):
    # 3 degrees per update at 5 Hz = 15 deg/s across the view, starting left of centre.
    neck, why, sent = _closed_loop(monkeypatch, pan_sign=1.0, person_pan=75.0, steps=12, walk=3.0)
    assert not (why or "").startswith("STOPPED"), why
