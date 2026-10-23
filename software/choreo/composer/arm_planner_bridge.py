"""Bridge between Choreography Composer and OpenArm Motion Planner (sim.planner).

Provides multi-backend planning choice for arm moves:
- 'spline': Default direct cubic Hermite spline interpolation.
- 'cbirrt': SSIK analytical kinematics + MuJoCo clearance search (CPU).
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
            "description": "SSIK analytical kinematics + MuJoCo collision checking (CPU)",
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


def choreo_to_model_rad(side: str, outward_deg_dict: dict, fallback_deg: dict | None = None) -> np.ndarray:
    """Convert outward degrees {1..7} (choreo format) to signed model radians (sim format).
    Unspecified joints are filled from fallback_deg (e.g. previous pose) or 0.0.
    """
    m = -1 if side == "left" else 1
    q = np.zeros(7, dtype=float)
    for j in range(1, 8):
        if j in outward_deg_dict:
            val = float(outward_deg_dict[j])
        elif str(j) in outward_deg_dict:
            val = float(outward_deg_dict[str(j)])
        elif fallback_deg is not None and (j in fallback_deg or str(j) in fallback_deg):
            val = float(fallback_deg.get(j) if j in fallback_deg else fallback_deg[str(j)])
        else:
            val = 0.0
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


def clamp_to_usable_limits(
    joints_deg: dict,
    side: str,
    limits: dict,
    report: list | None = None,
    fallback_deg: dict | None = None,
    prefix: str = "",
) -> dict:
    """Clamp joint angles in outward degrees to the validator's usable range [lo + margin, hi - margin].
    Unspecified joints are filled from fallback_deg (e.g. previous pose) or 0.0.
    """
    out = {}
    m = -1 if side == "left" else 1
    for j in range(1, 8):
        if j in joints_deg:
            raw_v = float(joints_deg[j])
        elif str(j) in joints_deg:
            raw_v = float(joints_deg[str(j)])
        elif fallback_deg is not None and (j in fallback_deg or str(j) in fallback_deg):
            raw_v = float(fallback_deg.get(j) if j in fallback_deg else fallback_deg[str(j)])
        else:
            raw_v = 0.0
        v = raw_v
        name = f"openarm_{side}_joint{j}"
        lo, hi = limits.get(name, (None, None))
        if lo is not None and hi is not None:
            lo_d = math.degrees(lo) + cue.MARGIN_DEG
            hi_d = math.degrees(hi) - cue.MARGIN_DEG
            sign = (m if j in cue.MIRRORED_JOINTS else 1)
            if sign > 0:
                min_deg, max_deg = lo_d, hi_d
            else:
                min_deg, max_deg = -hi_d, -lo_d
            # Add small 0.1 deg safety buffer so round(v, 1) never violates boundary
            clamped_v = max(min_deg + 0.1, min(max_deg - 0.1, v))
            if abs(clamped_v - raw_v) > 0.05:
                if report is not None:
                    pfx = f"{prefix} " if prefix else ""
                    report.append(f"{pfx}J{j} ({raw_v:.1f}° -> {clamped_v:.1f}°)")
            v = clamped_v
        out[str(j)] = round(v, 1)
    return out


def resolve_previous_arm_state(doc: dict, slot: str, target_time: float, limits: dict) -> tuple[dict, float]:
    """Find the arm's pose and finish time immediately prior to target_time in the show."""
    side = "left" if slot == "ARM_L" else "right"
    rest = cue.rest_pose(limits, side)
    last_pose = {str(j): rest.get(j, 0.0) for j in range(1, 8)}
    last_time = 0.0

    show = cue.Show(doc, "")
    try:
        events, _, _ = show._resolve(lambda p: None)
    except Exception as exc:
        raise ValueError(f"Failed to resolve show timeline for arm state: {exc}") from exc

    for e in events:
        if e.get("slot") == slot and e["t"] < target_time - 1e-6:
            fields = e.get("fields") or {}
            if "joints" in fields:
                for j, v in fields["joints"].items():
                    last_pose[str(j)] = round(float(v), 2)
                last_time = float(e.get("end") or e["t"])
            elif "solved" in fields:
                for j, v in fields["solved"].items():
                    last_pose[str(j)] = round(float(v), 2)
                last_time = float(e.get("end") or e["t"])
            elif fields.get("pose") == "rest":
                for j, v in cue.rest_pose(limits, side).items():
                    last_pose[str(j)] = round(float(v), 2)
                last_time = float(e.get("end") or e["t"])

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
    q_start_rad = choreo_to_model_rad(side, clamp_to_usable_limits(q_prev_deg, side, limits), fallback_deg=q_prev_deg)

    # 3. Determine goal pose
    kin = None
    try:
        kin = ik.Kin(limits)
        import sim_preview
        trial = dict(doc, cues=[x for k, x in enumerate(doc.get("cues") or []) if k != index])
        tl = cue.Show(trial, "").export(limits, limits_path)
        smp = sim_preview.sample(tl, t_goal)
        kin.pose(smp)
    except Exception:
        pass
    q_goal_rad = None
    goal_deg = None
    goal_clamped: list[str] = []
    waypoint_clamped: list[str] = []

    if reach_target is not None:
        if kin is None:
            raise RuntimeError("MuJoCo is required to solve Cartesian reach targets.")
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
            goal_deg = clamp_to_usable_limits(sol_outward, side, limits, report=goal_clamped, fallback_deg=q_prev_deg, prefix="goal")
            q_goal_rad = choreo_to_model_rad(side, goal_deg, fallback_deg=q_prev_deg)
            c["reach"] = reach_val
            c["solved"] = goal_deg
            c["solved_for"] = f"reach:{reach_target}@{kin.sig}"
            c.pop("joints", None)
            c.pop("pose", None)
        except Exception as e:
            raise ValueError(f"Failed to solve target '{reach_target}': {e}") from e
    elif "solved" in c:
        goal_deg = clamp_to_usable_limits(c["solved"], side, limits, report=goal_clamped, fallback_deg=q_prev_deg, prefix="goal")
        q_goal_rad = choreo_to_model_rad(side, goal_deg, fallback_deg=q_prev_deg)
    elif "joints" in c:
        goal_deg = clamp_to_usable_limits(c["joints"], side, limits, report=goal_clamped, fallback_deg=q_prev_deg, prefix="goal")
        q_goal_rad = choreo_to_model_rad(side, goal_deg, fallback_deg=q_prev_deg)
    elif "pose" in c and c["pose"] == "rest":
        goal_deg = clamp_to_usable_limits(cue.rest_pose(limits, side), side, limits, report=goal_clamped, fallback_deg=q_prev_deg, prefix="goal")
        q_goal_rad = choreo_to_model_rad(side, goal_deg, fallback_deg=q_prev_deg)
    elif "reach" in c:
        if kin is None:
            raise RuntimeError("MuJoCo is required to solve Cartesian reach targets.")
        goal_world = kin.point(c["reach"], side)
        sol_outward, err, _ = kin.solve(side, "reach", goal_world)
        goal_deg = clamp_to_usable_limits(sol_outward, side, limits, report=goal_clamped, fallback_deg=q_prev_deg, prefix="goal")
        q_goal_rad = choreo_to_model_rad(side, goal_deg, fallback_deg=q_prev_deg)
        c["solved"] = goal_deg
        c["solved_for"] = f"reach:{c['reach']}@{kin.sig}"
        c.pop("joints", None)
        c.pop("pose", None)
    else:
        raise ValueError(f"Cue {index} does not have joints, pose, or reach target")

    # 4. Handle planner selection
    planner_choice = planner_choice.lower()
    c.pop("planner", None)

    if planner_choice == "spline":
        # Straight spline move: update cue and apply
        if "reach" in c:
            c.pop("joints", None)
            c.pop("pose", None)
        elif goal_deg and "joints" in c:
            c["joints"] = {str(k): float(v) for k, v in goal_deg.items()}
        edits = [{"op": "set", "index": index, "cue": c}]
        msg = "Configured move to use standard Hermite cubic spline."
        all_clamped = goal_clamped + waypoint_clamped
        if all_clamped:
            msg += f" Note: {', '.join(all_clamped)} clamped to usable joint range (5° margin inside hardware limits)."
        new_text = yamledit.apply(text, edits)
        return {
            "ok": True,
            "planner": "spline",
            "text": new_text,
            "waypoints_inserted": 0,
            "min_clearance_mm": None,
            "duration_s": t_goal - t_prev,
            "message": msg,
            "clamped": all_clamped,
            "goal_clamped": goal_clamped,
            "waypoint_clamped": waypoint_clamped,
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
    # If the straight-line clearance is already >= CLEAR_MIN_MM, no intermediate waypoints needed
    straight_clearance = None
    needs_intermediate = False
    mujoco_checked = False
    if kin is not None:
        try:
            straight_clearance, _, _ = kin.clearance_along(side, q_start_rad, q_goal_rad)
            needs_intermediate = straight_clearance < ik.CLEAR_MIN_MM
            mujoco_checked = True
        except Exception:
            straight_clearance = None
            needs_intermediate = False

    dt = max(0.1, t_goal - t_prev)
    time_form = "t" in c and "t" or ("beat" in c and "beat" or ("bar" in c and "bar" or "t"))

    # Update arrival cue
    final_cue = copy.deepcopy(c)
    final_cue.pop("planner", None)
    goal_outward = clamp_to_usable_limits(
        model_rad_to_choreo(side, traj.positions[-1]),
        side,
        limits,
        report=goal_clamped,
        fallback_deg=q_prev_deg,
        prefix="goal",
    )
    if "reach" in final_cue:
        final_cue.pop("joints", None)
        final_cue.pop("pose", None)
        final_cue["solved"] = goal_outward
        final_cue["solved_for"] = f"reach:{final_cue['reach']}@{kin.sig if kin else 'unknown'}"
    else:
        final_cue["joints"] = goal_outward
        final_cue.pop("reach", None)
        final_cue.pop("solved", None)
        final_cue.pop("solved_for", None)
        final_cue.pop("pose", None)

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
            if hasattr(traj, "times") and len(traj.times) > idx and traj.duration > 0:
                frac = float(traj.times[idx] / traj.duration)
            else:
                frac = (k + 1) / (len(wp_indices) + 1)
            t_wp = t_prev + frac * dt
            t_wp = max(t_prev + 0.05, min(t_goal - 0.05, t_wp))
            raw_wp = model_rad_to_choreo(side, traj.positions[idx])
            clamped_wp = clamp_to_usable_limits(
                raw_wp,
                side,
                limits,
                report=waypoint_clamped,
                fallback_deg=q_prev_deg,
                prefix=f"waypoint {k+1}",
            )
            wp_cue = {
                "t": round(t_wp, 3),
                "slot": slot,
                "joints": clamped_wp,
            }
            edits.append({"op": "insert", "index": index + k, "cue": wp_cue})
            num_wp_inserted += 1

        # Adjust index of the final cue since items were inserted before it
        edits.append({"op": "set", "index": index + num_wp_inserted, "cue": final_cue})
    else:
        edits.append({"op": "set", "index": index, "cue": final_cue})

    all_clamped = goal_clamped + waypoint_clamped

    # Record provenance in a timeline note
    note_text = (
        f"Generated with {planner_choice.upper()} (planner clearance: {traj.min_clearance_mm:.1f} mm, "
        f"{num_wp_inserted} waypoints; save-time sweep verifies robot spline clearance)."
        if num_wp_inserted > 0 else
        f"Generated with {planner_choice.upper()} (planner clearance: {traj.min_clearance_mm:.1f} mm; "
        f"save-time sweep verifies robot spline clearance)."
    )
    if all_clamped:
        note_text += f" Clamped to usable joint range: {', '.join(all_clamped)}."

    note = {
        "id": f"plan_{int(t_goal * 100)}_{slot.lower()}",
        "at": round(t_prev, 2),
        "to": round(t_goal, 2),
        "slots": [slot],
        "text": note_text,
        "status": "done",
        "by": "composer",
    }
    edits.append({"op": "note_add", "note": note})

    new_text = yamledit.apply(text, edits)

    if not mujoco_checked:
        msg = (
            f"Planned path with {planner_choice.upper()} (straight-line clearance check skipped: MuJoCo not available). "
            f"Planner path clearance: {traj.min_clearance_mm:.1f} mm. "
            f"Note: save-time sweep verifies clearance for the robot's spline path."
        )
    elif num_wp_inserted > 0:
        msg = (
            f"Planned path with {planner_choice.upper()}! "
            f"Planner path clearance: {traj.min_clearance_mm:.1f} mm "
            f"({num_wp_inserted} avoidance waypoints inserted). "
            f"Note: save-time sweep verifies clearance for the robot's spline path."
        )
    else:
        msg = (
            f"Planned direct path with {planner_choice.upper()}! "
            f"Planner path clearance: {traj.min_clearance_mm:.1f} mm. "
            f"Note: save-time sweep verifies clearance for the robot's spline path."
        )
    if all_clamped:
        msg += f" Note: {', '.join(all_clamped)} clamped to usable joint range (5° margin inside hardware limits)."

    return {
        "ok": True,
        "planner": planner_choice,
        "text": new_text,
        "waypoints_inserted": num_wp_inserted,
        "min_clearance_mm": round(float(traj.min_clearance_mm), 1),
        "duration_s": round(float(traj.duration), 2),
        "straight_clearance_mm": round(float(straight_clearance), 1) if straight_clearance is not None else None,
        "mujoco_checked": mujoco_checked,
        "message": msg,
        "clamped": all_clamped,
        "goal_clamped": goal_clamped,
        "waypoint_clamped": waypoint_clamped,
    }
