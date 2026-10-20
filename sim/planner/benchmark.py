#!/usr/bin/env python3
"""Benchmark tool comparing OpenArm motion planners (CBiRRT vs cuRobo).

Measures and compares:
- Planning latency (min, max, mean, median, p95) in milliseconds
- Trajectory execution duration in seconds
- Trajectory density (waypoint count)
- Minimum collision clearance along the generated path
- Planning success rate (%)
- Relative speedup factor

All testing is run in MuJoCo simulation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
for p in (REPO, SIM_DIR, os.path.join(SIM_DIR, "ik_artifacts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import OpenArmPlanner, get_available_backends

# Canonical test poses for benchmark (in degrees)
BENCHMARK_POSES = {
    "stow": [0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0],
    "reach_forward": [0.0, -30.0, 0.0, 60.0, 0.0, 15.0, 0.0],
    "high_reach": [0.0, -50.0, 0.0, 45.0, 0.0, 20.0, 0.0],
    "zombie_reach": [25.0, -15.0, 0.0, 50.0, 0.0, 0.0, 0.0],
    "spooky_arms_up": [15.0, -60.0, 10.0, 40.0, -20.0, 30.0, 0.0],
    "pumpkin_carry": [10.0, -25.0, 0.0, 75.0, 0.0, 30.0, 0.0],
}

# Standard test motions (transitions between poses)
BENCHMARK_MOTIONS = [
    ("stow", "reach_forward", "Stow -> Reach Forward"),
    ("reach_forward", "spooky_arms_up", "Reach Forward -> Arms Up"),
    ("spooky_arms_up", "zombie_reach", "Arms Up -> Zombie Reach"),
    ("zombie_reach", "pumpkin_carry", "Zombie Reach -> Pumpkin Carry"),
    ("pumpkin_carry", "stow", "Pumpkin Carry -> Return to Stow"),
]


def _get_arm_poses(arm: str) -> dict[str, list[float]]:
    is_right = arm == "right"
    poses = {}
    for k, v in BENCHMARK_POSES.items():
        if is_right:
            poses[k] = [-v[0], -v[1], -v[2], v[3], v[4], v[5], v[6]]
        else:
            poses[k] = list(v)
    return poses


def run_benchmark(
    backends: list[str] | None = None,
    arm: str = "left",
    trials: int = 5,
    include_random: bool = False,
    random_count: int = 3,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run comprehensive motion planner benchmark suite in simulation.

    Args:
        backends: List of backend names to compare (defaults to available backends).
        arm: 'left' or 'right' arm.
        trials: Number of trials per motion per backend.
        include_random: Whether to include random valid workspace motions.
        random_count: Number of random motions if enabled.
        verbose: Print progress to console.

    Returns:
        Structured benchmark dictionary with metrics, tables, and comparisons.
    """
    avail = get_available_backends()
    if backends is None:
        backends = [b for b in ["cbirrt", "curobo"] if b in avail and avail[b].get("available", False)]
        if not backends:
            backends = ["cbirrt"]

    if verbose:
        print(f"=================================================================")
        print(f" OpenArm Motion Planner Benchmark Suite (Sim)")
        print(f" Arm: {arm.upper()} | Backends: {', '.join(backends)} | Trials: {trials}")
        print(f"=================================================================")

    # Initialize planners
    planners = {}
    for b in backends:
        if verbose:
            print(f"[Init] Initializing {b.upper()} backend for {arm} arm...")
        try:
            planners[b] = OpenArmPlanner(arm=arm, backend=b)
        except Exception as e:
            if verbose:
                print(f"[Warn] Failed to initialize backend {b}: {e}")

    active_backends = [b for b in backends if b in planners]
    if not active_backends:
        raise RuntimeError("No backends could be initialized for benchmark.")

    poses = _get_arm_poses(arm)

    # Build motion list
    motion_list: list[dict[str, Any]] = []
    for start_key, goal_key, name in BENCHMARK_MOTIONS:
        motion_list.append({
            "name": name,
            "q_start": np.radians(poses[start_key]),
            "q_goal": np.radians(poses[goal_key]),
            "type": "preset",
        })

    if include_random:
        # Generate collision-free random goals
        checker = planners[active_backends[0]].collision_checker
        rng = np.random.default_rng(42)
        q_stow = np.radians(poses["stow"])
        found = 0
        attempts = 0
        while found < random_count and attempts < 100:
            attempts += 1
            # Sample around reachable workspace limits
            sample_deg = [
                rng.uniform(-45, 45),
                rng.uniform(-60, 10),
                rng.uniform(-30, 30),
                rng.uniform(20, 90),
                rng.uniform(-45, 45),
                rng.uniform(-20, 40),
                rng.uniform(-45, 45),
            ]
            q_cand = np.radians(sample_deg)
            if checker.is_valid(q_cand):
                found += 1
                motion_list.append({
                    "name": f"Random Exploration #{found}",
                    "q_start": q_stow,
                    "q_goal": q_cand,
                    "type": "random",
                })

    # Warmup each planner once to eliminate JIT/cache overhead
    if verbose:
        print("\n[Warmup] Pre-warming backends...")
    warm_q0 = np.radians(poses["stow"])
    warm_q1 = np.radians(poses["reach_forward"])
    for b in active_backends:
        try:
            planners[b].plan_to_joint_goal(warm_q0, warm_q1, timeout=5.0)
            if verbose:
                print(f"  - {b.upper()}: warmed up")
        except Exception as e:
            if verbose:
                print(f"  - {b.upper()} warmup failed: {e}")

    # Benchmark results structure
    results_by_backend: dict[str, dict[str, Any]] = {
        b: {
            "motions": [],
            "all_latencies_ms": [],
            "all_durations_s": [],
            "all_clearances_mm": [],
            "all_waypoints": [],
            "total_attempts": 0,
            "total_successes": 0,
        }
        for b in active_backends
    }

    if verbose:
        print(f"\n[Run] Benchmarking {len(motion_list)} motion scenarios ({trials} trials each)...")

    for m_idx, motion in enumerate(motion_list, 1):
        m_name = motion["name"]
        q_s = motion["q_start"]
        q_g = motion["q_goal"]

        if verbose:
            print(f"\nMotion {m_idx}/{len(motion_list)}: {m_name}")

        for b in active_backends:
            planner = planners[b]
            latencies_ms = []
            durations_s = []
            waypoints_list = []
            clearances_mm = []
            successes = 0

            for t_idx in range(trials):
                t0 = time.perf_counter()
                try:
                    traj = planner.plan_to_joint_goal(q_s, q_g, timeout=6.0)
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    if traj is not None:
                        successes += 1
                        latencies_ms.append(dt_ms)
                        durations_s.append(float(traj.duration))
                        waypoints_list.append(int(traj.metadata.get("waypoints", len(traj.times))))
                        clearances_mm.append(float(traj.min_clearance_mm))
                    else:
                        latencies_ms.append(dt_ms)
                except Exception as ex:
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    latencies_ms.append(dt_ms)

            mean_lat = float(np.mean(latencies_ms)) if latencies_ms else 0.0
            med_lat = float(np.median(latencies_ms)) if latencies_ms else 0.0
            mean_dur = float(np.mean(durations_s)) if durations_s else 0.0
            mean_wp = float(np.mean(waypoints_list)) if waypoints_list else 0.0
            min_clr = float(np.min(clearances_mm)) if clearances_mm else 0.0
            succ_pct = (successes / trials) * 100.0

            motion_summary = {
                "motion": m_name,
                "trials": trials,
                "successes": successes,
                "success_rate_pct": succ_pct,
                "latency_mean_ms": round(mean_lat, 2),
                "latency_median_ms": round(med_lat, 2),
                "latency_min_ms": round(float(np.min(latencies_ms)), 2) if latencies_ms else 0.0,
                "latency_max_ms": round(float(np.max(latencies_ms)), 2) if latencies_ms else 0.0,
                "duration_mean_s": round(mean_dur, 2),
                "waypoints_mean": round(mean_wp, 1),
                "min_clearance_mm": round(min_clr, 1),
            }

            results_by_backend[b]["motions"].append(motion_summary)
            results_by_backend[b]["all_latencies_ms"].extend(latencies_ms)
            results_by_backend[b]["all_durations_s"].extend(durations_s)
            results_by_backend[b]["all_clearances_mm"].extend(clearances_mm)
            results_by_backend[b]["all_waypoints"].extend(waypoints_list)
            results_by_backend[b]["total_attempts"] += trials
            results_by_backend[b]["total_successes"] += successes

            if verbose:
                print(
                    f"  - {b:<8}: {mean_lat:6.1f} ms avg | {mean_dur:4.2f} s path | "
                    f"{mean_wp:3.0f} pts | min clr {min_clr:4.1f} mm | {succ_pct:5.1f}% succ"
                )

    # Compute overall aggregates per backend
    overall: dict[str, dict[str, Any]] = {}
    for b in active_backends:
        data = results_by_backend[b]
        lats = data["all_latencies_ms"]
        durs = data["all_durations_s"]
        clrs = data["all_clearances_mm"]
        wps = data["all_waypoints"]
        attempts = data["total_attempts"]
        succs = data["total_successes"]

        overall[b] = {
            "success_rate_pct": round((succs / attempts) * 100.0, 1) if attempts > 0 else 0.0,
            "total_attempts": attempts,
            "total_successes": succs,
            "latency_mean_ms": round(float(np.mean(lats)), 2) if lats else 0.0,
            "latency_median_ms": round(float(np.median(lats)), 2) if lats else 0.0,
            "latency_p95_ms": round(float(np.percentile(lats, 95)), 2) if lats else 0.0,
            "latency_min_ms": round(float(np.min(lats)), 2) if lats else 0.0,
            "latency_max_ms": round(float(np.max(lats)), 2) if lats else 0.0,
            "duration_mean_s": round(float(np.mean(durs)), 2) if durs else 0.0,
            "waypoints_mean": round(float(np.mean(wps)), 1) if wps else 0.0,
            "min_clearance_mm": round(float(np.min(clrs)), 1) if clrs else 0.0,
        }

    # Relative comparison (speedup)
    comparison: dict[str, Any] = {}
    if "cbirrt" in overall and "curobo" in overall:
        cb_lat = overall["cbirrt"]["latency_mean_ms"]
        cu_lat = overall["curobo"]["latency_mean_ms"]
        speedup = (cb_lat / cu_lat) if cu_lat > 0 else 1.0
        comparison = {
            "baseline": "cbirrt",
            "evaluated": "curobo",
            "speedup_factor": round(speedup, 2),
            "faster_backend": "curobo" if speedup >= 1.0 else "cbirrt",
            "latency_delta_ms": round(cb_lat - cu_lat, 2),
            "summary_text": (
                f"cuRobo is {speedup:.1f}x faster on average than CBiRRT "
                f"({cu_lat:.1f} ms vs {cb_lat:.1f} ms)"
            ),
        }

    out = {
        "arm": arm,
        "trials_per_motion": trials,
        "backends": active_backends,
        "motion_count": len(motion_list),
        "overall": overall,
        "comparison": comparison,
        "motions": {b: results_by_backend[b]["motions"] for b in active_backends},
    }

    if verbose:
        print_ascii_table(out)

    return out


def print_ascii_table(data: dict[str, Any]) -> None:
    """Print clean, professional ASCII comparison table."""
    backends = data["backends"]
    overall = data["overall"]
    comp = data.get("comparison", {})

    print("\n" + "=" * 78)
    print(f" SUMMARY: OpenArm Planner Performance Comparison ({data['arm'].upper()} ARM)")
    print("=" * 78)

    hdr = f"{'Metric':<28}"
    for b in backends:
        hdr += f" | {b.upper():<20}"
    print(hdr)
    print("-" * 78)

    metrics = [
        ("Success Rate", "%", lambda o: f"{o['success_rate_pct']:.1f}% ({o['total_successes']}/{o['total_attempts']})"),
        ("Mean Latency", "ms", lambda o: f"{o['latency_mean_ms']:.1f} ms"),
        ("Median Latency", "ms", lambda o: f"{o['latency_median_ms']:.1f} ms"),
        ("P95 Latency", "ms", lambda o: f"{o['latency_p95_ms']:.1f} ms"),
        ("Min / Max Latency", "ms", lambda o: f"{o['latency_min_ms']:.1f} / {o['latency_max_ms']:.1f} ms"),
        ("Mean Traj Duration", "s", lambda o: f"{o['duration_mean_s']:.2f} s"),
        ("Mean Waypoints", "pts", lambda o: f"{o['waypoints_mean']:.0f}"),
        ("Min Path Clearance", "mm", lambda o: f"{o['min_clearance_mm']:.1f} mm"),
    ]

    for label, unit, fn in metrics:
        row = f"{label:<28}"
        for b in backends:
            val_str = fn(overall[b]) if b in overall else "N/A"
            row += f" | {val_str:<20}"
        print(row)

    print("=" * 78)
    if comp:
        print(f" >> SPEEDUP: {comp.get('summary_text')}")
        print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="OpenArm Motion Planner Benchmark Tool")
    parser.add_argument("--arm", choices=["left", "right"], default="left", help="Arm to test (default: left)")
    parser.add_argument("--backends", type=str, default="cbirrt,curobo", help="Comma-separated backends (default: cbirrt,curobo)")
    parser.add_argument("--trials", "-n", type=int, default=5, help="Number of trials per motion (default: 5)")
    parser.add_argument("--include-random", action="store_true", help="Include random reachable goal evaluations")
    parser.add_argument("--random-count", type=int, default=3, help="Number of random goals (default: 3)")
    parser.add_argument("--json-out", type=str, default=None, help="Optional path to write results as JSON")
    args = parser.parse_args()

    backend_list = [b.strip() for b in args.backends.split(",") if b.strip()]
    results = run_benchmark(
        backends=backend_list,
        arm=args.arm,
        trials=args.trials,
        include_random=args.include_random,
        random_count=args.random_count,
        verbose=True,
    )

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n[Saved] Detailed JSON benchmark results written to: {args.json_out}")


if __name__ == "__main__":
    main()
