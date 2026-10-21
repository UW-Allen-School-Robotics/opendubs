"""Tests for arm planner bridge in composer (spline, cbirrt, curobo integration)."""
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
CHOREO = os.path.dirname(DIR)
REPO = os.path.dirname(os.path.dirname(CHOREO))
for p in (REPO, CHOREO):
    if p not in sys.path:
        sys.path.insert(0, p)

from software.choreo.composer import arm_planner_bridge
from software.choreo import cue


def test_planners_available():
    planners = arm_planner_bridge.get_available_planners()
    assert "spline" in planners
    assert planners["spline"]["available"] is True
    assert "cbirrt" in planners
    print("  ok    planners query reports spline and cbirrt")


def test_mirror_conversions():
    # Mirrored joints for ARM_L are 1, 2, 3
    q_outward = {str(j): 10.0 * j for j in range(1, 8)}
    # Right arm: no sign change
    q_right_rad = arm_planner_bridge.choreo_to_model_rad("right", q_outward)
    q_right_deg = arm_planner_bridge.model_rad_to_choreo("right", q_right_rad)
    for j in range(1, 8):
        assert abs(q_right_deg[str(j)] - 10.0 * j) < 1e-3

    # Left arm: 1, 2, 3 should invert in model radians
    q_left_rad = arm_planner_bridge.choreo_to_model_rad("left", q_outward)
    q_left_deg = arm_planner_bridge.model_rad_to_choreo("left", q_left_rad)
    for j in range(1, 8):
        assert abs(q_left_deg[str(j)] - 10.0 * j) < 1e-3
    print("  ok    left/right arm mirrored joints conversion roundtrips")


def test_plan_arm_move():
    doc = {
        "id": "test-plan",
        "name": "Test Plan",
        "slots": ["ARM_R"],
        "requires": {"arms": "active", "tab5": True, "rc_authority": "AUTO"},
        "cues": [
            {"t": 0.0, "slot": "ARM_R", "pose": "rest"},
            {"t": 3.0, "slot": "ARM_R", "joints": {"1": 15, "2": 20, "4": 60}},
        ],
    }
    raw_text = """id: test-plan
name: Test Plan
slots: [ARM_R]
requires:
  arms: active
  tab5: true
  rc_authority: AUTO
cues:
  - { t: 0.0, slot: ARM_R, pose: rest }
  - { t: 3.0, slot: ARM_R, joints: { 1: 15, 2: 20, 4: 60 } }
"""
    res = arm_planner_bridge.plan_arm_move(doc, raw_text, 1, planner_choice="cbirrt")
    assert res["ok"] is True
    assert "planner: cbirrt" in res["text"]
    print("  ok    plan_arm_move succeeds with cbirrt and updates cue")


if __name__ == "__main__":
    print("arm planner bridge: unit tests")
    test_planners_available()
    test_mirror_conversions()
    test_plan_arm_move()
    print("all good")
