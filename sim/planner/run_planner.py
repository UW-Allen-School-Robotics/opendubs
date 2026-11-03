#!/usr/bin/env python3
"""CLI runner to plan, test, and visually inspect OpenArm motions in simulation."""
from __future__ import annotations

import argparse
import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
for p in (REPO, SIM_DIR, os.path.join(SIM_DIR, "ik_artifacts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import OpenArmPlanner
import openarm_left_ik
import openarm_right_ik

PRESETS = {
    "zero": np.zeros(7),
    "stow": np.radians([0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0]),
    "reach_forward": np.radians([0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0]),
    "high_reach": np.radians([0.0, -50.0, 0.0, 45.0, 0.0, 20.0, 0.0]),
    "wave_ready": np.radians([20.0, -20.0, 30.0, 70.0, -10.0, 20.0, 0.0]),
}


def main():
    parser = argparse.ArgumentParser(description="Test OpenArm motion planning in simulation.")
    parser.add_argument("--arm", choices=["left", "right"], default="left", help="Which arm to plan for (default: left)")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), default=None, help="Target pose preset")
    parser.add_argument("--joints", type=str, default=None, help="Comma-separated target joint angles in degrees, e.g. '0,-30,0,60,0,15,0'")
    parser.add_argument("--cartesian", type=str, default=None, help="Target position 'x,y,z' in meters relative to arm base")
    parser.add_argument("--start", choices=list(PRESETS.keys()), default="zero", help="Start pose preset (default: zero)")
    parser.add_argument("--view", action="store_true", help="Open MuJoCo 3D interactive viewer to watch playback")
    parser.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier for 3D viewer (default: 1.0)")
    parser.add_argument("--backend", default="cbirrt", help="Motion planner backend (e.g. 'cbirrt', 'curobo') (default: cbirrt)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Planning timeout in seconds (default: 5.0)")
    args = parser.parse_args()

    print(f"=== OpenArm Motion Planning (Sim) ===")
    print(f"Arm: {args.arm.upper()} | Backend: {args.backend}")
    planner = OpenArmPlanner(arm=args.arm, backend=args.backend)

    q_start = PRESETS[args.start]
    print(f"Start configuration ({args.start}): {np.round(np.degrees(q_start), 1)} deg")

    traj = None
    if args.cartesian is not None:
        xyz = [float(x.strip()) for x in args.cartesian.split(",")]
        print(f"Target Cartesian position (link0 frame): {xyz} m")
        ik_mod = openarm_left_ik if args.arm == "left" else openarm_right_ik
        # Default orientation matching forward reach
        T_target = ik_mod.fk(PRESETS["reach_forward"])
        T_target[:3, 3] = xyz
        traj = planner.plan_to_cartesian_pose(q_start, T_target, timeout=args.timeout)
    else:
        if args.joints is not None:
            q_goal = np.radians([float(x.strip()) for x in args.joints.split(",")])
        elif args.preset is not None:
            q_goal = PRESETS[args.preset]
        else:
            q_goal = PRESETS["reach_forward"]
            
        print(f"Target joint configuration: {np.round(np.degrees(q_goal), 1)} deg")
        traj = planner.plan_to_joint_goal(q_start, q_goal, timeout=args.timeout)

    if traj is None:
        print("\n[!] Planning FAILED (infeasible, collision, or timeout).")
        sys.exit(1)

    print("\n[✓] Planning SUCCEEDED!")
    print(f"    - Waypoints:       {traj.metadata.get('waypoints')}")
    print(f"    - Plan time:       {traj.metadata.get('plan_time_ms', 0):.1f} ms")
    if "ik_solve_ms" in traj.metadata:
        print(f"    - IK solve time:   {traj.metadata['ik_solve_ms']:.2f} ms ({traj.metadata['ik_candidate_count']} solutions)")
    print(f"    - Duration:        {traj.duration:.2f} s")
    print(f"    - Min clearance:   {traj.min_clearance_mm:.1f} mm")

    if args.view:
        planner.visualize_trajectory(traj, playback_speed=args.speed)
    else:
        print("\nTip: Add '--view' to watch the trajectory in the MuJoCo 3D viewer.")


if __name__ == "__main__":
    main()
