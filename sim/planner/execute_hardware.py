#!/usr/bin/env python3
"""CLI utility to plan, validate, and execute trajectories on physical OpenArm hardware or HITL.

Usage:
    # Validate and dry-run (HITL) motion to a preset pose
    python -m sim.planner.execute_hardware --arm left --goal reach_forward --hitl

    # Execute planned trajectory on live robot hardware
    python -m sim.planner.execute_hardware --arm left --goal reach_forward --live

    # Execute custom sequence from choreo YAML show
    python -m sim.planner.execute_hardware --show shows/hello.yaml --live
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)

for p in (REPO, SIM_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import OpenArmPlanner
from sim.planner.hardware_bridge import HARDWARE_BRIDGE, validate_trajectory_safety
from sim.planner.ui.server import PRESETS_DEG, get_preset_deg


def main():
    parser = argparse.ArgumentParser(description="OpenArm Physical Robot & HITL Trajectory Execution Tool")
    parser.add_argument("--arm", choices=["left", "right"], default="left", help="Arm side (left or right)")
    parser.add_argument("--start", default="stow", help="Start pose preset (default: stow)")
    parser.add_argument("--goal", default="reach_forward", help="Goal pose preset (default: reach_forward)")
    parser.add_argument("--backend", default="cbirrt", help="Planner backend (cbirrt or curobo)")
    parser.add_argument("--v_max", type=float, default=1.2, help="Max velocity rad/s")
    parser.add_argument("--a_max", type=float, default=2.5, help="Max acceleration rad/s^2")
    parser.add_argument("--hitl", action="store_true", help="Run in HITL simulated loopback mode")
    parser.add_argument("--live", action="store_true", help="Execute on physical robot hardware")
    args = parser.parse_args()

    arm = args.arm
    if args.live:
        print("ERROR: Direct hardware execution is disabled on this branch.")
        print("All physical arm motion must go through the choreography runner (software/choreo/runner.py).")
        sys.exit(1)
    mode = "hitl"

    print("=" * 65)
    print(f" 🚀 OpenArm Hardware Trajectory Execution Utility ({arm.upper()} ARM)")
    print("=" * 65)

    # Detect hardware environment
    env = HARDWARE_BRIDGE.detect_environment()
    print(f" Environment Detection:")
    print(f"  • HITL Simulation Available:  {env['hitl_available']}")
    print(f"  • Docker OpenArm Container:   {env['docker_running']} (available: {env['docker_available']})")
    print(f"  • ROS 2 rclpy / control_msgs: {env['ros2_rclpy_available']} / {env['ros2_control_msgs_available']}")
    print(f"  • Selected Target Mode:       {mode.upper()}")
    print("-" * 65)

    # Resolve poses
    q_start_deg = get_preset_deg(args.start, arm)
    q_goal_deg = get_preset_deg(args.goal, arm)
    q_start_rad = np.radians(q_start_deg)
    q_goal_rad = np.radians(q_goal_deg)

    print(f" Start Pose ({args.start}): {[round(v, 1) for v in q_start_deg]}°")
    print(f" Goal Pose  ({args.goal}):  {[round(v, 1) for v in q_goal_deg]}°")
    print(f" Planning collision-free trajectory with backend '{args.backend}'...")

    planner = OpenArmPlanner(arm=arm, backend=args.backend, v_max=args.v_max, a_max=args.a_max)
    t0 = time.perf_counter()
    traj = planner.plan_to_joint_goal(q_start_rad, q_goal_rad)
    dt_ms = (time.perf_counter() - t0) * 1000.0

    if traj is None:
        print(f"❌ Planning failed: could not find collision-free path within timeout.")
        sys.exit(1)

    print(f"✅ Planned in {dt_ms:.1f} ms: {len(traj.positions)} waypoints, duration: {traj.duration:.2f} s")

    # Run safety checks
    print("\n🔍 Running Preflight Safety Validation:")
    safety = validate_trajectory_safety(
        arm=arm,
        times=traj.times.tolist(),
        positions_rad=traj.positions,
        velocities_rad_s=traj.velocities,
        min_clearance_mm=traj.min_clearance_mm,
    )
    print(f"  • Self-Collision Clearance: {safety.min_clearance_mm:.1f} mm (>= 15.0 mm: {'PASS' if safety.min_clearance_mm >= 15.0 else 'FAIL'})")
    print(f"  • Joint Limits Compliance:   {'PASS' if safety.joint_limits_ok else 'FAIL'}")
    print(f"  • Velocity Trip Caps:        {'PASS' if safety.speed_trip_ok else 'FAIL'}")
    print(f"  • HAND_BOX Workspace Guard:  {'PASS' if safety.hand_box_ok else 'FAIL'} (z: {safety.tcp_min_z_m:.3f}..{safety.tcp_max_z_m:.3f} m)")

    if not safety.ok:
        print("\n❌ SAFETY CHECK FAILED! Refusing hardware execution:")
        for prob in safety.problems:
            print(f"   - {prob}")
        sys.exit(1)

    if safety.warnings:
        print("\n⚠️  Warnings:")
        for w in safety.warnings:
            print(f"   - {w}")

    # Execution
    print(f"\n▶ Starting Execution in [{mode.upper()}] mode...")
    traj_dict = {
        "times": traj.times.tolist(),
        "positions_rad": traj.positions.tolist(),
        "velocities_rad_s": traj.velocities.tolist(),
        "min_clearance_mm": traj.min_clearance_mm,
    }

    last_pct = [0.0]
    def progress_callback(cur_t, pct):
        if pct - last_pct[0] >= 5.0 or pct >= 100.0:
            last_pct[0] = pct
            bar_len = 30
            filled = int(round(bar_len * pct / 100.0))
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  [{bar}] {pct:5.1f}% | t = {cur_t:4.2f}s / {traj.duration:4.2f}s", end="", flush=True)

    res = HARDWARE_BRIDGE.execute_trajectory(arm=arm, trajectory=traj_dict, mode=mode, progress_cb=progress_callback)
    if not res.get("success"):
        print(f"\n❌ Execution failed: {res.get('error')}")
        sys.exit(1)

    # Wait for execution to finish
    while HARDWARE_BRIDGE.is_executing:
        time.sleep(0.05)

    final_status = HARDWARE_BRIDGE.get_status()
    print()
    if final_status["status"] == "succeeded":
        print(f"\n🎉 Trajectory execution SUCCEEDED on {arm.upper()} arm!")
    else:
        print(f"\n⚠️  Execution ended with status: {final_status['status']}. Error: {final_status.get('last_error')}")


if __name__ == "__main__":
    main()
