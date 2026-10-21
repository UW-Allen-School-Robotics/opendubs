"""Bridge between Choreography Composer and OpenArm Motion Planner (sim.planner).

Provides multi-backend planning choice for arm moves:
- 'spline': Default direct cubic Hermite spline interpolation.
- 'cbirrt': Siddh's SSIK analytical kinematics + MuJoCo clearance search (CPU).
- 'curobo': NVIDIA cuRobo GPU trajectory generator & collision optimizer (CUDA).
"""
from __future__ import annotations

import os
import sys
import math
import copy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CHOREO_DIR = os.path.dirname(HERE)
SOFTWARE_DIR = os.path.dirname(CHOREO_DIR)
REPO_DIR = os.path.dirname(SOFTWARE_DIR)

for p in (HERE, REPO_DIR, CHOREO_DIR, os.path.join(REPO_DIR, "sim")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cue
import ik
import yamledit


def get_available_planners() -> dict:
    """Return dict of available motion planner backends."""
    planners = {
        "spline": {
            "id": "spline",
            "name": "Spline (Default)",
            "description": "Monotone cubic Hermite spline interpolation (direct keyframe move)",
            "available": True,
            "badge": "DEFAULT",
        },
        "cbirrt": {
            "id": "cbirrt",
            "name": "CBiRRT (SSIK + Clearance)",
            "description": "Siddh SSIK analytical kinematics + MuJoCo collision checking (CPU)",
            "available": False,
            "badge": "CPU RRT",
        },
        "curobo": {
            "id": "curobo",
            "name": "cuRobo (GPU Tensor)",
            "description": "NVIDIA cuRobo collision optimizer & trajectory generator (CUDA)",
            "available": False,
            "badge": "GPU TENSOR",
        },
    }

    try:
        from sim.planner import get_available_backends
        backends = get_available_backends()
        if backends.get("cbirrt", {}).get("available"):
            planners["cbirrt"]["available"] = True
        if backends.get("curobo", {}).get("available"):
            planners["curobo"]["available"] = True
    except Exception:
        pass

    return planners


def choreo_to_model_rad(side: str, outward_deg_dict: dict) -> np.ndarray:
    """Convert outward degrees {1..7} (choreo format) to signed model radians (sim format)."""
    m = -1 if side == "left" else 1
    q = np.zeros(7, dtype=float)
    for j in range(1, 8):
        val = float(outward_deg_dict.get(j) or outward_deg_dict.get(str(j)) or 0.0)
        signed_deg = val * (m if j in cue.MIRRORED_JOINTS else 1)
        q[j - 1] = math.radians(signed_deg)
    return q


def model_rad_to_choreo(side: str, model_rad: np.ndarray | list[float]) -> dict:
    """Convert signed model radians (sim format) to outward degrees {1..7} (choreo format)."""
    m = -1 if side == "left" else 1
    out = {}
    for j in range(1, 8):
        signed_deg = math.degrees(float(model_rad[j - 1]))
        outward_deg = signed_deg * (m if j in cue.MIRRORED_JOINTS else 1)
        out[str(j)] = round(outward_deg, 2)
    return out


def resolve_previous_arm_state(doc: dict, slot: str, target_time: float, limits: dict) -> tuple[dict, float]:
    """Find the arm's pose and finish time immediately prior to target_time in the show."""
    side = "left" if slot == "ARM_L" else "right"
    rest = cue.rest_pose(limits, side)
    last_pose = {str(j): rest.get(j, 0.0) for j in range(1, 8)}
    last_time = 0.0

    # Resolve events using cue.Show
    try:
        show = cue.Show(doc, "")
        events, _, _ = show._resolve(lambda p: None)
        for e in events:
            if e.get("slot") == slot and e.get("kind") == "keyframe":
                if e["t"] < target_time - 1e-6:
                    if e.get("pose"):
                        last_pose = {str(j): round(float(v), 2) for j, v in e["pose"].items()}
                    last_time = float(e.get("end") or e["t"])
    except Exception:
        pass

    return last_pose, last_time


def plan_arm_move(
    doc: dict,
    text: str,
    index: int,
    planner_choice: str = "cbirrt",
    reach_target: str | list[float] | None = None,
    limits_path: str | None = None,
) -> dict:
    """Plan collision-free motion for cue at `index` using selected planner."""
    cues = doc.get("cues") or []
    if index < 0 or index >= len(cues):
        raise ValueError(f"Cue index {index} out of range (0..{len(cues)-1})")

    c = copy.deepcopy(cues[index])
    slot = c.get("slot")
    if slot not in cue.ARM_SLOTS:
        raise ValueError(f"Cue {index} slot '{slot}' is not an arm slot (must be ARM_L or ARM_R)")

    side = "left" if slot == "ARM_L" else "right"
    if not limits_path or not os.path.isfile(limits_path):
        fixture = os.path.join(CHOREO_DIR, "tests", "joint_limits_fixture.yaml")
        limits_path = cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS) else fixture
    limits = cue.load_limits(limits_path)

    # 1. Resolve target arrival time t_goal
    show = cue.Show(doc, "")
    events, _, _ = show._resolve(lambda p: None)
    target_event = None
    for e in events:
        src = e.get("src") or {}
        if src.get("index") == index:
            target_event = e
            break

    if target_event is None:
        raise ValueError(f"Could not resolve timing for cue {index}")

    t_goal = float(target_event["t"])

    # 2. Find start pose and start time
    q_prev_deg, t_prev = resolve_previous_arm_state(doc, slot, t_goal, limits)
    q_start_rad = choreo_to_model_rad(side, q_prev_deg)

    # 3. Determine goal pose
    import sim_preview
    kin = ik.Kin(limits)
    trial = dict(doc, cues=[x for k, x in enumerate(doc.get("cues") or []) if k != index])
    try:
        tl = cue.Show(trial, "").export(limits, limits_path)
        smp = sim_preview.sample(tl, t_goal)
        kin.pose(smp)
    except Exception:
        pass
    q_goal_rad = None
    goal_deg = None

    if reach_target is not None:
        # Solve reach target (Cartesian static point or named target)
        mode = "reach"
        try:
            if isinstance(reach_target, str) and reach_target.startswith("["):
                import json
                reach_val = json.loads(reach_target)
            else:
                reach_val = reach_target
            goal_world = kin.point(reach_val, side)
            sol_outward, err, _ = kin.solve(side, mode, goal_world)
            if err > ik.REACH_TOL_M:
                raise ValueError(f"Target '{reach_target}' is out of reach ({err:.3f} m off)")
            goal_deg = sol_outward
            q_goal_rad = choreo_to_model_rad(side, goal_deg)
            c["reach"] = reach_val
            c["solved"] = sol_outward
            c["solved_for"] = f"reach:{reach_target}@{kin.sig}"
        except Exception as e:
            raise ValueError(f"Failed to solve target '{reach_target}': {e}") from e
    elif "solved" in c:
        goal_deg = c["solved"]
        q_goal_rad = choreo_to_model_rad(side, goal_deg)
    elif "joints" in c:
        goal_deg = c["joints"]
        q_goal_rad = choreo_to_model_rad(side, goal_deg)
    elif "pose" in c and c["pose"] == "rest":
        goal_deg = cue.rest_pose(limits, side)
        q_goal_rad = choreo_to_model_rad(side, goal_deg)
    elif "reach" in c:
        goal_world = kin.point(c["reach"], side)
        sol_outward, err, _ = kin.solve(side, "reach", goal_world)
        goal_deg = sol_outward
        q_goal_rad = choreo_to_model_rad(side, goal_deg)
        c["solved"] = sol_outward
        c["solved_for"] = f"reach:{c['reach']}@{kin.sig}"
    else:
        raise ValueError(f"Cue {index} does not have joints, pose, or reach target")

    # 4. Handle planner selection
    planner_choice = planner_choice.lower()
    c["planner"] = planner_choice

    if planner_choice == "spline":
        # Straight spline move: update cue with planner: spline and apply
        if goal_deg and "joints" in c:
            c["joints"] = {str(k): float(v) for k, v in goal_deg.items()}
        edits = [{"op": "set", "index": index, "cue": c}]
        new_text = yamledit.apply(text, edits)
        return {
            "ok": True,
            "planner": "spline",
            "text": new_text,
            "waypoints_inserted": 0,
            "min_clearance_mm": None,
            "duration_s": t_goal - t_prev,
            "message": "Configured move to use standard Hermite cubic spline.",
        }

    # 5. Collision-Free Planning (cbirrt or curobo)
    from sim.planner import OpenArmPlanner
    planner = OpenArmPlanner(arm=side, backend=planner_choice)

    try:
        traj = planner.plan_to_joint_goal(q_start_rad, q_goal_rad, timeout=6.0)
    except Exception as e:
        raise RuntimeError(f"Motion planning failed: {e}") from e

    if traj is None or len(traj.positions) == 0:
        raise RuntimeError(f"Planner '{planner_choice}' could not find a collision-free path between start and goal.")

    # 6. Check if intermediate waypoints are necessary
    # If the straight-line clearance is already >= 25 mm, no intermediate waypoints needed
    straight_clearance, _, _ = kin.clearance_along(side, q_start_rad, q_goal_rad)
    needs_intermediate = straight_clearance < ik.CLEAR_MIN_MM

    dt = max(0.1, t_goal - t_prev)
    time_form = "t" in c and "t" or ("beat" in c and "beat" or ("bar" in c and "bar" or "t"))

    # Update arrival cue
    final_cue = copy.deepcopy(c)
    final_cue["planner"] = planner_choice
    final_cue["joints"] = model_rad_to_choreo(side, traj.positions[-1])
    if "solved" in final_cue:
        final_cue["solved"] = model_rad_to_choreo(side, traj.positions[-1])

    edits = []
    num_wp_inserted = 0

    if needs_intermediate and len(traj.positions) > 3:
        # Sample 2 intermediate waypoints to steer around obstacles
        wp_indices = [
            int(len(traj.positions) * 0.35),
            int(len(traj.positions) * 0.70),
        ]
        # Insert them right before the arrival cue
        for k, idx in enumerate(wp_indices):
            frac = (k + 1) / (len(wp_indices) + 1)
            t_wp = t_prev + frac * dt
            wp_cue = {
                "t": round(t_wp, 3),
                "slot": slot,
                "joints": model_rad_to_choreo(side, traj.positions[idx]),
                "planner": planner_choice,
            }
            edits.append({"op": "insert", "index": index + k, "cue": wp_cue})
            num_wp_inserted += 1

        # Adjust index of the final cue since items were inserted before it
        edits.append({"op": "set", "index": index + num_wp_inserted, "cue": final_cue})
    else:
        edits.append({"op": "set", "index": index, "cue": final_cue})

    new_text = yamledit.apply(text, edits)

    return {
        "ok": True,
        "planner": planner_choice,
        "text": new_text,
        "waypoints_inserted": num_wp_inserted,
        "min_clearance_mm": round(float(traj.min_clearance_mm), 1),
        "duration_s": round(float(traj.duration), 2),
        "straight_clearance_mm": round(float(straight_clearance), 1),
        "message": (
            f"Planned collision-free path with {planner_choice.upper()}! "
            f"Min clearance: {traj.min_clearance_mm:.1f} mm "
            f"({num_wp_inserted} avoidance waypoints inserted)."
            if num_wp_inserted > 0 else
            f"Planned direct path with {planner_choice.upper()}! Min clearance: {traj.min_clearance_mm:.1f} mm."
        ),
    }
