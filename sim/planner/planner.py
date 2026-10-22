"""High-level OpenArm Motion Planner with modular backend support."""
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
    import mujoco
    import scene
    import clearance
    _HAVE_MUJOCO = True
except ImportError:
    _HAVE_MUJOCO = False

try:
    import toppra
    import toppra.constraint as constraint
    _HAVE_TOPPRA = True
except ImportError:
    _HAVE_TOPPRA = False

try:
    import openarm_left_ik
    import openarm_right_ik
    _HAVE_SSIK = True
except ImportError:
    _HAVE_SSIK = False

from .base import MotionPlannerBackend, PlannedTrajectory, create_backend, get_available_backends
from .cbirrt_backend import CBiRRTBackend
try:
    from . import curobo_backend  # Registers cuRobo backend if available
except Exception:
    pass


class MuJoCoRobotModel:
    """RobotModel protocol adapter for sscbirrt."""
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, joint_names: list[str], site_name: str):
        self.model = model
        self.data = data
        self.joint_names = joint_names
        self._dof = len(joint_names)
        
        self.qpos_indices = []
        lower = []
        upper = []
        for name in joint_names:
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            self.qpos_indices.append(model.jnt_qposadr[jid])
            rng = model.jnt_range[jid]
            lower.append(rng[0])
            upper.append(rng[1])
            
        self._joint_limits = (np.array(lower, dtype=float), np.array(upper, dtype=float))
        self.site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, site_name)

    @property
    def dof(self) -> int:
        return self._dof

    @property
    def joint_limits(self) -> tuple[np.ndarray, np.ndarray]:
        return self._joint_limits

    def forward_kinematics(self, q: np.ndarray) -> np.ndarray:
        for idx, val in zip(self.qpos_indices, q):
            self.data.qpos[idx] = val
        mujoco.mj_kinematics(self.model, self.data)
        
        pos = self.data.site_xpos[self.site_id]
        mat = self.data.site_xmat[self.site_id].reshape(3, 3)
        T = np.eye(4)
        T[:3, :3] = mat
        T[:3, 3] = pos
        return T


class MuJoCoCollisionChecker:
    """Checks self-collision and clearance using MuJoCo distance queries."""
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, joint_qpos_indices: list[int], min_clearance_mm: float = 15.0):
        self.model = model
        self.data = data
        self.joint_qpos_indices = joint_qpos_indices
        self.min_clearance_mm = min_clearance_mm
        self.pairs = clearance.pairs_of_interest(model)

    def is_valid(self, q: np.ndarray) -> bool:
        for idx, val in zip(self.joint_qpos_indices, q):
            self.data.qpos[idx] = val
        mujoco.mj_forward(self.model, self.data)
        
        if self.data.ncon > 0:
            for i in range(self.data.ncon):
                c = self.data.contact[i]
                g1, g2 = c.geom1, c.geom2
                if (g1, g2) in self.pairs or (g2, g1) in self.pairs:
                    return False

        fromto = np.zeros(6)
        for a, b in self.pairs:
            dist = mujoco.mj_geomDistance(self.model, self.data, a, b, self.min_clearance_mm / 1000.0, fromto)
            if dist * 1000.0 < self.min_clearance_mm:
                return False
        return True

    def min_clearance(self) -> float:
        """Return the minimum clearance in mm across all collision pairs."""
        fromto = np.zeros(6)
        min_d = float('inf')
        for a, b in self.pairs:
            d = mujoco.mj_geomDistance(self.model, self.data, a, b, 1.0, fromto)
            min_d = min(min_d, d * 1000.0)
        return min_d


class SSIKWrapper:
    """IKSolver wrapper for sscbirrt."""
    def __init__(self, solver_module):
        self.solver = solver_module

    def solve(self, pose: np.ndarray, q_init: np.ndarray | None = None) -> list[np.ndarray]:
        solutions = self.solver.solve(pose)
        res = [s.q for s in solutions]
        if q_init is not None and res:
            res.sort(key=lambda q: float(np.linalg.norm(q - q_init)))
        return res


class OpenArmPlanner:
    """Integrated motion planner for OpenArm V1."""

    def __init__(
        self,
        arm: str = "left",
        min_clearance_mm: float = 15.0,
        v_max: float = 2.0,      # rad/s joint velocity limit
        a_max: float = 4.0,      # rad/s^2 joint acceleration limit
        backend: MotionPlannerBackend | str | None = None,
    ):
        if arm not in ("left", "right"):
            raise ValueError(f"Arm must be 'left' or 'right', got {arm!r}")
        self.arm = arm
        self.v_max = v_max
        self.a_max = a_max

        if not _HAVE_MUJOCO:
            raise ImportError("MuJoCo is required for OpenArmPlanner simulation environment.")

        # 1. Load MuJoCo model and data
        self.model = scene.build(None)
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)

        # 2. Setup joint indices & kinematics
        self.joint_names = [f"oa_openarm_{arm}_joint{i}" for i in range(1, 8)]
        self.site_name = f"oa_openarm_{arm}_ee_site"
        self.robot_model = MuJoCoRobotModel(self.model, self.data, self.joint_names, self.site_name)
        self.collision_checker = MuJoCoCollisionChecker(
            self.model, self.data, self.robot_model.qpos_indices, min_clearance_mm=min_clearance_mm
        )

        # 3. Setup SSIK solver module
        if _HAVE_SSIK:
            ik_mod = openarm_left_ik if arm == "left" else openarm_right_ik
            self.ik_module = ik_mod
            self.ik_solver = SSIKWrapper(ik_mod)
        else:
            self.ik_module = None
            self.ik_solver = None

        # 4. Set planning backend (defaults to CBiRRT, modular for cuRobo/custom)
        if isinstance(backend, str):
            self.backend = create_backend(
                backend,
                robot_model=self.robot_model,
                collision_checker=self.collision_checker,
                ik_solver=self.ik_solver,
                arm=self.arm,
            )
        elif backend is not None:
            self.backend = backend
        else:
            self.backend = create_backend(
                "cbirrt",
                robot_model=self.robot_model,
                collision_checker=self.collision_checker,
                ik_solver=self.ik_solver,
                arm=self.arm,
            )

    def set_backend(self, backend: MotionPlannerBackend | str) -> None:
        """Swap the motion planning backend (e.g. 'cbirrt', 'curobo', or custom backend instance)."""
        if isinstance(backend, str):
            self.backend = create_backend(
                backend,
                robot_model=self.robot_model,
                collision_checker=self.collision_checker,
                ik_solver=self.ik_solver,
                arm=self.arm,
            )
        else:
            self.backend = backend

    def solve_ik(self, T_target: np.ndarray, q_seed: np.ndarray | None = None) -> list[np.ndarray]:
        """Solve IK for a 4x4 target in the arm base (link0) frame, sorted nearest to seed."""
        return self.ik_solver.solve(T_target, q_init=q_seed)

    def plan_to_joint_goal(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> PlannedTrajectory | None:
        """Plan a collision-free, retimed trajectory between two joint configurations."""
        q_start = np.asarray(q_start, dtype=float)
        q_goal = np.asarray(q_goal, dtype=float)

        if not self.collision_checker.is_valid(q_start):
            raise ValueError(f"Start configuration is in collision: {q_start}")
        if not self.collision_checker.is_valid(q_goal):
            raise ValueError(f"Goal configuration is in collision: {q_goal}")

        t0 = time.perf_counter()
        raw_result = self.backend.plan(q_start, q_goal, timeout=timeout)
        plan_time = time.perf_counter() - t0

        if raw_result is None:
            return None

        # If backend already returns a complete PlannedTrajectory (e.g. cuRobo)
        if isinstance(raw_result, PlannedTrajectory):
            raw_result.arm = self.arm
            raw_result.joint_names = self.joint_names
            if "plan_time_ms" not in raw_result.metadata:
                raw_result.metadata["plan_time_ms"] = plan_time * 1000.0
            return raw_result

        if len(raw_result) == 0:
            return None

        # Otherwise retime raw geometric waypoints with TOPP-RA
        traj = self._retime_path(raw_result, plan_time=plan_time)
        return traj

    def plan_to_cartesian_pose(
        self,
        q_start: np.ndarray,
        T_target_link0: np.ndarray,
        timeout: float = 5.0,
    ) -> PlannedTrajectory | None:
        """Plan to a static 4x4 Cartesian target pose expressed in openarm_<side>_link0."""
        # 1. Native Cartesian trajectory planning if supported by backend (e.g. cuRobo)
        if hasattr(self.backend, "plan_cartesian") and callable(getattr(self.backend, "plan_cartesian")):
            try:
                traj = self.backend.plan_cartesian(q_start, T_target_link0, timeout=timeout)
                if traj is not None:
                    traj.arm = self.arm
                    traj.joint_names = self.joint_names
                    return traj
            except Exception as e:
                print(f"[OpenArmPlanner] Native Cartesian planning failed ({e}), falling back to SSIK IK...")

        # 2. Analytical SSIK IK solving + joint goal planning fallback
        t_ik0 = time.perf_counter()
        candidates = self.solve_ik(T_target_link0, q_seed=q_start)
        t_ik = time.perf_counter() - t_ik0

        if not candidates:
            return None

        # Filter candidates by collision check
        valid_candidates = [q for q in candidates if self.collision_checker.is_valid(q)]
        if not valid_candidates:
            return None

        # Pick nearest collision-free solution
        q_goal = valid_candidates[0]

        traj = self.plan_to_joint_goal(q_start, q_goal, timeout=timeout)
        if traj is not None:
            traj.metadata["ik_solve_ms"] = t_ik * 1000.0
            traj.metadata["ik_candidate_count"] = len(candidates)
        return traj

    def _retime_path(self, path: list[np.ndarray], plan_time: float = 0.0) -> PlannedTrajectory | None:
        """Retime waypoints into a smooth trajectory using TOPP-RA."""
        path_arr = np.array(path, dtype=float)
        v_lim = np.array([self.v_max] * self.robot_model.dof)
        a_lim = np.array([self.a_max] * self.robot_model.dof)

        if _HAVE_TOPPRA:
            ss = np.linspace(0, 1, len(path_arr))
            path_interp = toppra.SplineInterpolator(ss, path_arr)
            pc_vel = constraint.JointVelocityConstraint(v_lim)
            pc_acc = constraint.JointAccelerationConstraint(a_lim)

            gridpoints = np.linspace(0, 1, max(100, len(path_arr)))
            instance = toppra.algorithm.TOPPRA([pc_vel, pc_acc], path_interp, gridpoints=gridpoints)
            jnt_traj = instance.compute_trajectory(0.0, 0.0)
            if jnt_traj is None:
                return None

            duration = jnt_traj.duration
            sample_times = np.linspace(0, duration, max(10, int(duration * 100)))
            positions = np.array([jnt_traj(t) for t in sample_times])
            velocities = np.array([jnt_traj(t, 1) for t in sample_times])
            accelerations = np.array([jnt_traj(t, 2) for t in sample_times])
        else:
            diffs = np.abs(np.diff(path_arr, axis=0))
            seg_durations = np.max(diffs / np.maximum(self.v_max, 1e-3), axis=1)
            seg_durations = np.maximum(seg_durations, 0.05)
            cum_times = np.insert(np.cumsum(seg_durations), 0, 0.0)
            duration = float(cum_times[-1])
            sample_times = np.linspace(0, duration, max(10, int(duration * 100)))
            positions = np.zeros((len(sample_times), self.robot_model.dof))
            for j in range(self.robot_model.dof):
                positions[:, j] = np.interp(sample_times, cum_times, path_arr[:, j])
            velocities = np.zeros_like(positions)
            if len(sample_times) > 1:
                dt = sample_times[1] - sample_times[0]
                velocities[1:] = np.diff(positions, axis=0) / dt
            accelerations = np.zeros_like(positions)

        # Validate clearance along trajectory
        min_clearance = float('inf')
        for q in positions[::5]:  # subsample check at 20 Hz
            if not self.collision_checker.is_valid(q):
                return None
            min_clearance = min(min_clearance, self.collision_checker.min_clearance())

        return PlannedTrajectory(
            arm=self.arm,
            joint_names=self.joint_names,
            times=sample_times,
            positions=positions,
            velocities=velocities,
            accelerations=accelerations,
            duration=duration,
            min_clearance_mm=min_clearance,
            metadata={"plan_time_ms": plan_time * 1000.0, "waypoints": len(path)},
        )

    def visualize_trajectory(self, traj: PlannedTrajectory, playback_speed: float = 1.0) -> None:
        """Play the trajectory inside the MuJoCo interactive passive viewer."""
        try:
            import mujoco.viewer
        except ImportError:
            print("Interactive viewer not available on this system.")
            return

        with mujoco.viewer.launch_passive(self.model, self.data) as viewer:
            print(f"Playing trajectory ({traj.duration:.2f} s) in 3D viewer (press ESC to exit)...")
            start_wall = time.time()
            while viewer.is_running():
                elapsed = (time.time() - start_wall) * playback_speed
                if elapsed > traj.duration:
                    time.sleep(0.5)
                    start_wall = time.time() # Loop playback
                    continue
                
                pos, _, _ = traj.sample(elapsed)
                for idx, val in zip(self.robot_model.qpos_indices, pos):
                    self.data.qpos[idx] = val
                mujoco.mj_forward(self.model, self.data)
                viewer.sync()
                time.sleep(0.01)
