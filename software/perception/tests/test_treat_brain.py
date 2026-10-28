"""The trick-or-treat decisions that, wrong, move the arm or head when they should not, or jump it.

  python3 -m pytest software/perception/tests/test_treat_brain.py
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from treat_brain import (Brain, BrainParams, Frame, HeadLaw, HeadParams, Offer, OfferParams, PalmFilter,  # noqa: E402
                         Smoother, reach_goal)

ARM_SHOULDER = np.array([0.0, -0.031, 0.698])
# a person 1.2 m in front, shoulders at 1.4 m high; their right hand is on the robot's left (+y)
SH = {"left": np.array([1.2, -0.18, 1.4]), "right": np.array([1.2, 0.18, 1.4])}
HANGING = np.array([1.22, 0.22, 0.85])        # their right hand at their side
HELD_OUT = np.array([0.85, 0.15, 1.05])       # held out toward the robot


def brain():
    return Brain(BrainParams(), Offer(OfferParams()), PalmFilter())


def run(b, t0, t1, palm=HANGING, person=True, acquired=True, hz=14, side="right"):
    """Frames from t0 to t1, ticking at 50 Hz; the last (motion, goal)."""
    t, next_frame, out = t0, t0, None
    while t < t1:
        if t >= next_frame:
            b.update(Frame(t, person, dict(SH) if person else {}, {side: palm} if palm is not None else {}))
            next_frame += 1 / hz
        out = b.tick(t, acquired)
        t += 0.02
    return out


def test_waits_for_the_head_then_follows_only_a_held_out_hand():
    b = brain()
    assert run(b, 0, 1, palm=HELD_OUT, acquired=False)[0] == "hold"     # the head has not settled: nothing moves
    assert run(b, 1, 2, palm=HANGING)[0] == "home"                       # settled, hand at the side: home
    motion, goal = run(b, 2, 2.5, palm=HELD_OUT)
    assert motion == "follow" and np.linalg.norm(goal - HELD_OUT) < 0.05


def test_hand_down_goes_home_and_up_again_follows():
    b = brain()
    run(b, 0, 1, palm=HELD_OUT)
    assert run(b, 1, 2, palm=HANGING)[0] == "home"
    assert run(b, 2, 2.5, palm=HELD_OUT)[0] == "follow"
    assert run(b, 2.5, 3.5, palm=None)[0] == "home"      # the hand gone, the person still there: dropped


def test_a_lost_palm_is_coasted_not_dropped_and_does_not_run_on():
    b = brain()
    run(b, 0, 1, palm=HELD_OUT)
    last = b.filter.x.copy()
    motion, goal = run(b, 1, 1.3, palm=None)               # a fast move loses it for a few frames
    assert motion == "follow" and np.linalg.norm(goal - last) < 0.02


def test_nobody_holds_then_goes_home():
    b = brain()
    run(b, 0, 1, palm=HELD_OUT)
    assert run(b, 1, 2, palm=None, person=False)[0] == "hold"
    assert run(b, 2, 4.5, palm=None, person=False)[0] == "home"


def test_someone_else_stepping_in_holds_the_arm():
    b = brain()
    run(b, 0, 1, palm=HELD_OUT)
    other = {s: v + np.array([0.0, 0.9, 0.0]) for s, v in SH.items()}
    assert not b.update(Frame(1.0, True, other, {}))       # one frame: noise, or a side-on person's shoulders swapping
    assert not b.update(Frame(1.07, True, dict(SH), {}))
    assert [b.update(Frame(1.1 + i * 0.07, True, other, {})) for i in range(3)] == [False, False, True]
    assert b.tick(1.31, False)[0] == "hold"


def test_palm_filter_drops_one_stray_point_and_takes_a_real_jump():
    f = PalmFilter()
    for i in range(10):
        f.update(HELD_OUT, i * 0.07)
    stray = HELD_OUT + np.array([0, 0.6, 0])
    assert not f.update(stray, 0.77)
    assert np.linalg.norm(f.estimate(0.77) - HELD_OUT) < 0.01
    assert f.update(stray + 0.01, 0.84)                     # a second frame there: the hand really moved


def test_reach_goal_follows_sideways_one_to_one_and_stays_in_reach():
    a = reach_goal(np.array([1.2, -0.2, 1.0]), ARM_SHOULDER)
    b = reach_goal(np.array([1.2, 0.1, 1.0]), ARM_SHOULDER)
    assert abs((b[1] - a[1]) - 0.3) < 1e-9
    # never past the reach (elbow straight: singular), behind 0.30 m or over 0.35 m up (straight above the shoulder,
    # where the simulated arm wedged and its guard tripped)
    for p in ([3.0, 2.0, 3.0], [0.0, 0.0, 0.7], [1.0, -1.5, -0.5], [0.5, 0.0, 2.0]):
        d = reach_goal(np.array(p), ARM_SHOULDER) - ARM_SHOULDER
        assert np.linalg.norm(d) <= 0.57 + 1e-9 and d[0] >= 0.30 - 1e-9 and d[2] <= 0.35 + 1e-9


def test_smoother_never_exceeds_its_speed_on_a_jump():
    s = Smoother(vmax=0.5)
    s.reset(np.zeros(3))
    prev = s.p.copy()
    for _ in range(100):
        p = s.step(np.array([1.0, 0, 0]), 0.02)
        assert np.linalg.norm(p - prev) <= 0.5 * 0.02 + 1e-9
        prev = p


def test_head_turns_toward_the_torso_settles_and_freezes_when_the_arm_is_out():
    h = HeadLaw(HeadParams(pan=True))
    h.start(0.0, 90.0, 90.0)
    pan, tilt = h.step(0.1, {"below_deg": 10.0, "right_deg": 10.0}, 0.1)
    assert tilt < 90 and pan < 90                     # below -> tilt down; right -> pan falls (the dashboard's sign)
    h.sent(pan, tilt)
    for i in range(6):                                 # centred frames, after the wait
        t = 1.0 + i * 0.1
        assert h.step(t, {"below_deg": 0.5, "right_deg": 0.0}, t) is None
    assert h.acquired
    assert h.step(2.0, {"below_deg": 7.0, "right_deg": 0}, 2.0, hold_still=True) is None   # under the lock while following
    assert h.step(10.0, None, 0.0, hold_still=True) is None and h.mode == "freeze"
    assert h.step(10.1, None, 0.0) is not None and h.mode == "search" and not h.acquired


def test_capture_waits_for_a_still_raised_hand_and_takes_the_latest():
    from treat_brain import HandCapture
    c = HandCapture(Offer(OfferParams()), frames=5, still_m=0.05)
    assert c.update({"right": HANGING}, SH) is None                  # not lifted: nothing
    for i in range(6):                                               # rising 8 cm a frame: moving, never captured
        assert c.update({"right": HELD_OUT + np.array([0, 0, -0.4 + 0.08 * i])}, SH) is None
    got, sent = None, None
    for i in range(5):                                               # held there, a centimetre of noise
        palm = HELD_OUT + np.array([0.01 * (i % 2), 0, 0])
        if got is None:
            got, sent = c.update({"right": palm}, SH), palm
    assert got is not None and got[0] == "right"
    np.testing.assert_allclose(got[1], sent)                         # the latest palm, the one that completed the run
    assert c.update({"right": HELD_OUT}, SH) is None                 # once only


def test_capture_starts_over_when_the_hand_drops():
    from treat_brain import HandCapture
    c = HandCapture(Offer(OfferParams()), frames=5)
    for _ in range(4):
        assert c.update({"right": HELD_OUT}, SH) is None
    assert c.update({"right": HANGING}, SH) is None                  # dropped on the 5th
    assert all(c.update({"right": HELD_OUT}, SH) is None for _ in range(4))
    assert c.update({"right": HELD_OUT}, SH) is not None
