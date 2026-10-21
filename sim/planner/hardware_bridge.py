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

import tcp
from cue import TRIP_DEG_S

# Default joint limits in degrees [lower, upper] per joint (from openarm stack)
DEFAULT_JOINT_LIMITS_DEG = {
    1: (-150.0, 150.0),
    2: (-180.0, 180.0),
    3: (-150.0, 150.0),
    4: (0.0, 135.0),
    5: (-120.0, 120.0),
    6: (-90.0, 90.0),
    7: (-180.0, 180.0),
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
    min_clearance_mm: float = 15.0,
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

    # 1. Joint Limits Verification
    deg = np.degrees(pos)
    joint_limits_ok = True
    for j_idx in range(7):
        j_num = j_idx + 1
        lo, hi = DEFAULT_JOINT_LIMITS_DEG.get(j_num, (-180.0, 180.0))
        j_min = float(np.min(deg[:, j_idx]))
        j_max = float(np.max(deg[:, j_idx]))
        if j_min < lo - 0.5 or j_max > hi + 0.5:
            joint_limits_ok = False
            problems.append(
                f"Joint {j_num} limits exceeded: min={j_min:.1f}°, max={j_max:.1f}° (allowed [{lo:.1f}°, {hi:.1f}°])"
            )

    # 2. Velocity and Speed Trip Verification
    speed_trip_ok = True
    max_speeds: Dict[int, float] = {}

    if velocities_rad_s is not None and len(velocities_rad_s) == n_pts:
        vel_deg_s = np.degrees(np.asarray(velocities_rad_s, float))
    else:
        # Numerically differentiate if velocities not provided
        dt = np.diff(t)
        dt = np.where(dt <= 1e-6, 1e-6, dt)
        vel_deg_s = np.zeros_like(deg)
        vel_deg_s[:-1] = np.abs(np.diff(deg, axis=0)) / dt[:, None]
        vel_deg_s[-1] = vel_deg_s[-2] if n_pts > 1 else np.zeros(7)

    for j_idx in range(7):
        j_num = j_idx + 1
        peak_v = float(np.max(np.abs(vel_deg_s[:, j_idx])))
        max_speeds[j_num] = peak_v
        trip_limit = TRIP_DEG_S.get(j_num, 960.0)
        # We enforce a safe operational ceiling at 80% of trip cap
        safe_ceiling = trip_limit * 0.8
        if peak_v >= trip_limit:
            speed_trip_ok = False
            problems.append(
                f"Joint {j_num} peak speed {peak_v:.1f}°/s exceeds hardware trip cap of {trip_limit}°/s (motor will fault)!"
            )
        elif peak_v >= safe_ceiling:
            warnings.append(
                f"Joint {j_num} peak speed {peak_v:.1f}°/s is near trip cap ({trip_limit}°/s, >80%)."
            )

    # 3. Workspace Box Verification (HAND_BOX link7 + 186 mm)
    hand_box_ok = True
    tcp_min_z = 0.0
    tcp_max_z = 0.0
    try:
        chain = tcp.load_chain()
        if chain is not None:
            # Controller sense joint angles
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

    ok = joint_limits_ok and speed_trip_ok and hand_box_ok and (min_clearance_mm >= 15.0)

    return PreflightCheckResult(
        ok=ok,
        hand_box_ok=hand_box_ok,
        speed_trip_ok=speed_trip_ok,
        joint_limits_ok=joint_limits_ok,
        min_clearance_mm=min_clearance_mm,
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

    def detect_environment(self) -> Dict[str, Any]:
        """Detect what execution transports are available on the host system."""
        # Check Docker container 'openarm'
        docker_available = False
        docker_running = False
        try:
            r = subprocess.run(["docker", "ps", "--filter", "name=openarm", "--format", "{{.Names}}"],
                               capture_output=True, text=True, timeout=2.0)
            if "openarm" in r.stdout:
                docker_available = True
                docker_running = True
        except Exception:
            pass

        # Check rclpy and control_msgs
        ros2_rclpy = False
        ros2_control_msgs = False
        try:
            import rclpy  # noqa: F401
            ros2_rclpy = True
            from control_msgs.action import FollowJointTrajectory  # noqa: F401
            ros2_control_msgs = True
        except Exception:
            pass

        # Check openarm-up binary
        openarm_up_present = os.path.exists("/usr/local/bin/openarm-up") or os.path.exists(
            os.path.join(REPO, "software", "openarm", "openarm-up")
        )

        return {
            "hitl_available": True,
            "docker_available": docker_available,
            "docker_running": docker_running,
            "ros2_rclpy_available": ros2_rclpy,
            "ros2_control_msgs_available": ros2_control_msgs,
            "openarm_up_present": openarm_up_present,
            "recommended_mode": "ros2" if (ros2_rclpy and ros2_control_msgs) else ("docker" if docker_running else "hitl"),
        }

    def get_status(self) -> Dict[str, Any]:
        """Return real-time execution status."""
        with self.lock:
            return {
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

    def command_gripper(
        self,
        arm: str,
        action: str | float | int,
        mode: str = "auto",
    ) -> Dict[str, Any]:
        """Command the specified arm gripper to open or close."""
        arm = arm.lower()
        if arm not in ("left", "right"):
            return {"success": False, "error": f"Invalid arm: {arm}"}

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
        # Stroke travel: 0.044 m (44 mm)
        pos_m = round(fraction * 0.044, 4)

        env = self.detect_environment()
        chosen_mode = mode
        if chosen_mode == "auto":
            chosen_mode = env["recommended_mode"]

        with self.lock:
            self._sim_gripper_state[arm] = fraction

        # If live hardware or docker
        if chosen_mode == "docker" and env.get("docker_running"):
            cmd = (
                f"docker exec openarm bash -c 'ros2 action send_goal "
                f"/{arm}_gripper_controller/gripper_cmd "
                f"control_msgs/action/GripperCommand "
                f"\"{{command: {{position: {pos_m:.4f}, max_effort: 5.0}}}}\"'"
            )
            try:
                subprocess.Popen(cmd, shell=True)
            except Exception as e:
                return {"success": False, "error": f"Docker gripper command error: {e}"}
        elif chosen_mode == "ros2" and env.get("ros2_rclpy_available"):
            cmd = (
                f"ros2 action send_goal /{arm}_gripper_controller/gripper_cmd "
                f"control_msgs/action/GripperCommand "
                f"'{{command: {{position: {pos_m:.4f}, max_effort: 5.0}}}}'"
            )
            try:
                subprocess.Popen(cmd, shell=True)
            except Exception as e:
                return {"success": False, "error": f"ROS 2 gripper command error: {e}"}

        return {
            "success": True,
            "arm": arm,
            "action": "open" if fraction >= 0.5 else "close",
            "open_fraction": fraction,
            "position_m": pos_m,
            "mode": chosen_mode,
        }

    def stop(self) -> Dict[str, Any]:
        """Emergency Stop / Hold: cancel ongoing trajectory execution."""
        with self.lock:
            if not self.is_executing:
                return {"success": True, "message": "No active execution to stop."}
            self._cancel_requested.set()
            arm = self.active_arm

        # Attempt to issue hold command to hardware if available
        try:
            openarm_up = "/usr/local/bin/openarm-up"
            if os.path.exists(openarm_up) and arm:
                subprocess.run(["sudo", "-n", openarm_up, "hold", arm], capture_output=True, timeout=3.0)
        except Exception:
            pass

        return {"success": True, "message": f"Execution cancellation requested for {arm} arm."}

    def execute_trajectory(
        self,
        arm: str,
        trajectory: Dict[str, Any],
        mode: str = "auto",
        progress_cb: Optional[Callable[[float, float], None]] = None,
    ) -> Dict[str, Any]:
        """Execute trajectory on robot or via HITL loopback."""
        arm = arm.lower()
        times = trajectory.get("times", [])
        positions = trajectory.get("positions_rad", [])
        velocities = trajectory.get("velocities_rad_s", None)
        clearance_mm = float(trajectory.get("min_clearance_mm", 20.0))

        if not times or not positions:
            return {"success": False, "error": "Empty trajectory provided."}

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

        # Determine mode
        env = self.detect_environment()
        if mode == "auto":
            mode = env["recommended_mode"]

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

        # Run execution asynchronously in background thread
        def runner():
            try:
                if mode == "hitl":
                    self._run_hitl(arm, times, positions, gripper_events, progress_cb)
                elif mode == "ros2":
                    self._run_ros2_action(arm, times, positions, velocities, progress_cb)
                elif mode == "docker":
                    self._run_docker_action(arm, times, positions, progress_cb)
                else:
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
            "mode": mode,
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

    def _run_ros2_action(
        self,
        arm: str,
        times: List[float],
        positions: List[List[float]],
        velocities: Optional[List[List[float]]],
        progress_cb: Optional[Callable[[float, float], None]],
    ):
        """Execute on physical robot using rclpy FollowJointTrajectory."""
        import rclpy
        from control_msgs.action import FollowJointTrajectory
        from rclpy.action import ActionClient
        from rclpy.signals import SignalHandlerOptions
        from trajectory_msgs.msg import JointTrajectoryPoint
        from builtin_interfaces.msg import Duration

        if not rclpy.ok():
            rclpy.init(signal_handler_options=SignalHandlerOptions.NO)

        node = rclpy.create_node(f"{self.node_name}_{arm}")
        action_name = f"/{arm}_controller/follow_joint_trajectory"
        client = ActionClient(node, FollowJointTrajectory, action_name)

        if not client.wait_for_server(timeout_sec=3.0):
            node.destroy_node()
            raise RuntimeError(f"Action server '{action_name}' not reachable.")

        goal = FollowJointTrajectory.Goal()
        goal.trajectory.joint_names = [f"openarm_{arm}_joint{j}" for j in range(1, 8)]

        for i, (t_sec, pos_rad) in enumerate(zip(times, positions)):
            pt = JointTrajectoryPoint()
            pt.positions = [float(v) for v in pos_rad]
            if velocities is not None and i < len(velocities):
                pt.velocities = [float(v) for v in velocities[i]]
            sec = int(t_sec)
            pt.time_from_start = Duration(sec=sec, nanosec=int(round((t_sec - sec) * 1e9)))
            goal.trajectory.points.append(pt)

        with self.lock:
            self.status = "executing"

        send_fut = client.send_goal_async(goal)
        rclpy.spin_until_future_complete(node, send_fut, timeout_sec=5.0)
        goal_handle = send_fut.result()

        if not goal_handle or not goal_handle.accepted:
            node.destroy_node()
            raise RuntimeError("Hardware controller REJECTED the trajectory goal.")

        result_fut = goal_handle.get_result_async()
        t_total = float(times[-1])
        t_start = time.perf_counter()

        while not result_fut.done():
            if self._cancel_requested.is_set():
                goal_handle.cancel_goal_async()
                rclpy.spin_until_future_complete(node, goal_handle.cancel_goal_async(), timeout_sec=2.0)
                node.destroy_node()
                with self.lock:
                    self.status = "cancelled"
                return

            rclpy.spin_once(node, timeout_sec=0.05)
            t_elapsed = time.perf_counter() - t_start
            with self.lock:
                self.current_time_s = min(t_elapsed, t_total)
                self.progress_pct = min(100.0, (t_elapsed / max(0.001, t_total)) * 100.0)

            if progress_cb:
                progress_cb(self.current_time_s, self.progress_pct)

        node.destroy_node()
        res = result_fut.result()
        if res.status != 4:  # 4 = STATUS_SUCCEEDED
            raise RuntimeError(f"Trajectory execution finished with status code {res.status}.")

    def _run_docker_action(
        self,
        arm: str,
        times: List[float],
        positions: List[List[float]],
        progress_cb: Optional[Callable[[float, float], None]],
    ):
        """Execute on physical robot through docker exec openarm container."""
        names = [f"openarm_{arm}_joint{j}" for j in range(1, 8)]
        names_str = ", ".join(f"'{n}'" for n in names)

        # Build trajectory points string
        points_str_list = []
        for t, pos in zip(times, positions):
            sec = int(t)
            nanosec = int(round((t - sec) * 1e9))
            pos_str = ", ".join(f"{float(p):.5f}" for p in pos)
            points_str_list.append(
                f"{{positions: [{pos_str}], time_from_start: {{sec: {sec}, nanosec: {nanosec}}}}}"
            )
        pts_str = ", ".join(points_str_list)

        goal_yaml = f"{{trajectory: {{joint_names: [{names_str}], points: [{pts_str}]}}}}"
        total_time = int(times[-1]) + 10

        cmd = [
            "docker", "exec", "openarm", "bash", "-c",
            f"source /opt/ros/jazzy/setup.bash && source /openarm_ws/install/setup.bash && "
            f"timeout {total_time} ros2 action send_goal /{arm}_controller/follow_joint_trajectory "
            f"control_msgs/action/FollowJointTrajectory \"{goal_yaml}\""
        ]

        with self.lock:
            self.status = "executing"

        t_total = float(times[-1])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        t_start = time.perf_counter()

        while proc.poll() is None:
            if self._cancel_requested.is_set():
                proc.terminate()
                # Run hold
                subprocess.run(
                    ["docker", "exec", "openarm", "bash", "-c", f"/usr/local/bin/openarm-up hold {arm}"],
                    capture_output=True, timeout=3.0
                )
                with self.lock:
                    self.status = "cancelled"
                return

            t_elapsed = time.perf_counter() - t_start
            with self.lock:
                self.current_time_s = min(t_elapsed, t_total)
                self.progress_pct = min(100.0, (t_elapsed / max(0.001, t_total)) * 100.0)

            if progress_cb:
                progress_cb(self.current_time_s, self.progress_pct)

            time.sleep(0.05)

        out, err = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Docker action execution failed: {err or out}")
        if "Goal accepted" not in out:
            raise RuntimeError(f"Controller did not accept goal: {out}")


# Global instance
HARDWARE_BRIDGE = HardwareBridge()
