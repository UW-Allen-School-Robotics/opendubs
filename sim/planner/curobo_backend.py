"""NVIDIA cuRobo GPU Motion Planner Backend.

Provides a drop-in MotionPlannerBackend implementation wrapping NVIDIA cuRobo.
Computes collision-free, time-optimal joint trajectories directly on CUDA GPU.
"""
from __future__ import annotations

import os
import time
from typing import Any
import numpy as np

from .base import MotionPlannerBackend, PlannedTrajectory, register_backend

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
URDF_PATH = os.path.join(REPO, "description", "openarm", "urdf", "v1.urdf")


class CuRoboBackend(MotionPlannerBackend):
    """GPU-accelerated trajectory generation backend using NVIDIA cuRobo."""

    def __init__(
        self,
        robot_model=None,
        collision_checker=None,
        ik_solver=None,
        arm: str = "right",
        urdf_path: str = URDF_PATH,
        tensor_args: Any | None = None,
        world_config: dict | None = None,
        interpolation_dt: float = 0.02,
        **kwargs,
    ):
        if not self.is_available():
            raise ImportError(
                "cuRobo is not available in the current environment.\n"
                "Requirements:\n"
                "  1. NVIDIA GPU with CUDA support\n"
                "  2. PyTorch with CUDA (e.g. torch >= 2.0 with CUDA >= 11.8)\n"
                "  3. cuRobo (pip install curobo or see https://curobo.org)\n"
            )

        import torch
        from curobo.types.base import TensorDeviceType
        from curobo.types.robot import RobotConfig
        from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig

        self.arm = arm
        self.urdf_path = urdf_path
        self.collision_checker = collision_checker
        self.ik_solver = ik_solver
        self.tensor_args = tensor_args or TensorDeviceType()
        self.interpolation_dt = interpolation_dt

        # Base and End-Effector link selection based on arm
        self.base_link = "openarm_body_link0"
        self.ee_link = f"openarm_{arm}_hand_tcp"

        # Load OpenArm robot config directly from URDF into cuRobo
        self.robot_cfg = RobotConfig.from_basic(
            urdf_path,
            base_link=self.base_link,
            ee_link=self.ee_link,
            tensor_args=self.tensor_args,
        )
        self.joint_names = list(self.robot_cfg.cspace.joint_names)

        # Build cuRobo motion generator configuration
        config = MotionGenConfig.load_from_robot_config(
            self.robot_cfg,
            world_model=world_config,
            tensor_args=self.tensor_args,
            interpolation_dt=self.interpolation_dt,
        )
        self.motion_gen = MotionGen(config)
        self.motion_gen.warmup()

    @property
    def name(self) -> str:
        return "curobo"

    @staticmethod
    def is_available() -> bool:
        """Check whether cuRobo and a CUDA-capable GPU are accessible."""
        try:
            import torch
            if not torch.cuda.is_available():
                return False
            import curobo  # noqa: F401
            from curobo.curobolib import lbfgs_step_cu  # noqa: F401
            return True
        except (ImportError, Exception):
            return False

    def plan(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> PlannedTrajectory | None:
        """Generate a full time-optimal collision-free trajectory using cuRobo."""
        import torch
        from curobo.types.robot import JointState
        from curobo.wrap.reacher.motion_gen import MotionGenPlanConfig

        t0 = time.perf_counter()
        q_start_tensor = torch.as_tensor(q_start, dtype=torch.float32, device=self.tensor_args.device)
        q_goal_tensor = torch.as_tensor(q_goal, dtype=torch.float32, device=self.tensor_args.device)

        start_state = JointState.from_position(q_start_tensor.view(1, -1), joint_names=self.joint_names)
        goal_state = JointState.from_position(q_goal_tensor.view(1, -1), joint_names=self.joint_names)

        plan_cfg = MotionGenPlanConfig(max_attempts=int(max(1, timeout * 2)))
        result = self.motion_gen.plan_single_js(start_state, goal_state, plan_cfg)
        plan_time = time.perf_counter() - t0

        if not result.success.item():
            return None

        interpolated = result.get_interpolated_plan()

        def _to_2d_numpy(t):
            if t is None:
                return None
            arr = t.cpu().numpy()
            if arr.ndim == 3:
                arr = arr[0]
            elif arr.ndim == 1:
                arr = arr.reshape(1, -1)
            return arr

        positions = _to_2d_numpy(interpolated.position)
        velocities = _to_2d_numpy(getattr(interpolated, "velocity", None))
        if velocities is None:
            velocities = np.zeros_like(positions)
        accelerations = _to_2d_numpy(getattr(interpolated, "acceleration", None))
        if accelerations is None:
            accelerations = np.zeros_like(positions)

        dt = float(result.interpolation_dt) if hasattr(result, "interpolation_dt") else self.interpolation_dt
        times = np.arange(len(positions)) * dt
        duration = float(times[-1]) if len(times) > 0 else 0.0

        # MuJoCo collision validation if checker is attached
        min_clearance = 25.0
        if self.collision_checker is not None:
            min_clearance = float("inf")
            for q_pt in positions:
                if not self.collision_checker.is_valid(q_pt):
                    # In collision with MuJoCo scene
                    return None
                min_clearance = min(min_clearance, self.collision_checker.min_clearance())
            if min_clearance == float("inf"):
                min_clearance = 25.0

        return PlannedTrajectory(
            arm=self.arm,
            joint_names=self.joint_names,
            times=times,
            positions=positions,
            velocities=velocities,
            accelerations=accelerations,
            duration=duration,
            min_clearance_mm=min_clearance,
            metadata={
                "planner": "curobo",
                "plan_time_ms": plan_time * 1000.0,
                "gpu": True,
                "waypoints": len(positions),
            },
        )

    def plan_cartesian(
        self,
        q_start: np.ndarray,
        T_target: np.ndarray,
        timeout: float = 5.0,
    ) -> PlannedTrajectory | None:
        """Plan directly to a 4x4 Cartesian pose using cuRobo's Pose reacher."""
        import torch
        from curobo.types.math import Pose
        from curobo.types.robot import JointState
        from curobo.wrap.reacher.motion_gen import MotionGenPlanConfig
        from scipy.spatial.transform import Rotation

        t0 = time.perf_counter()
        q_start_tensor = torch.as_tensor(q_start, dtype=torch.float32, device=self.tensor_args.device)
        start_state = JointState.from_position(q_start_tensor.view(1, -1), joint_names=self.joint_names)

        pos = T_target[:3, 3]
        quat_xyzw = Rotation.from_matrix(T_target[:3, :3]).as_quat()
        quat_wxyz = [quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]]

        target_pose = Pose(
            position=torch.as_tensor(pos, dtype=torch.float32, device=self.tensor_args.device).view(1, 3),
            quaternion=torch.as_tensor(quat_wxyz, dtype=torch.float32, device=self.tensor_args.device).view(1, 4),
        )

        plan_cfg = MotionGenPlanConfig(max_attempts=int(max(1, timeout * 2)))
        result = self.motion_gen.plan_single(start_state, target_pose, plan_cfg)
        plan_time = time.perf_counter() - t0

        if not result.success.item():
            return None

        interpolated = result.get_interpolated_plan()
        positions = _to_2d_numpy(interpolated.position)
        velocities = _to_2d_numpy(getattr(interpolated, "velocity", None))
        if velocities is None:
            velocities = np.zeros_like(positions)
        accelerations = _to_2d_numpy(getattr(interpolated, "acceleration", None))
        if accelerations is None:
            accelerations = np.zeros_like(positions)

        dt = float(result.interpolation_dt) if hasattr(result, "interpolation_dt") else self.interpolation_dt
        times = np.arange(len(positions)) * dt
        duration = float(times[-1]) if len(times) > 0 else 0.0

        min_clearance = 25.0
        if self.collision_checker is not None:
            min_clearance = float("inf")
            for q_pt in positions:
                if not self.collision_checker.is_valid(q_pt):
                    return None
                min_clearance = min(min_clearance, self.collision_checker.min_clearance())
            if min_clearance == float("inf"):
                min_clearance = 25.0

        return PlannedTrajectory(
            arm=self.arm,
            joint_names=self.joint_names,
            times=times,
            positions=positions,
            velocities=velocities,
            accelerations=accelerations,
            duration=duration,
            min_clearance_mm=min_clearance,
            metadata={
                "planner": "curobo",
                "plan_time_ms": plan_time * 1000.0,
                "gpu": True,
                "waypoints": len(positions),
            },
        )


# Auto-register cuRobo backend
register_backend(
    name="curobo",
    factory=CuRoboBackend,
    description="NVIDIA cuRobo GPU trajectory generator and collision optimizer (CUDA)",
    is_available_fn=CuRoboBackend.is_available,
)
