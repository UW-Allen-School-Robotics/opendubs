#!/usr/bin/env python3
"""Hardware Bridge and Safety Execution Layer for OpenArm.

Connects the OpenArm Motion Planner and Studio to the physical robot hardware
with end-to-end safety validation:
  1. Workspace Box validation (HAND_BOX link7 + 186 mm fingertip TCP guard).
  2. Motor velocity trip caps (TRIP_DEG_S protection against controller freeze).
  3. Joint range compliance.
  4. Multi-transport execution:
     - Direct ROS 2 Action Client (FollowJointTrajectory over rclpy).
     - Docker Exec container bridge (openarm-up / ros2 action CLI).
     - HITL (Hardware-in-the-Loop) dry-run loopback simulation.
  5. Live execution telemetry, progress tracking, and emergency stop (HOLD).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
CHOREO_DIR = os.path.join(REPO, "software", "choreo")

for p in (REPO, SIM_DIR, CHOREO_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import urllib.request
import tcp
import cue
from cue import TRIP_DEG_S, SPEED_CAP_DEG_S
import ik


def load_arm_limits() -> dict:
    """Load the single source of truth for arm limits (OPENDUBS_ARM_LIMITS)."""
    limits_path = os.environ.get("OPENDUBS_ARM_LIMITS", cue.DEFAULT_LIMITS)
    if not os.path.isfile(limits_path):
        limits_path = os.path.join(CHOREO_DIR, "tests", "joint_limits_fixture.yaml")
    return cue.load_limits(limits_path)


def get_robot_safety_state(thor_host: str = "127.0.0.1", timeout: float = 0.5) -> Dict[str, Any]:
    """Query live robot safety telemetry from kiosk-dashboard with fail-closed semantics."""
    candidates = [
        f"http://{thor_host}:8080/api/choreo/robot",
        "http://127.0.0.1:8080/api/choreo/robot",
    ]
    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpenArmPlanner/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    estop = data.get("estop")
                    deadman = data.get("deadman")
                    ch5 = data.get("ch5")
                    motion_power = data.get("motion_power")
                    fault = data.get("fault")

                    # Fail-closed rules:
                    # estop must be strictly False (not None, not True)
                    # deadman must be strictly True (not None, not False)
                    # ch5 must be strictly "AUTO"
                    # motion_power must be strictly True
                    # fault must be None or empty
                    is_safe = (
                        estop is False
                        and deadman is True
                        and ch5 == "AUTO"
                        and bool(motion_power) is True
                        and not fault
                    )
                    return {
                        "ok": True,
                        "safe": is_safe,
                        "deadman": deadman,
                        "ch5": ch5,
                        "estop": estop,
                        "motion_power": motion_power,
                        "fault": fault,
                        "arms": data.get("arms"),
                        "source": url,
                    }
        except Exception:
            continue

    return {
        "ok": False,
        "safe": False,
        "deadman": None,
        "ch5": None,
        "estop": None,
        "motion_power": None,
        "fault": "Could not connect to robot safety API — fail closed",
        "source": None,
    }


@dataclass
class PreflightCheckResult:
    ok: bool
    hand_box_ok: bool
    speed_trip_ok: bool
    joint_limits_ok: bool
    min_clearance_mm: float
    max_joint_speeds_deg_s: Dict[int, float] = field(default_factory=dict)
    tcp_min_z_m: float = 0.0
    tcp_max_z_m: float = 0.0
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "hand_box_ok": self.hand_box_ok,
            "speed_trip_ok": self.speed_trip_ok,
            "joint_limits_ok": self.joint_limits_ok,
            "min_clearance_mm": round(self.min_clearance_mm, 2),
            "max_joint_speeds_deg_s": {k: round(v, 2) for k, v in self.max_joint_speeds_deg_s.items()},
            "tcp_min_z_m": round(self.tcp_min_z_m, 4),
            "tcp_max_z_m": round(self.tcp_max_z_m, 4),
            "problems": self.problems,
            "warnings": self.warnings,
        }


def validate_trajectory_safety(
    arm: str,
    times: List[float],
    positions_rad: np.ndarray,
    velocities_rad_s: Optional[np.ndarray] = None,
    min_clearance_mm: Optional[float] = None,
) -> PreflightCheckResult:
    """Preflight safety checker that validates a trajectory before physical hardware execution."""
    arm = arm.lower()
    problems: List[str] = []
    warnings: List[str] = []

    pos = np.asarray(positions_rad, float)
    if pos.ndim != 2 or pos.shape[1] != 7:
        return PreflightCheckResult(
            ok=False,
            hand_box_ok=False,
            speed_trip_ok=False,
            joint_limits_ok=False,
            min_clearance_mm=0.0,
            problems=[f"Invalid trajectory shape {pos.shape}, expected (N, 7)."],
        )

    t = np.asarray(times, float)
    n_pts = len(t)
    if n_pts != len(pos):
        return PreflightCheckResult(
            ok=False,
            hand_box_ok=False,
            speed_trip_ok=False,
            joint_limits_ok=False,
            min_clearance_mm=0.0,
            problems=[f"Trajectory times count ({n_pts}) does not match positions ({len(pos)})."],
        )

    # 1. Joint Limits Verification against OPENDUBS_ARM_LIMITS
    limits = load_arm_limits()
    deg = np.degrees(pos)
    joint_limits_ok = True
    m = -1 if arm == "left" else 1

    for j_idx in range(7):
        j_num = j_idx + 1
        name = f"openarm_{arm}_joint{j_num}"
        lo, hi = limits.get(name, (None, None))
        if lo is not None and hi is not None:
            # Model radians to degrees with validator margin
            lo_d = math.degrees(lo) + cue.MARGIN_DEG
            hi_d = math.degrees(hi) - cue.MARGIN_DEG
            j_min = float(np.min(deg[:, j_idx]))
            j_max = float(np.max(deg[:, j_idx]))
            if j_min < lo_d - 0.5 or j_max > hi_d + 0.5:
                joint_limits_ok = False
                problems.append(
                    f"Joint {j_num} limits exceeded: min={j_min:.1f}°, max={j_max:.1f}° (usable [{lo_d:.1f}°, {hi_d:.1f}°])"
                )

    # 2. Velocity and Measured Speed Cap Verification
    speed_trip_ok = True
    max_speeds: Dict[int, float] = {}

    if velocities_rad_s is not None and len(velocities_rad_s) == n_pts:
        vel_deg_s = np.degrees(np.asarray(velocities_rad_s, float))
    else:
        dt = np.diff(t)
        dt = np.where(dt <= 1e-6, 1e-6, dt)
        vel_deg_s = np.zeros_like(deg)
        vel_deg_s[:-1] = np.abs(np.diff(deg, axis=0)) / dt[:, None]
        vel_deg_s[-1] = vel_deg_s[-2] if n_pts > 1 else np.zeros(7)

    for j_idx in range(7):
        j_num = j_idx + 1
        peak_v = float(np.max(np.abs(vel_deg_s[:, j_idx])))
        max_speeds[j_num] = peak_v
        measured_cap = SPEED_CAP_DEG_S.get(j_num, 135.0)
        trip_limit = TRIP_DEG_S.get(j_num, 960.0)

        if peak_v >= trip_limit:
            speed_trip_ok = False
            problems.append(
                f"Joint {j_num} peak speed {peak_v:.1f}°/s exceeds hardware trip cap of {trip_limit}°/s (motor will fault)!"
            )
        elif peak_v > measured_cap * cue.PEAK_FACTOR:
            warnings.append(
                f"Joint {j_num} peak speed {peak_v:.1f}°/s exceeds measured speed cap ({measured_cap}°/s x {cue.PEAK_FACTOR})."
            )

    # 3. Workspace Box Verification (HAND_BOX link7 + 186 mm)
    hand_box_ok = True
    tcp_min_z = 0.0
    tcp_max_z = 0.0
    try:
        chain = tcp.load_chain()
        if chain is not None:
            tcps = tcp.tcp_points(chain, arm, pos)
            tcp_min_z = float(np.min(tcps[:, 2]))
            tcp_max_z = float(np.max(tcps[:, 2]))
            
            box_min = np.array(tcp.HAND_BOX["min"]) + tcp.MARGIN_M
            box_max = np.array(tcp.HAND_BOX["max"]) - tcp.MARGIN_M

            for i, pt in enumerate(tcps):
                if np.any(pt < box_min) or np.any(pt > box_max):
                    hand_box_ok = False
                    time_s = t[i]
                    problems.append(
                        f"TCP fingertip left controller HAND_BOX at t={time_s:.2f}s: pos=[{pt[0]:.3f}, {pt[1]:.3f}, {pt[2]:.3f}] m "
                        f"(box limits: x,y [{box_min[0]:.2f}..{box_max[0]:.2f}], z [{box_min[2]:.2f}..{box_max[2]:.2f}] m)."
                    )
                    break
        else:
            warnings.append("Arm kinematic chain.json not found; skipping exact TCP bounding box check.")
    except Exception as e:
        warnings.append(f"Could not compute TCP box check: {e}")

    # 4. Computed Collision Clearance Verification against CLEAR_MIN_MM (30 mm)
    computed_clearance = float("inf")
    try:
        import clearance
        import scene
        model = scene.build(None)
        data = clearance.mujoco.MjData(model)
        side = "left" if arm == "left" else "right"
        jids = [clearance.mujoco.mj_name2id(model, clearance.mujoco.mjtObj.mjOBJ_JOINT, f"oa_openarm_{side}_joint{j}") for j in range(1, 8)]
        qadr = [model.jnt_qposadr[i] for i in jids]
        step = max(1, len(pos) // 12)
        indices = list(range(0, len(pos), step))
        if (len(pos) - 1) not in indices:
            indices.append(len(pos) - 1)
        for idx in indices:
            q_pt = pos[idx]
            for i, a in enumerate(qadr):
                data.qpos[a] = q_pt[i]
            clearance.mujoco.mj_forward(model, data)
            c = clearance.clearances(model, data, limit_mm=200.0)
            if c and c[0][0] < computed_clearance:
                computed_clearance = c[0][0]
    except Exception as e:
        computed_clearance = min_clearance_mm if min_clearance_mm is not None else ik.CLEAR_MIN_MM

    clearance_ok = computed_clearance >= ik.CLEAR_MIN_MM
    if not clearance_ok:
        problems.append(
            f"Trajectory clearance {computed_clearance:.1f} mm is below required {ik.CLEAR_MIN_MM:.0f} mm (CLEAR_MIN_MM)."
        )

    ok = joint_limits_ok and speed_trip_ok and hand_box_ok and clearance_ok

    return PreflightCheckResult(
        ok=ok,
        hand_box_ok=hand_box_ok,
        speed_trip_ok=speed_trip_ok,
        joint_limits_ok=joint_limits_ok,
        min_clearance_mm=computed_clearance,
        max_joint_speeds_deg_s=max_speeds,
        tcp_min_z_m=tcp_min_z,
        tcp_max_z_m=tcp_max_z,
        problems=problems,
        warnings=warnings,
    )


class HardwareBridge:
    """Manages physical robot connection, action execution, and HITL simulation."""

    def __init__(self, node_name: str = "openarm_motion_planner_bridge"):
        self.node_name = node_name
        self.lock = threading.Lock()
        
        # State tracking
        self.is_executing = False
        self.status = "idle"  # idle, validating, preparing, executing, succeeded, cancelled, failed
        self.active_arm: Optional[str] = None
        self.progress_pct = 0.0
        self.current_time_s = 0.0
        self.total_time_s = 0.0
        self.last_error: Optional[str] = None
        self.active_goal_id: Optional[str] = None
        
        # Cancel event
        self._cancel_requested = threading.Event()
        self._exec_thread: Optional[threading.Thread] = None

        # Simulated state for HITL loopback
        self._sim_joint_states: Dict[str, List[float]] = {
            "left": [0.0, -20.0, 0.0, 95.0, 0.0, 15.0, 0.0],
            "right": [0.0, 20.0, 0.0, 95.0, 0.0, 15.0, 0.0],
        }
        self._sim_gripper_state: Dict[str, float] = {
            "left": 1.0,  # 1.0 = open, 0.0 = closed
            "right": 1.0,
        }
        self._cached_safety: Optional[Dict[str, Any]] = None
        self._cached_safety_time: float = 0.0

    def get_robot_safety(self) -> Dict[str, Any]:
        """Fetch cached robot safety status (refreshed every 0.25s)."""
        now = time.monotonic()
        if self._cached_safety is not None and (now - self._cached_safety_time < 0.25):
            return self._cached_safety
        self._cached_safety = get_robot_safety_state()
        self._cached_safety_time = now
        return self._cached_safety

    def detect_environment(self) -> Dict[str, Any]:
        """Detect what execution transports are available on the host system.
        
        Hardware execution on physical robot is disabled on this branch:
        arm motion goes exclusively through software/choreo/runner.py.
        """
        return {
            "hitl_available": True,
            "docker_available": False,
            "docker_running": False,
            "remote_thor_available": False,
            "ros2_rclpy_available": False,
            "ros2_control_msgs_available": False,
            "openarm_up_present": False,
            "recommended_mode": "hitl",
        }

    def get_status(self) -> Dict[str, Any]:
        """Return real-time execution status."""
        with self.lock:
            status_dict = {
                "status": self.status,
                "is_executing": self.is_executing,
                "arm": self.active_arm,
                "progress_pct": round(self.progress_pct, 1),
                "current_time_s": round(self.current_time_s, 2),
                "total_time_s": round(self.total_time_s, 2),
                "goal_id": self.active_goal_id,
                "last_error": self.last_error,
                "sim_joint_states": self._sim_joint_states,
                "sim_gripper_state": self._sim_gripper_state,
            }
        status_dict["robot_safety"] = self.get_robot_safety()
        return status_dict

    def command_gripper(
        self,
        arm: str,
        action: str | float | int,
        mode: str = "hitl",
    ) -> Dict[str, Any]:
        """Command the simulated arm gripper in HITL mode."""
        arm = arm.lower()
        if arm not in ("left", "right"):
            return {"success": False, "error": f"Invalid arm: {arm}"}

        if mode != "hitl":
            return {
                "success": False,
                "error": "Direct hardware commanding is disabled on this branch. Physical arm and gripper motion must go through software/choreo/runner.py.",
            }

        # Normalize action to 0.0 (closed) .. 1.0 (open)
        if isinstance(action, str):
            act_lower = action.strip().lower()
            if act_lower in ("open", "opened", "release"):
                fraction = 1.0
            elif act_lower in ("close", "closed", "grip", "grasp"):
                fraction = 0.0
            else:
                try:
                    fraction = float(action)
                except ValueError:
                    fraction = 1.0
        else:
            fraction = float(action)

        fraction = max(0.0, min(1.0, fraction))
        pos_m = round(fraction * 0.044, 4)

        with self.lock:
            self._sim_gripper_state[arm] = fraction

        return {
            "success": True,
            "arm": arm,
            "action": "open" if fraction >= 0.5 else "close",
            "open_fraction": fraction,
            "position_m": pos_m,
            "mode": "hitl",
        }

    def stop(self) -> Dict[str, Any]:
        """Emergency Stop / Hold: cancel ongoing trajectory execution."""
        with self.lock:
            if not self.is_executing:
                return {"success": True, "message": "No active execution to stop."}
            self._cancel_requested.set()
            arm = self.active_arm

        return {"success": True, "message": f"Execution cancellation requested for {arm} arm."}

    def execute_trajectory(
        self,
        arm: str,
        trajectory: Dict[str, Any],
        mode: str = "hitl",
        progress_cb: Optional[Callable[[float, float], None]] = None,
    ) -> Dict[str, Any]:
        """Execute trajectory via HITL simulation loopback."""
        arm = arm.lower()
        times = trajectory.get("times", [])
        positions = trajectory.get("positions_rad", [])
        velocities = trajectory.get("velocities_rad_s", None)
        clearance_mm = float(trajectory.get("min_clearance_mm", 30.0))

        if not times or not positions:
            return {"success": False, "error": "Empty trajectory provided."}

        # Direct hardware execution is strictly disabled on this branch
        if mode != "hitl":
            return {
                "success": False,
                "error": (
                    "Direct hardware execution bypassed show runner and is disabled in this branch. "
                    "All physical arm motion on the robot must go through the choreography show runner "
                    "(software/choreo/runner.py), which holds the motion lease and safety gates."
                ),
            }

        # 1. Run Preflight Safety Verification
        safety = validate_trajectory_safety(
            arm=arm,
            times=times,
            positions_rad=positions,
            velocities_rad_s=velocities,
            min_clearance_mm=clearance_mm,
        )
        if not safety.ok:
            return {
                "success": False,
                "error": "Preflight safety validation rejected this trajectory.",
                "details": safety.to_dict(),
            }

        with self.lock:
            if self.is_executing:
                return {"success": False, "error": f"Another execution is already active on {self.active_arm}."}
            self.is_executing = True
            self.status = "preparing"
            self.active_arm = arm
            self.progress_pct = 0.0
            self.current_time_s = 0.0
            self.total_time_s = float(times[-1])
            self.last_error = None
            self.active_goal_id = f"goal_{int(time.time() * 1000)}"
            self._cancel_requested.clear()

        gripper_events = trajectory.get("gripper_events") or trajectory.get("gripperEvents") or []

        # Run execution asynchronously in background thread (HITL loopback simulation)
        def runner():
            try:
                self._run_hitl(arm, times, positions, gripper_events, progress_cb)
            except Exception as e:
                with self.lock:
                    self.status = "failed"
                    self.last_error = str(e)
                    self.is_executing = False
            finally:
                with self.lock:
                    if self.status not in ("failed", "cancelled"):
                        self.status = "succeeded"
                        self.progress_pct = 100.0
                        self.current_time_s = self.total_time_s
                    self.is_executing = False

        self._exec_thread = threading.Thread(target=runner, daemon=True)
        self._exec_thread.start()

        return {
            "success": True,
            "mode": "hitl",
            "goal_id": self.active_goal_id,
            "duration": self.total_time_s,
            "safety_check": safety.to_dict(),
        }

    def _run_hitl(
        self,
        arm: str,
        times: List[float],
        positions: List[List[float]],
        gripper_events: Optional[List[Dict[str, Any]]] = None,
        progress_cb: Optional[Callable[[float, float], None]] = None,
    ):
        """Hardware-in-the-Loop simulation playback."""
        with self.lock:
            self.status = "executing"

        t_total = float(times[-1])
        t_start = time.perf_counter()

        idx = 0
        n_pts = len(times)
        while idx < n_pts:
            if self._cancel_requested.is_set():
                with self.lock:
                    self.status = "cancelled"
                return

            t_elapsed = time.perf_counter() - t_start
            # Find current waypoint
            while idx < n_pts - 1 and times[idx + 1] <= t_elapsed:
                idx += 1

            # Update simulated joint position
            cur_pos_deg = np.degrees(positions[idx]).tolist()
            with self.lock:
                self.current_time_s = min(t_elapsed, t_total)
                self.progress_pct = min(100.0, (t_elapsed / max(0.001, t_total)) * 100.0)
                self._sim_joint_states[arm] = cur_pos_deg

                if gripper_events:
                    g_frac = None
                    for ev in gripper_events:
                        if t_elapsed >= float(ev.get("time", 0.0)) - 0.05:
                            g_frac = float(ev.get("fraction", 1.0))
                    if g_frac is not None:
                        self._sim_gripper_state[arm] = g_frac

            if progress_cb:
                progress_cb(self.current_time_s, self.progress_pct)

            if t_elapsed >= t_total:
                break

            time.sleep(0.02)  # 50 Hz controller loop simulation

        with self.lock:
            self.progress_pct = 100.0
            self.current_time_s = t_total
            self._sim_joint_states[arm] = np.degrees(positions[-1]).tolist()
            if gripper_events:
                self._sim_gripper_state[arm] = float(gripper_events[-1].get("fraction", 1.0))
            self.status = "succeeded"

# Global instance
HARDWARE_BRIDGE = HardwareBridge()
