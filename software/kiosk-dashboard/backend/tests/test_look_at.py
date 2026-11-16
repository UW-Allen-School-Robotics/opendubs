"""Look-at (look_at.py): the aim is right, the gates hold, and it never fights neck_follow."""
import math
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import look_at as la
import neck_follow
import neck_state

K = 300 / 180
URDF = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "description", "opendubs", "opendubs.urdf")


def _chain():
    with open(URDF, encoding="utf-8") as fh:
        return la.chain(la.parse_urdf(fh.read()), "base_link", "camera_link")


def test_the_aim_puts_the_cameras_axis_through_the_point_in_joint_states_signs():
    """A sign slip here aims a real head the wrong way: check the solve, and that its commanded degrees
    come back through neck_state.to_radians (what /head/joint_states publishes) as the same angles."""
    ch = _chain()
    for p in ([2.0, 1.0, 0.5], [1.0, -2.0, 2.0], [0.5, 0.0, 1.0]):
        pan, tilt = la.solve(ch, p, 0.0)
        az, el, fwd = la._bearing(ch, {"head_pan": pan, "head_tilt": tilt}, p)
        assert abs(az) < 1e-6 and abs(el) < 1e-6 and fwd > 0
        assert neck_state.to_radians(*la.to_cmd(pan, tilt, K), 300) == pytest.approx((pan, tilt), abs=1e-9)
    # Left of the robot turns the head left (+pan). The Femto already looks ~36 deg down, so only a point
    # on the floor close by tips the face further down (+tilt), and one at its own height tips it up.
    pan, tilt = la.solve(ch, [1.0, 1.0, 0.0], 0.0)
    assert pan > 0 and tilt > 0
    assert la.solve(ch, [3.0, 0.0, 1.35], 0.0)[1] < 0


class FakeNeck:
    """pantilt_core's slew, exactly: a 1.5-degree deadband, then 70 deg/s in 50 Hz ticks."""

    def __init__(self, pan=90.0, tilt=90.0):
        self.pan, self.tilt, self.tgt, self.moving, self.t = pan, tilt, (pan, tilt), False, time.time()
        self.sent = []

    def step(self):
        now = time.time()
        while self.t + 0.02 <= now:
            self.t += 0.02
            self.moving = False
            for i, name in ((0, "pan"), (1, "tilt")):
                d = self.tgt[i] - getattr(self, name)
                if abs(d) > 1.5:
                    setattr(self, name, getattr(self, name) + math.copysign(min(abs(d), 1.4), d))
                    self.moving = True

    def read(self):
        self.step()
        return {"pan": self.pan, "tilt": self.tilt, "moving": self.moving, "owner": "gaze",
                "tilt_band": (40.0, 140.0), "at": time.time()}

    def gaze(self, p, t):
        self.step()
        self.tgt = (min(180.0, max(0.0, p)), min(140.0, max(40.0, t)))
        self.sent.append((p, t))
        return f"OK gaze {round(p)} {round(t)}"


@pytest.fixture
def rig(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    neck = FakeNeck()
    gates = {"why": None}
    monkeypatch.setattr(la, "_neck", neck.read)
    monkeypatch.setattr(la, "_send", lambda p, t: neck.gaze(p, t))
    monkeypatch.setattr(la, "_gaze_off", lambda: neck.sent.append("OFF"))
    monkeypatch.setattr(la, "_ensure_hold_task", lambda: None)
    monkeypatch.setattr(la, "_k", lambda: K)
    monkeypatch.setattr(la, "urdf_path", lambda env=None: URDF)
    monkeypatch.setattr(neck_follow, "motion_gate", lambda: gates["why"])
    monkeypatch.setattr(neck_follow, "being_touched", lambda: False)
    monkeypatch.setattr(la, "POLL_S", 0.01)
    la.session.update(active=False, owner=None, holding=False, cmd=None)
    app = FastAPI()
    app.include_router(la.router)
    yield TestClient(app), neck, gates
    la.session.update(active=False, owner=None, holding=False, cmd=None)


AIM = {"owner": "hallpass", "frame_id": "base_link", "x": 2.0, "y": 1.0, "z": 0.5, "timeout_s": 6}


def test_it_settles_within_reach_of_the_necks_deadband_and_holds(rig):
    client, neck, _ = rig
    r = client.post("/api/head/look_at", json=AIM).json()
    # A GAZE alone stops up to 1.5 commanded degrees (2.5 real) short; the one nudge gets it to ~1.2.
    assert r["err_deg"] <= 1.2, r
    if r["settled"]:
        assert r["err_deg"] <= 1.0 and r["t_settled"]
    else:
        assert r["reason"] == "stopped_short"
    assert la.active() and client.get("/api/head/look_at").json()["owner"] == "hallpass"
    assert client.post("/api/head/look_at", json={**AIM, "owner": "other"}).status_code == 409
    assert client.post("/api/head/look_at/release", json={"owner": "hallpass"}).json()["released"]
    assert neck.sent[-1] == "OFF" and not la.active()


def test_out_of_reach_moves_nothing_and_says_what_it_needs(rig):
    client, neck, _ = rig
    r = client.post("/api/head/look_at", json={**AIM, "x": -3.0, "y": 0.0, "z": 1.5}).json()   # behind it
    assert r["settled"] is False and r["reason"] == "out_of_range" and r["limits"]["pan_deg"] == [-150.0, 150.0]
    assert neck.sent == [] and not la.active()


def test_a_closed_gate_blocks_it_and_while_it_holds_tracking_sends_nothing(rig, tmp_path, monkeypatch):
    client, neck, gates = rig
    gates["why"] = "the robot is E_STOP_LATCH"
    r = client.post("/api/head/look_at", json=AIM).json()
    assert r["reason"] == "blocked: the robot is E_STOP_LATCH" and neck.sent == []
    gates["why"] = None
    client.post("/api/head/look_at", json=AIM)
    import head_settings
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "s.json"))
    head_settings.save({**head_settings.DEFAULTS, "tracking": "tablet"})
    assert neck_follow.why_not_now() == "a capture mission is aiming the head (look-at)"
    # A gate that closes mid-hold stops the renewals; the session stays, so tracking still waits.
    n = len(neck.sent)
    gates["why"] = "a show is loaded"
    la._hold_tick()
    assert len(neck.sent) == n and la.session["blocked"] == "a show is loaded" and la.active()


def test_a_dead_runner_frees_the_head_when_hold_s_runs_out(rig):
    client, _, _ = rig
    client.post("/api/head/look_at", json={**AIM, "hold_s": 1})
    assert la.active()
    la.session["until"] = la._clock() - 0.01
    assert not la.active() and la.session["why"] == "hold_s ran out"
