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
from sim.planner.hardware_bridge import HARDWARE_BRIDGE, validate_trajectory_safety
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


GLOBAL_STUDIO_URL = None


def get_studio_url() -> str:
    global GLOBAL_STUDIO_URL
    if GLOBAL_STUDIO_URL is not None:
        return GLOBAL_STUDIO_URL

    import threading
    from sim.planner.ui.server import HTTPServer, StudioHandler, SERVICE

    for candidate_port in [8092, 8095]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{candidate_port}/api/status", timeout=1.0) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "ready":
                    GLOBAL_STUDIO_URL = f"http://127.0.0.1:{candidate_port}"
                    return GLOBAL_STUDIO_URL
        except Exception:
            pass

    SERVICE.set_backend("cbirrt")
    server = None
    for port in [8095, 8092, 0]:
        try:
            server = HTTPServer(("127.0.0.1", port), StudioHandler)
            actual_port = server.server_address[1]
            GLOBAL_STUDIO_URL = f"http://127.0.0.1:{actual_port}"
            break
        except OSError:
            continue
    if server is not None:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.5)

    return GLOBAL_STUDIO_URL or "http://127.0.0.1:8092"


def test_studio_rest_api():
    test_section("5. Studio HTTP Server & REST API Endpoints")
    base_url = get_studio_url()

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


def test_hardware_bridge_and_hitl():
    test_section("6. Hardware Safety Preflight, HITL Loopback & Hardware APIs")

    planner = OpenArmPlanner(arm="left", v_max=1.2, a_max=2.5)
    q_start = np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0])
    q_goal = np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0])

    traj = planner.plan_to_joint_goal(q_start, q_goal)
    assert_true(traj is not None, "Generated baseline trajectory for hardware validation")

    # 1. Direct Preflight Validation (Safe Trajectory)
    safety_pass = validate_trajectory_safety(
        arm="left",
        times=traj.times.tolist(),
        positions_rad=traj.positions,
        velocities_rad_s=traj.velocities,
        min_clearance_mm=traj.min_clearance_mm,
    )
    assert_true(safety_pass.ok is True, "Preflight safety validation PASSED for safe trajectory")
    assert_true(safety_pass.hand_box_ok is True, f"Fingertip TCP inside HAND_BOX (z: {safety_pass.tcp_min_z_m:.3f}..{safety_pass.tcp_max_z_m:.3f} m)")
    assert_true(safety_pass.speed_trip_ok is True, "Joint speeds well below hardware trip caps (TRIP_DEG_S)")

    # 2. Preflight Safety Failure Injection (Excessive Velocity Trip)
    bad_times = [0.0, 0.01]  # 30 degrees in 10 ms = 3000 deg/s! Exceeds any motor trip
    bad_pos = np.array([q_start, q_goal])
    safety_fail = validate_trajectory_safety("left", bad_times, bad_pos)
    assert_true(safety_fail.ok is False, "Safety validator caught velocity trip violation")
    assert_true(safety_fail.speed_trip_ok is False, "Speed trip flag marked False on excessive speed")

    # 3. REST API /api/hardware/status
    base_url = get_studio_url()
    req_status = urllib.request.Request(f"{base_url}/api/hardware/status")
    with urllib.request.urlopen(req_status, timeout=5) as resp:
        hw_status = json.loads(resp.read().decode())
    assert_true("environment" in hw_status, "/api/hardware/status returned environment detection")
    assert_true(hw_status.get("environment", {}).get("hitl_available") is True, "HITL simulation available")

    # 4. REST API /api/hardware/preflight
    req_pref = urllib.request.Request(
        f"{base_url}/api/hardware/preflight",
        data=json.dumps({
            "arm": "left",
            "times": traj.times.tolist(),
            "positions_rad": traj.positions.tolist(),
            "velocities_rad_s": traj.velocities.tolist(),
            "min_clearance_mm": traj.min_clearance_mm,
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_pref, timeout=5) as resp:
        pref_res = json.loads(resp.read().decode())
    assert_true(pref_res.get("ok") is True, "/api/hardware/preflight passed trajectory verification")

    # 5. REST API /api/hardware/execute (HITL mode)
    req_exec = urllib.request.Request(
        f"{base_url}/api/hardware/execute",
        data=json.dumps({
            "arm": "left",
            "mode": "hitl",
            "trajectory": {
                "times": traj.times.tolist(),
                "positions_rad": traj.positions.tolist(),
                "velocities_rad_s": traj.velocities.tolist(),
                "min_clearance_mm": traj.min_clearance_mm,
            },
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_exec, timeout=5) as resp:
        exec_res = json.loads(resp.read().decode())
    assert_true(exec_res.get("success") is True, f"/api/hardware/execute started HITL goal: {exec_res.get('goal_id')}")

    # Wait for execution completion
    time.sleep(0.5)
    with urllib.request.urlopen(f"{base_url}/api/hardware/status", timeout=5) as resp:
        active_stat = json.loads(resp.read().decode())
    assert_true(active_stat.get("is_executing") or active_stat.get("status") in ("executing", "succeeded"),
                f"Execution in progress or finished (status: {active_stat.get('status')}, progress: {active_stat.get('progress_pct')}%)")

    # Wait for completion via HTTP status
    final_status = None
    for _ in range(60):
        with urllib.request.urlopen(f"{base_url}/api/hardware/status", timeout=5) as resp:
            active_stat = json.loads(resp.read().decode())
        final_status = active_stat.get("status")
        if not active_stat.get("is_executing") and final_status in ("succeeded", "idle"):
            break
        time.sleep(0.1)

    assert_true(final_status == "succeeded", f"HITL trajectory execution finished with status '{final_status}'")

    # 6. E-Stop / Stop API verification
    req_stop = urllib.request.Request(
        f"{base_url}/api/hardware/stop",
        data=json.dumps({}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_stop, timeout=5) as resp:
        stop_res = json.loads(resp.read().decode())
    assert_true(stop_res.get("success") is True, "/api/hardware/stop returned success")


def test_choreo_import_export():
    print("\n" + "=" * 65)
    print("▶ 7. Choreo Library, Import/Export, and Persistence Manager")
    print("=" * 65)

    base_url = get_studio_url()

    # 1. Test /api/choreo/library
    with urllib.request.urlopen(f"{base_url}/api/choreo/library", timeout=5) as resp:
        lib_data = json.loads(resp.read().decode())
    assert_true(lib_data.get("success") is True, "/api/choreo/library returned success")
    choreos = lib_data.get("choreos", [])
    assert_true(len(choreos) > 0, f"Discovered {len(choreos)} choreos in library")
    has_halloween = any("halloween.yaml" in c.get("filename", "") for c in choreos)
    assert_true(has_halloween, "halloween.yaml found in choreo library")

    # 2. Test /api/choreo/load for builtin:halloween.yaml (Left arm)
    with urllib.request.urlopen(f"{base_url}/api/choreo/load?id=builtin:halloween.yaml&arm=left", timeout=5) as resp:
        load_data = json.loads(resp.read().decode())
    assert_true(load_data.get("success") is True, "Loaded builtin:halloween.yaml successfully")
    kfs = load_data.get("keyframes", [])
    assert_true(len(kfs) >= 2, f"Loaded {len(kfs)} keyframes for left arm")
    assert_true(all(kf["arm"] == "left" for kf in kfs), "All keyframes scoped to left arm")

    # 3. Test /api/choreo/import with raw YAML content
    test_yaml = """
id: unit_test_routine
cues:
  - { t: 1.0, slot: ARM_L, joints: { 1: 15, 2: -20, 4: 60 } }
  - { t: 4.5, slot: ARM_L, joints: { 1: -25, 4: 75 } }
"""
    req_import_yaml = urllib.request.Request(
        f"{base_url}/api/choreo/import",
        data=json.dumps({"content": test_yaml, "arm": "left"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_import_yaml, timeout=5) as resp:
        import_res = json.loads(resp.read().decode())
    assert_true(import_res.get("success") is True, "Imported YAML choreo successfully")
    assert_true(len(import_res.get("keyframes", [])) == 2, "Parsed exactly 2 keyframes from YAML")

    # 4. Test /api/choreo/save to user library
    test_kfs = import_res["keyframes"]
    req_save = urllib.request.Request(
        f"{base_url}/api/choreo/save",
        data=json.dumps({"name": "ci_test_routine", "format": "yaml", "keyframes": test_kfs}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_save, timeout=5) as resp:
        save_res = json.loads(resp.read().decode())
    assert_true(save_res.get("success") is True, "Saved user routine to sim/planner/saved_choreos/")
    saved_fname = save_res["filename"]

    # 5. Test loading saved user choreo
    with urllib.request.urlopen(f"{base_url}/api/choreo/load?id=user:{saved_fname}&arm=left", timeout=5) as resp:
        user_load = json.loads(resp.read().decode())
    assert_true(user_load.get("success") is True, "Loaded user saved choreo successfully")
    assert_true(len(user_load.get("keyframes", [])) == 2, "User saved choreo contains 2 keyframes")

    # 6. Test cleanup / deletion of user choreo
    req_del = urllib.request.Request(
        f"{base_url}/api/choreo/delete",
        data=json.dumps({"name": saved_fname}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_del, timeout=5) as resp:
        del_res = json.loads(resp.read().decode())
    assert_true(del_res.get("success") is True, "Deleted temporary test choreo")


def test_gripper_control():
    print("\n" + "=" * 65)
    print("▶ 8. Gripper Controls, Action Dispatches, and Show Cues")
    print("=" * 65)

    base_url = get_studio_url()

    # 1. Test GET /api/gripper
    with urllib.request.urlopen(f"{base_url}/api/gripper?arm=left", timeout=5) as resp:
        grip_data = json.loads(resp.read().decode())
    assert_true("open_fraction" in grip_data, "GET /api/gripper returned open_fraction")
    assert_true("position_m" in grip_data, "GET /api/gripper returned position_m")

    # 2. Test POST /api/gripper close
    req_close = urllib.request.Request(
        f"{base_url}/api/gripper",
        data=json.dumps({"arm": "left", "action": "close", "mode": "hitl"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_close, timeout=5) as resp:
        close_res = json.loads(resp.read().decode())
    assert_true(close_res.get("success") is True, "POST /api/gripper close succeeded")
    assert_true(close_res.get("open_fraction") == 0.0, "Gripper open_fraction marked 0.0 (closed)")
    assert_true(close_res.get("position_m") == 0.0, "Gripper position marked 0.0 m")

    # 3. Test POST /api/gripper open
    req_open = urllib.request.Request(
        f"{base_url}/api/gripper",
        data=json.dumps({"arm": "left", "action": "open", "mode": "hitl"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_open, timeout=5) as resp:
        open_res = json.loads(resp.read().decode())
    assert_true(open_res.get("success") is True, "POST /api/gripper open succeeded")
    assert_true(open_res.get("open_fraction") == 1.0, "Gripper open_fraction marked 1.0 (open)")
    assert_true(open_res.get("position_m") == 0.044, "Gripper position marked 0.044 m (44mm stroke)")

    # 4. Test Export Show with GRIP cues
    test_kfs = [
        {"name": "Wave 1", "arm": "left", "time_s": 1.0, "joints_deg": [0, -20, 0, 95, 0, 15, 0], "gripper": "open"},
        {"name": "Grip 1", "arm": "left", "time_s": 3.0, "joints_deg": [0, -20, 0, 95, 0, 15, 0], "gripper": "close"},
    ]
    req_export = urllib.request.Request(
        f"{base_url}/api/export_show",
        data=json.dumps({"keyframes": test_kfs}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_export, timeout=5) as resp:
        yaml_text = resp.read().decode()
    assert_true("slot: GRIP_L, do: open" in yaml_text, "YAML contains GRIP_L open cue")
    assert_true("slot: GRIP_L, do: close" in yaml_text, "YAML contains GRIP_L close cue")
    assert_true("GRIP_L" in yaml_text and "GRIP_R" in yaml_text, "YAML declares GRIP slots")


def main():
    print("=" * 65)
    print(" 🤖 OpenArm Motion Planner Complete Test Suite")
    print("=" * 65)

    test_ssik_kinematics()
    test_collision_checker()
    test_cbirrt_planning()
    test_modular_backends()
    test_studio_rest_api()
    test_hardware_bridge_and_hitl()
    test_choreo_import_export()
    test_gripper_control()

    print("\n" + "=" * 65)
    print(" 🎉 ALL TESTS PASSED! Motion planner & studio verified 100% operational.")
    print("=" * 65)


if __name__ == "__main__":
    main()

