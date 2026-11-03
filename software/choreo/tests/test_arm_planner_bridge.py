"""Tests for arm planner bridge in composer (spline, cbirrt, curobo integration)."""
import os
import sys
import yaml

DIR = os.path.dirname(os.path.abspath(__file__))
CHOREO = os.path.dirname(DIR)
REPO = os.path.dirname(os.path.dirname(CHOREO))
for p in (REPO, CHOREO):
    if p not in sys.path:
        sys.path.insert(0, p)

from software.choreo.composer import arm_planner_bridge, yamledit
from software.choreo import cue

LIMITS = cue.load_limits(os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml"))


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
            {"t": 4.0, "slot": "ARM_R", "joints": {"1": 15, "2": 20, "4": 60}},
            {"t": 8.0, "slot": "ARM_R", "pose": "rest"},
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
  - { t: 4.0, slot: ARM_R, joints: { 1: 15, 2: 20, 4: 60 } }
  - { t: 8.0, slot: ARM_R, pose: rest }
"""
    # 1. Spline planner works in all environments
    res_spline = arm_planner_bridge.plan_arm_move(doc, raw_text, 0, planner_choice="spline")
    assert res_spline["ok"] is True
    new_doc_spline = yaml.safe_load(res_spline["text"])
    probs_spline = cue.Show(new_doc_spline, "").validate(LIMITS)
    assert len(probs_spline) == 0, f"Unexpected validation problems in spline: {probs_spline}"
    print("  ok    plan_arm_move succeeds with spline and produces valid show")

    # 1b. Spline planner on pose: rest cue preserves pose: rest and does not clamp rest
    res_rest = arm_planner_bridge.plan_arm_move(doc, raw_text, 1, planner_choice="spline")
    assert res_rest["ok"] is True
    assert "clamped to usable joint range" not in res_rest["message"]
    new_doc_rest = yaml.safe_load(res_rest["text"])
    assert new_doc_rest["cues"][1].get("pose") == "rest"
    assert "joints" not in new_doc_rest["cues"][1]
    probs_rest = cue.Show(new_doc_rest, "").validate(LIMITS)
    assert len(probs_rest) == 0, f"Unexpected validation problems in rest move: {probs_rest}"
    print("  ok    plan_arm_move to pose: rest cue preserves pose: rest without clamping")

    # 2. CBiRRT planner if dependencies available
    planners = arm_planner_bridge.get_available_planners()
    try:
        import mujoco  # noqa: F401
        have_mujoco = True
    except ImportError:
        have_mujoco = False

    if not have_mujoco or not planners.get("cbirrt", {}).get("available"):
        print("  skip  plan_arm_move with cbirrt (mujoco/sscbirrt not installed)")
        return

    res = arm_planner_bridge.plan_arm_move(doc, raw_text, 0, planner_choice="cbirrt")
    assert res["ok"] is True
    assert "timeline_notes:" in res["text"]
    new_doc = yaml.safe_load(res["text"])
    probs = cue.Show(new_doc, "").validate(LIMITS)
    assert len(probs) == 0, f"Unexpected validation problems: {probs}"
    print("  ok    plan_arm_move succeeds with cbirrt and produces valid show")


def test_reach_target_solve_and_validate():
    doc = {
        "id": "test-reach",
        "name": "Test Reach",
        "slots": ["ARM_R"],
        "requires": {"arms": "active", "tab5": True, "rc_authority": "AUTO"},
        "cues": [
            {"t": 5.0, "slot": "ARM_R", "joints": {"1": 20, "2": 20, "4": 70}},
            {"t": 10.0, "slot": "ARM_R", "pose": "rest"},
        ],
    }
    raw_text = """id: test-reach
name: Test Reach
slots: [ARM_R]
requires:
  arms: active
  tab5: true
  rc_authority: AUTO
cues:
  - { t: 5.0, slot: ARM_R, joints: { 1: 20, 2: 20, 4: 70 } }
  - { t: 10.0, slot: ARM_R, pose: rest }
"""
    try:
        import mujoco  # noqa: F401
        have_mujoco = True
    except ImportError:
        have_mujoco = False

    planners = arm_planner_bridge.get_available_planners()
    if not have_mujoco or not planners.get("cbirrt", {}).get("available"):
        print("  skip  reach_target_solve_and_validate (mujoco/cbirrt not installed)")
        return

    res = arm_planner_bridge.plan_arm_move(doc, raw_text, 0, planner_choice="cbirrt", reach_target="head_screen")
    assert res["ok"] is True
    assert "reach: head_screen" in res["text"]
    assert "joints:" not in res["text"].splitlines()[7]  # cue at index 0 must NOT have joints:
    new_doc = yaml.safe_load(res["text"])
    probs = cue.Show(new_doc, "").validate(LIMITS)
    assert len(probs) == 0, f"Reach cue validation failed: {probs}"
    print("  ok    reach target produces mutually exclusive reach/joints and validates cleanly")


def test_clamping_reported():
    report = []
    out = arm_planner_bridge.clamp_to_usable_limits({"4": 200.0}, "right", LIMITS, report=report)
    assert len(report) > 0
    assert "J4" in report[0]
    print("  ok    clamp_to_usable_limits reports clamped joints")


def test_partial_joints_filled_from_previous_pose():
    doc = {
        "id": "test-partial",
        "name": "Test Partial",
        "slots": ["ARM_R"],
        "requires": {"arms": "active", "tab5": True, "rc_authority": "AUTO"},
        "cues": [
            {"t": 2.0, "slot": "ARM_R", "joints": {"1": 15, "2": 25, "3": 10, "4": 40, "5": 20, "6": 15, "7": 30}},
            {"t": 6.0, "slot": "ARM_R", "joints": {"4": 75}},
        ],
    }
    raw_text = """id: test-partial
name: Test Partial
slots: [ARM_R]
requires:
  arms: active
  tab5: true
  rc_authority: AUTO
cues:
  - { t: 2.0, slot: ARM_R, joints: { 1: 15, 2: 25, 3: 10, 4: 40, 5: 20, 6: 15, 7: 30 } }
  - { t: 6.0, slot: ARM_R, joints: { 4: 75 } }
"""
    res = arm_planner_bridge.plan_arm_move(doc, raw_text, 1, planner_choice="spline")
    assert res["ok"] is True
    parsed = yaml.safe_load(res["text"])
    cue1_joints = parsed["cues"][1]["joints"]
    get_j = lambda j: float(cue1_joints[j] if j in cue1_joints else cue1_joints[str(j)])
    # Joint 4 should be 75, and unspecified joints should keep previous values, NOT 0.0
    assert abs(get_j(4) - 75.0) < 1e-2
    assert abs(get_j(1) - 15.0) < 1e-2
    assert abs(get_j(2) - 25.0) < 1e-2
    assert abs(get_j(5) - 20.0) < 1e-2
    print("  ok    partial joint cues preserve previous pose values rather than 0.0")


def test_yamledit_insert():
    raw_text = """id: test
slots: [ARM_R]
cues:
  - { t: 0.0, slot: ARM_R, pose: rest }
  - { t: 5.0, slot: ARM_R, joints: { 1: 20 } }
"""
    edits = [
        {"op": "insert", "index": 1, "cue": {"t": 2.5, "slot": "ARM_R", "joints": {"1": 10}}},
    ]
    out = yamledit.apply(raw_text, edits)
    parsed = yaml.safe_load(out)
    assert len(parsed["cues"]) == 3
    assert parsed["cues"][1]["t"] == 2.5
    print("  ok    yamledit op insert correctly places cue before target index")


def test_yamledit_clear():
    raw_text = """id: test
slots: [ARM_L, ARM_R, SOUND]
cues:
  - { t: 1.0, slot: ARM_L, joints: { 1: 10 } }
  - { t: 1.5, slot: SOUND, file: beep.wav }
  - { t: 2.0, slot: ARM_R, joints: { 1: -10 } }
"""
    # 1. Clear arms only
    out_arms = yamledit.apply(raw_text, [{"op": "clear", "slots": ["ARM_L", "ARM_R"]}])
    p_arms = yaml.safe_load(out_arms)
    assert len(p_arms["cues"]) == 1
    assert p_arms["cues"][0]["slot"] == "SOUND"

    # 2. Clear all
    out_all = yamledit.apply(raw_text, [{"op": "clear"}])
    p_all = yaml.safe_load(out_all)
    assert p_all["cues"] == []

    # 3. Add cue after clear all
    out_added = yamledit.apply(out_all, [{"op": "add", "cue": {"t": 1.0, "slot": "ARM_L", "joints": {"1": 5}}}])
    p_added = yaml.safe_load(out_added)
    assert len(p_added["cues"]) == 1
    assert p_added["cues"][0]["joints"][1] == 5
    print("  ok    yamledit op clear wipes cues cleanly and accepts subsequent cues")


def test_yamledit_delete_last_cue():
    raw_text = """id: test
slots: [ARM_R]
cues:
  - { t: 0.0, slot: ARM_R, pose: rest }
"""
    out = yamledit.apply(raw_text, [{"op": "delete", "index": 0}])
    parsed = yaml.safe_load(out)
    assert parsed["cues"] == []
    assert "cues: []" in out
    print("  ok    yamledit delete last cue writes empty list correctly")


if __name__ == "__main__":
    print("arm planner bridge: unit tests")
    test_planners_available()
    test_mirror_conversions()
    test_clamping_reported()
    test_partial_joints_filled_from_previous_pose()
    test_plan_arm_move()
    test_reach_target_solve_and_validate()
    test_yamledit_insert()
    test_yamledit_clear()
    test_yamledit_delete_last_cue()
    print("all good")
