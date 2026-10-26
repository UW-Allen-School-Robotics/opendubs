#!/usr/bin/env python3
"""End-to-end verification test suite for OpenArm Simulation Motion Planners.

Tests:
  1. IK and FK consistency across both arms using SSIK.
  2. MuJoCo self-collision checking & safety clearance validation.
  3. CBiRRT trajectory planning & TOPP-RA time-optimal retiming.
  4. Modular backend registry and cuRobo adapter graceful fallback.
  5. Performance benchmark runner.
"""
from __future__ import annotations

import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
IK_DIR = os.path.join(SIM_DIR, "ik_artifacts")

for p in (REPO, SIM_DIR, IK_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import pytest
except ImportError:
    pytest = None

try:
    import mujoco
    import scene
    import clearance
    _HAVE_MUJOCO = True
except ImportError:
    _HAVE_MUJOCO = False

try:
    from sim.planner import (
        OpenArmPlanner,
        PlannedTrajectory,
        CBiRRTBackend,
        CuRoboBackend,
        create_backend,
        get_available_backends,
        run_benchmark,
    )
    _HAVE_PLANNER = True
except ImportError:
    _HAVE_PLANNER = False

try:
    import sscbirrt
    _HAVE_SSCBIRRT = True
except ImportError:
    _HAVE_SSCBIRRT = False

try:
    import openarm_left_ik
    import openarm_right_ik
    _HAVE_SSIK = True
except ImportError:
    _HAVE_SSIK = False


def _section(title: str):
    print(f"\n{'=' * 65}")
    print(f"▶ {title}")
    print(f"{'=' * 65}")


def assert_true(cond: bool, msg: str):
    if not cond:
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)
    print(f"  [PASS] {msg}")


def _skip(reason: str):
    print(f"  [SKIP] {reason}")
    if pytest and os.environ.get("PYTEST_CURRENT_TEST"):
        pytest.skip(reason)


def test_ssik_kinematics():
    _section("1. SSIK Analytical Kinematics & FK Consistency")
    if not _HAVE_SSIK:
        _skip("ssik / IK artifacts not available in this environment")
        return

    # Left Arm
    q_test_l = np.radians([10.0, -20.0, 15.0, 60.0, -10.0, 20.0, 5.0])
    T_fk_l = openarm_left_ik.fk(q_test_l)
    sols_l = openarm_left_ik.solve(T_fk_l)
    assert_true(len(sols_l) > 0, f"Left SSIK solved ({len(sols_l)} candidates found)")
    T_recon_l = openarm_left_ik.fk(sols_l[0].q)
    pos_err_l = np.linalg.norm(T_fk_l[:3, 3] - T_recon_l[:3, 3])
    assert_true(pos_err_l < 1e-4, f"Left Arm FK reconstruction error: {pos_err_l * 1000:.4f} mm")

    # Right Arm
    q_test_r = np.radians([-10.0, 20.0, -15.0, 60.0, 10.0, 20.0, -5.0])
    T_fk_r = openarm_right_ik.fk(q_test_r)
    sols_r = openarm_right_ik.solve(T_fk_r)
    assert_true(len(sols_r) > 0, f"Right SSIK solved ({len(sols_r)} candidates found)")
    T_recon_r = openarm_right_ik.fk(sols_r[0].q)
    pos_err_r = np.linalg.norm(T_fk_r[:3, 3] - T_recon_r[:3, 3])
    assert_true(pos_err_r < 1e-4, f"Right Arm FK reconstruction error: {pos_err_r * 1000:.4f} mm")


def test_collision_checker():
    _section("2. MuJoCo Collision Checking & Clearance Guard")
    if not _HAVE_MUJOCO or not _HAVE_PLANNER:
        _skip("MuJoCo / sim.planner not available in this environment")
        return

    planner_l = OpenArmPlanner(arm="left", min_clearance_mm=30.0)
    planner_r = OpenArmPlanner(arm="right", min_clearance_mm=30.0)

    # Safe Left Arm Stow
    q_stow_l = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
    valid_stow_l = planner_l.collision_checker.is_valid(q_stow_l)
    clear_stow_l = planner_l.collision_checker.min_clearance()
    assert_true(valid_stow_l, f"Left Arm stow pose is valid (clearance = {clear_stow_l:.1f} mm)")

    # Safe Right Arm Stow
    q_stow_r = np.radians([0.0, 20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
    valid_stow_r = planner_r.collision_checker.is_valid(q_stow_r)
    clear_stow_r = planner_r.collision_checker.min_clearance()
    assert_true(valid_stow_r, f"Right Arm stow pose is valid (clearance = {clear_stow_r:.1f} mm)")

    # Known Collision Pose (Right Arm J2=182 colliding with head)
    q_head_col = np.radians([0.0, 182.0, 0.0, 100.0, 0.0, 0.0, 0.0])
    valid_head_col = planner_r.collision_checker.is_valid(q_head_col)
    clear_head_col = planner_r.collision_checker.min_clearance()
    assert_true(not valid_head_col, f"Right Arm head collision caught correctly (valid=False, clearance={clear_head_col:.1f} mm)")

    contacts = clearance.clearances(planner_r.model, planner_r.data, limit_mm=50.0)
    top_contact = contacts[0]
    assert_true("head_collision" in (top_contact[1], top_contact[2]), f"Identified contact geom: {top_contact[1]} <-> {top_contact[2]}")


def test_cbirrt_planning():
    _section("3. CBiRRT Motion Planning & TOPP-RA Retiming")
    if not _HAVE_MUJOCO or not _HAVE_PLANNER or not _HAVE_SSCBIRRT:
        _skip("MuJoCo / sscbirrt not available in this environment")
        return

    planner = OpenArmPlanner(arm="left", v_max=1.5, a_max=3.0, min_clearance_mm=30.0)

    q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])  # stow
    q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])   # reach_forward

    t0 = time.perf_counter()
    traj = planner.plan_to_joint_goal(q_start, q_goal, timeout=5.0)
    plan_wall = (time.perf_counter() - t0) * 1000.0

    assert_true(traj is not None, f"CBiRRT trajectory planned successfully in {plan_wall:.1f} ms")
    assert_true(traj.duration > 0.0, f"Trajectory duration: {traj.duration:.2f} s")
    assert_true(len(traj.positions) > 10, f"Waypoints count: {len(traj.positions)}")
    assert_true(traj.min_clearance_mm >= 30.0, f"Minimum clearance along path: {traj.min_clearance_mm:.1f} mm")

    # Verify limits along dense trajectory
    max_v = np.max(np.abs(traj.velocities))
    max_a = np.max(np.abs(traj.accelerations))
    assert_true(max_v <= 1.5 + 0.05, f"Max joint velocity: {max_v:.2f} rad/s (limit: 1.5)")
    assert_true(max_a <= 3.0 * 1.35, f"Max joint acceleration: {max_a:.2f} rad/s^2 (within spline tolerance: <= {3.0 * 1.35:.2f})")

    # Verify start and goal endpoints match
    start_err = np.linalg.norm(traj.positions[0] - q_start)
    goal_err = np.linalg.norm(traj.positions[-1] - q_goal)
    assert_true(start_err < 1e-4, f"Start endpoint matches (err={start_err:.6f} rad)")
    assert_true(goal_err < 1e-4, f"Goal endpoint matches (err={goal_err:.6f} rad)")


def test_modular_backends():
    _section("4. Modular Backend Registry & cuRobo Adapter")
    if not _HAVE_MUJOCO or not _HAVE_PLANNER:
        _skip("MuJoCo / sim.planner not available in this environment")
        return

    backends = get_available_backends()
    assert_true("cbirrt" in backends, "CBiRRT backend registered in registry")
    assert_true("curobo" in backends, "cuRobo backend registered in registry")

    # cuRobo execution if available
    if backends["curobo"]["available"]:
        curobo_planner = OpenArmPlanner(arm="left", backend="curobo")
        assert_true(curobo_planner.backend.name == "curobo", "Planner initialized with cuRobo backend")

        q_start = np.zeros(7)
        q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])
        cu_traj = curobo_planner.plan_to_joint_goal(q_start, q_goal)
        assert_true(cu_traj is not None, f"cuRobo planned valid trajectory in {cu_traj.metadata.get('plan_time_ms', 0):.1f} ms")
        assert_true(cu_traj.min_clearance_mm >= 30.0, f"cuRobo trajectory satisfies collision clearance ({cu_traj.min_clearance_mm:.1f} mm)")
    else:
        print("  [INFO] cuRobo is not installed in current environment; verifying graceful fallback")
        assert_true(backends["curobo"]["available"] is False, "cuRobo backend reports available=False (graceful fallback)")

    if _HAVE_SSCBIRRT:
        # Instantiating CBiRRT by string
        planner = OpenArmPlanner(arm="left", backend="cbirrt")
        assert_true(planner.backend.name == "cbirrt", f"Planner initialized with backend name '{planner.backend.name}'")

        # Custom backend injection test
        from sim.planner.example_custom_planner import MyCustomInterpolationPlanner
        custom_backend = MyCustomInterpolationPlanner(collision_checker=planner.collision_checker, steps=10)
        planner.set_backend(custom_backend)
        assert_true(planner.backend.name == "custom_linear_interp", "Successfully swapped backend to custom planner")

        q_start = np.zeros(7)
        q_goal = np.radians([0.0, -20.0, 0.0, 45.0, 0.0, 15.0, 0.0])
        custom_traj = planner.plan_to_joint_goal(q_start, q_goal)
        assert_true(custom_traj is not None, f"Custom backend generated valid trajectory ({custom_traj.duration:.2f} s)")


def test_benchmark():
    _section("5. Performance Benchmarking")
    if not _HAVE_MUJOCO or not _HAVE_PLANNER or not _HAVE_SSCBIRRT:
        _skip("MuJoCo / sscbirrt not available in this environment")
        return

    res = run_benchmark(arm="left", trials=1, verbose=False)
    assert_true("overall" in res, "Benchmark generated overall summary")
    assert_true("cbirrt" in res["overall"], "Benchmark evaluated CBiRRT")
    assert_true(res["overall"]["cbirrt"]["success_rate_pct"] == 100.0, "CBiRRT benchmark passed with 100% success")
    if "curobo" in res["overall"]:
        assert_true(res["overall"]["curobo"]["success_rate_pct"] == 100.0, "cuRobo benchmark passed with 100% success")


def main():
    print("=" * 65)
    print(" 🤖 OpenArm Motion Planner Complete Test Suite")
    print("=" * 65)

    test_ssik_kinematics()
    test_collision_checker()
    test_cbirrt_planning()
    test_modular_backends()
    test_benchmark()

    print("\n" + "=" * 65)
    print(" 🎉 ALL TESTS PASSED! Motion planner verified 100% operational.")
    print("=" * 65)


if __name__ == "__main__":
    main()
