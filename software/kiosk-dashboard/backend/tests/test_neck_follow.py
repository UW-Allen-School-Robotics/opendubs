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
    assert nf.aim(STILL, (0.52, 0.51, 0.9), CFG)[1] == "close enough: eyes only"    # jitter: the eyes'
    assert nf.aim(STILL, (0.6, 0.55, 0.9), CFG)[1] is None                         # the head centres it
    assert "held" in nf.aim({**STILL, "owner": "rc"}, (0.9, 0.5, 0.9), CFG)[1]     # a person has it
    assert nf.aim({**STILL, "moving": True}, (0.9, 0.5, 0.9), CFG)[1] is None      # a turning head aims too


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
    _fresh()
    return sent


def _fresh():
    nf.state.update(stopped=None, limit_since=None, poses=[], hist_seen=None, sighting=None, prev_sighting=None)


def test_one_step_sends_a_leased_gaze(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9, "age_ms": 0})
    assert nf.step(CFG) is None
    assert sent == ["NECK", "GAZE 102.0 90.0 600"]


def test_nothing_is_sent_when_not_allowed_or_nobody_is_seen(monkeypatch):
    sent = _sends(monkeypatch, why="a show is loaded (wave, RUNNING)")
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9, "age_ms": 0})
    assert nf.step(CFG) == "a show is loaded (wave, RUNNING)" and sent == []
    sent = _sends(monkeypatch)
    nf.note_target(None)                                                # lost: the lease runs out
    assert nf.step(CFG) == "nobody seen" and not [s for s in sent if s.startswith("GAZE")]
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9, "age_ms": 0})
    nf.state["target_at"] = time.monotonic() - 5                        # stale
    assert nf.step(CFG) == "nobody seen" and not [s for s in sent if s.startswith("GAZE")]
    nf.note_target({"ix": 9, "iy": 0.5, "conf": 0.9, "age_ms": 0})                   # nonsense is ignored
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


def test_a_person_below_centre_lowers_the_head_by_default():
    """The neck's tilt rises with its degrees (measured 2026-10-15); the image's y grows downward."""
    cfg = nf.config()
    assert cfg["tilt_sign"] == -1.0
    (_, tilt), _ = nf.aim(STILL, (0.5, 0.95, 0.9), cfg)                 # a person low in the frame
    assert tilt < 90


class _Robot:
    """A simulated robot: a neck that SLEWS at 70 deg/s to where GAZE sends it, and a tracker that
    captures a frame every `every` s, reports it `latency` s later, and re-sends that same sighting
    (its age growing) until the next one, as the face page does. A person stands or walks at
    person(t) degrees of pan. The real geometry, measured 2026-10-16: a person at a higher pan, the
    robot's left, is LEFT of the image's centre."""

    def __init__(self, monkeypatch, pan_sign, person, start=90.0, every=0.25, latency=0.4):
        self.t, self.pan, self.target, self.person = 1000.0, start, start, person
        self.every, self.latency, self.track = every, latency, [(self.t, start)]
        self.sent, self.cfg = [], {**CFG, "pan_sign": pan_sign, "tilt_sign": -1.0, "gain": 1.0, "rate": 10.0}
        monkeypatch.setattr(nf, "_ask", self.ask)
        monkeypatch.setattr(nf, "why_not_now", lambda: None)
        monkeypatch.setattr(nf, "_clock", lambda: self.t)
        import head
        monkeypatch.setattr(head, "neck_host", lambda env=None: "neck.local")
        _fresh()

    def ask(self, host, line, timeout=0.3):
        if line == "NECK":
            return f"OK neck pan={self.pan:.2f} tilt=90.0 target=0,0 moving=0 owner=none"
        self.target = float(line.split()[1])
        self.sent.append(self.target)
        return "OK gaze"

    def pose(self, t):
        """Where the head truly was at `t`: it slews continuously between the steps."""
        for (t0, p0), (t1, p1) in zip(self.track, self.track[1:]):
            if t0 <= t <= t1:
                return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
        return self.track[-1][1]

    def run(self, seconds):
        why, first = None, self.t
        while self.t < first + seconds:
            self.t = round(self.t + 0.1, 6)
            step = max(-7.0, min(7.0, self.target - self.pan))         # 70 deg/s
            self.pan += step
            self.track.append((self.t, self.pan))
            k = int((self.t - self.latency - first) // self.every)     # the newest frame reported by now
            if k >= 0:
                tc = first + k * self.every                             # when it was captured
                ox = (self.pose(tc) - self.person(tc)) / (self.cfg["hfov"] / 2)
                if abs(ox) <= 1:
                    nf.note_target({"ix": 0.5 + ox / 2, "iy": 0.5, "conf": 0.9, "kind": "face",
                                    "age_ms": round((self.t - tc) * 1000)})
                else:
                    nf.note_target(None)                                # out of the picture
            why = nf.step(self.cfg)
            if why and why.startswith("STOPPED"):
                break
        return why


def test_the_right_sign_centres_a_person_standing_still(monkeypatch):
    assert nf.config()["pan_sign"] == -1.0 and nf.config()["gain"] == 1.0  # the measured sign, all the way
    r = _Robot(monkeypatch, -1.0, lambda t: 105.0)
    why = r.run(3.0)
    assert not (why or "").startswith("STOPPED"), why
    assert abs(r.pan - 105.0) <= nf.EYE_ONLY * CFG["hfov"] / 2 + 0.01, r.pan
    assert max(r.sent) <= 105.0 + 0.5                                    # it goes there; it does not overshoot


def test_a_wrong_sign_is_caught_as_a_runaway_before_the_limit(monkeypatch):
    r = _Robot(monkeypatch, 1.0, lambda t: 105.0)
    why = r.run(5.0)
    assert why and why.startswith("STOPPED") and "PAN_SIGN" in why, why
    assert min(r.sent) > 90 - CFG["pan_range"], r.sent                  # it never sent the limit
    assert nf.step(r.cfg).startswith("STOPPED")                         # latched


def test_a_person_crossing_the_view_is_followed_not_stopped(monkeypatch):
    """The robot, 2026-10-16: the user crossed the view and the old guard tripped. 20 deg/s, there and back."""
    walk = lambda t: 75.0 + 30.0 * (1 - abs(((t - 1000.0) / 1.5) % 2 - 1))   # 75 -> 105 -> 75 ...
    r = _Robot(monkeypatch, -1.0, walk, start=75.0)
    why = r.run(6.0)
    assert not (why or "").startswith("STOPPED"), why
    assert max(r.sent) > 98 and min(r.sent) < 82                        # it went with them both ways


def test_a_brisk_walker_is_not_mistaken_for_a_runaway(monkeypatch):
    r = _Robot(monkeypatch, -1.0, lambda t: 65.0 + 30.0 * (t - 1000.0))   # 30 deg/s, one way, 65 -> 113
    why = r.run(1.6)
    assert not (why or "").startswith("STOPPED"), why
    # It went with them, behind by no more than what it cannot know: the latency, a frame, an update.
    behind = r.person(r.t) - r.pan
    assert 0 <= behind <= 30.0 * (r.latency + r.every + 0.1), behind


def test_a_held_sighting_aims_at_one_bearing_and_never_accumulates(monkeypatch):
    """Replays the robot, 2026-10-16: the page repeated one face sighting (x 0.27) for ~2 s, age growing,
    and the old code re-aimed the offset five times a second: pan 100 -> 112 -> 120. Now that sighting is
    a bearing, 100 + 0.46 x 30 = 113.8, and every re-send of it says the same."""
    r = _Robot(monkeypatch, -1.0, lambda t: 113.8, start=100.0)
    r.ask("x", "GAZE 100 90")
    nf.note_pose(r.t, 100.0, 90.0)
    tc = r.t
    for _ in range(20):                                                  # 2 s at 10 Hz, one sighting
        r.t = round(r.t + 0.1, 6)
        r.pan += max(-7.0, min(7.0, r.target - r.pan))
        nf.note_target({"ix": 0.27, "iy": 0.5, "conf": 0.68, "kind": "face", "age_ms": round((r.t - tc) * 1000)})
        why = nf.step(r.cfg)
    assert {round(p, 1) for p in r.sent[1:]} == {113.8}, r.sent
    assert why == "close enough: eyes only" and nf.state["stopped"] is None


def test_a_sighting_is_placed_by_the_pose_its_frame_was_taken_at():
    _fresh()
    nf.note_pose(10.0, 90.0, 90.0)
    nf.note_pose(10.2, 104.0, 90.0)                                      # turning at 70 deg/s
    assert nf.pose_at(10.1) == (97.0, 90.0)
    assert nf.pose_at(10.3) == (104.0, 90.0)                             # after the last: the last
    assert nf.pose_at(9.0) is None                                       # before any: unknown
    nf.note_pose(30.0, 104.0, 90.0)
    assert nf.pose_at(10.1) is None                                      # forgotten after POSE_KEEP_S


def test_a_sighting_older_than_the_remembered_pose_is_not_used(monkeypatch):
    sent = _sends(monkeypatch)
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9, "age_ms": 9000})
    assert nf.step(CFG) == "the sighting is older than the head's remembered pose"
    assert not [s for s in sent if s.startswith("GAZE")]


def test_changing_the_setting_clears_a_stop(tmp_path, monkeypatch):
    import head_settings
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "s.json"))
    head_settings.save({**head_settings.DEFAULTS, "tracking": "off"})
    nf.state["stopped"] = "STOPPED: test"
    nf.why_not_now()
    assert nf.state["stopped"] is None


def test_a_target_out_of_reach_is_held_not_chased(monkeypatch):
    sent = _sends(monkeypatch, neck="OK neck pan=119.0 tilt=90.0 target=0,0 moving=0 owner=none")
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9, "age_ms": 0})                  # far right: the aim is the limit
    assert nf.step(CFG) is None                                            # sent, the clock starts
    nf.state["limit_since"] = time.monotonic() - 3
    nf.note_target({"ix": 1.0, "iy": 0.5, "conf": 0.9, "age_ms": 0})
    assert nf.step(CFG).startswith("holding")
    assert sent.count("NECK") == 2 and sum(1 for s in sent if s.startswith("GAZE")) == 1


def test_the_neck_block_says_how_old_it_is():
    nf.state.update(neck={"pan": 90.0, "tilt": 90.0, "moving": False, "owner": "none"}, neck_at=time.monotonic() - 4)
    assert nf.status()["neck_age_s"] >= 4


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


def test_a_reserved_state_sends_nothing_and_says_why(tmp_path, monkeypatch):
    sent = _sends(monkeypatch)
    monkeypatch.setattr(nf, "why_not_now", lambda: "the robot is DEGRADED: the head does not follow in a reserved state")
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9})
    assert "DEGRADED" in nf.step(nf.config())
    assert not [l for l in sent if l.startswith("GAZE")]
    assert nf.state["stopped"] is None                                 # a reason, never a latch


def test_a_page_without_sighting_times_never_moves_the_head(monkeypatch):
    sent = _sends(monkeypatch)
    for bad in (None, -5, 99999, "0", True):
        t = {"ix": 0.9, "iy": 0.5, "conf": 0.9}
        if bad is not None:
            t["age_ms"] = bad
        nf.note_target(t)
        assert "reload" in nf.step(CFG)
    assert not [s for s in sent if s.startswith("GAZE")]
    nf.note_target({"ix": 0.9, "iy": 0.5, "conf": 0.9, "age_ms": 40})
    assert nf.status()["sighting_age_s"] is not None


def test_a_person_moves_the_head_at_a_lower_confidence_than_motion():
    assert nf.aim(STILL, (0.9, 0.5, 0.5), CFG, "person")[1] is None              # 0.5 >= 0.45: turns
    assert nf.aim(STILL, (0.9, 0.5, 0.5), CFG, "motion")[1] == "not confident enough"
    assert nf.aim(STILL, (0.9, 0.5, 0.4), CFG, "person")[1] == "not confident enough"


def test_the_runaway_signature_is_a_bearing_that_flees_the_head():
    """(pan_at, tilt_at, pan_bearing, tilt_bearing, ox, oy) for two sightings."""
    still_right = ((90.0, 90.0, 105.0, 90.0, -0.5, 0.0), (105.0, 90.0, 105.2, 90.0, -0.01, 0.0))
    assert nf.runaway(*still_right) is None                              # the bearing stayed put
    wrong = ((90.0, 90.0, 75.0, 90.0, -0.5, 0.0), (75.0, 90.0, 45.0, 90.0, -1.0, 0.0))
    assert nf.runaway(*wrong) == "pan"                                   # it fled twice as far
    crossing = ((112.0, 90.0, 104.0, 90.0, 0.27, 0.0), (104.0, 90.0, 88.0, 90.0, -0.53, 0.0))
    assert nf.runaway(*crossing) is None                                 # sides flipped: following
    assert nf.runaway(None, wrong[1]) is None
