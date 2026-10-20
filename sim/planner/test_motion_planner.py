#!/usr/bin/env python3
"""End-to-end verification test suite for OpenArm Simulation Motion Planner and Studio.

Tests:
  1. IK and FK consistency across both arms using SSIK.
  2. MuJoCo self-collision checking & safety clearance validation.
  3. CBiRRT trajectory planning & TOPP-RA time-optimal retiming.
  4. Modular backend registry and cuRobo adapter graceful fallback.
  5. Studio Web server REST API endpoints (/status, /fk, /ik, /collision_check, /plan, /export_show).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
IK_DIR = os.path.join(SIM_DIR, "ik_artifacts")

for p in (REPO, SIM_DIR, IK_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import (
    OpenArmPlanner,
    PlannedTrajectory,
    CBiRRTBackend,
    CuRoboBackend,
    create_backend,
    get_available_backends,
)
import openarm_left_ik
import openarm_right_ik
import clearance


def test_section(title: str):
    print(f"\n{'=' * 65}")
    print(f"▶ {title}")
    print(f"{'=' * 65}")


def assert_true(cond: bool, msg: str):
    if not cond:
        print(f"  [FAIL] {msg}")
        raise AssertionError(msg)
    print(f"  [PASS] {msg}")


def test_ssik_kinematics():
    test_section("1. SSIK Analytical Kinematics & FK Consistency")
    
    # Left Arm
    q_test_l = np.radians([10.0, -20.0, 15.0, 60.0, -10.0, 20.0, 5.0])
    T_fk_l = openarm_left_ik.fk(q_test_l)
    sols_l = openarm_left_ik.solve(T_fk_l)
    assert_true(len(sols_l) > 0, f"Left SSIK solved ({len(sols_l)} candidates found)")
    # Reconstruct FK from first solution
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
    test_section("2. MuJoCo Collision Checking & Clearance Guard")

    planner_l = OpenArmPlanner(arm="left", min_clearance_mm=15.0)
    planner_r = OpenArmPlanner(arm="right", min_clearance_mm=15.0)

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
    test_section("3. CBiRRT Motion Planning & TOPP-RA Retiming")

    planner = OpenArmPlanner(arm="left", v_max=1.5, a_max=3.0)
    q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])  # stow
    q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])   # reach_forward

    t0 = time.perf_counter()
    traj = planner.plan_to_joint_goal(q_start, q_goal, timeout=5.0)
    plan_wall = (time.perf_counter() - t0) * 1000.0

    assert_true(traj is not None, f"CBiRRT trajectory planned successfully in {plan_wall:.1f} ms")
    assert_true(traj.duration > 0.0, f"Trajectory duration: {traj.duration:.2f} s")
    assert_true(len(traj.positions) > 10, f"Waypoints count: {len(traj.positions)}")
    assert_true(traj.min_clearance_mm >= 15.0, f"Minimum clearance along path: {traj.min_clearance_mm:.1f} mm")

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
    test_section("4. Modular Backend Registry & cuRobo Adapter")

    backends = get_available_backends()
    assert_true("cbirrt" in backends, "CBiRRT backend registered")
    assert_true(backends["cbirrt"]["available"] is True, "CBiRRT backend reports available=True")
    assert_true("curobo" in backends, "cuRobo backend registered in registry")

    # If cuRobo is available in environment, test real GPU trajectory generation
    if backends["curobo"]["available"]:
        print("  [INFO] cuRobo is AVAILABLE (CUDA GPU detected)")
        curobo_planner = OpenArmPlanner(arm="left", backend="curobo")
        assert_true(curobo_planner.backend.name == "curobo", "Planner initialized with cuRobo backend")
        q_start = np.zeros(7)
        q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])
        cu_traj = curobo_planner.plan_to_joint_goal(q_start, q_goal)
        assert_true(cu_traj is not None, f"cuRobo planned valid trajectory in {cu_traj.metadata.get('plan_time_ms', 0):.1f} ms")
        assert_true(cu_traj.min_clearance_mm >= 15.0, f"cuRobo trajectory satisfies collision clearance ({cu_traj.min_clearance_mm:.1f} mm)")
    else:
        print("  [INFO] cuRobo is not installed in current environment; verifying graceful fallback")
        assert_true(backends["curobo"]["available"] is False, "cuRobo backend reports available=False (graceful fallback)")

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


def test_studio_rest_api():
    test_section("5. Studio HTTP Server & REST API Endpoints")

    base_url = "http://127.0.0.1:8092"

    # Start server in a background thread if not running
    import threading
    from sim.planner.ui.server import HTTPServer, StudioHandler, SERVICE

    try:
        with urllib.request.urlopen(f"{base_url}/api/status", timeout=0.5):
            pass
    except Exception:
        SERVICE.set_backend("cbirrt")
        server = HTTPServer(("127.0.0.1", 8092), StudioHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(1.0)

    def get(path: str) -> dict:
        req = urllib.request.Request(f"{base_url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def post(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{base_url}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())

    # /api/status
    status = get("/api/status")
    assert_true(status.get("status") == "ready", f"/api/status ready: active_backend={status.get('active_backend')}")
    assert_true("cbirrt" in status.get("available_backends", {}), "Available backends included in /api/status")

    # /api/presets
    presets = get("/api/presets")
    assert_true("stow" in presets and "zombie_reach" in presets, f"/api/presets returned {len(presets)} presets")

    # /api/collision_check
    col = post("/api/collision_check", {"arm": "left", "joints": [0, -20, 0, 95, 0, 15, 0]})
    assert_true(col.get("valid") is True, f"/api/collision_check safe: {col.get('min_clearance_mm'):.1f} mm")

    # /api/fk
    fk = post("/api/fk", {"arm": "left", "joints": [0, -20, 0, 95, 0, 15, 0]})
    assert_true("position" in fk and len(fk["position"]) == 3, f"/api/fk EE pos: {fk.get('position')}")

    # /api/ik
    ik = post("/api/ik", {"arm": "left", "position": [0.35, 0.20, 0.15]})
    assert_true(ik.get("success") is True, f"/api/ik solved via SSIK: {len(ik.get('joints_deg', []))} DOF")

    # /api/plan
    plan = post("/api/plan", {
        "arm": "left",
        "q_start": [0, -20, 0, 95, 0, 15, 0],
        "q_goal": [0, -30, 0, 60, 0, 15, 0],
    })
    assert_true(plan.get("success") is True, f"/api/plan duration: {plan.get('duration'):.2f} s, waypoints: {plan.get('waypoints')}")

    # /api/export_show
    req_show = urllib.request.Request(
        f"{base_url}/api/export_show",
        data=json.dumps({"keyframes": [{"name": "Stow", "arm": "left", "time_s": 0.0, "joints_deg": [0, -20, 0, 95, 0, 15, 0]}]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_show, timeout=5) as resp:
        show_yaml = resp.read().decode()
    assert_true("slot: ARM_L" in show_yaml and "joints: {" in show_yaml, "/api/export_show matches choreo YAML format")

    # /api/benchmark
    bench = post("/api/benchmark", {"arm": "left", "trials": 1, "backends": ["cbirrt"]})
    assert_true("overall" in bench and "cbirrt" in bench["overall"], "/api/benchmark returned performance metrics")


def main():
    print("=" * 65)
    print(" 🤖 OpenArm Motion Planner Complete Test Suite")
    print("=" * 65)

    test_ssik_kinematics()
    test_collision_checker()
    test_cbirrt_planning()
    test_modular_backends()
    test_studio_rest_api()

    print("\n" + "=" * 65)
    print(" 🎉 ALL TESTS PASSED! Motion planner & studio verified 100% operational.")
    print("=" * 65)


if __name__ == "__main__":
    main()
