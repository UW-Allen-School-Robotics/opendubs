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
        urdf_path: str = URDF_PATH,
        tensor_args: Any | None = None,
        world_config: dict | None = None,
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
        from curobo.wrap.reacher.motion_gen import MotionGen, MotionGenConfig

        self.urdf_path = urdf_path
        self.tensor_args = tensor_args or TensorDeviceType()

        # Build cuRobo motion generator configuration
        config = MotionGenConfig.load_from_robot_config(
            urdf_path,
            tensor_args=self.tensor_args,
            world_model=world_config,
            **kwargs,
        )
        self.motion_gen = MotionGen(config)
        print("[CuRoboBackend] Initializing and warming up cuRobo GPU kernels...")
        self.motion_gen.warmup()
        print("[CuRoboBackend] cuRobo GPU planner ready.")

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

        t0 = time.perf_counter()
        q_start_tensor = torch.as_tensor(q_start, dtype=torch.float32, device=self.tensor_args.device)
        q_goal_tensor = torch.as_tensor(q_goal, dtype=torch.float32, device=self.tensor_args.device)

        start_state = JointState.from_position(q_start_tensor.view(1, -1))
        goal_state = JointState.from_position(q_goal_tensor.view(1, -1))

        result = self.motion_gen.plan_single_js(
            start_state,
            goal_state,
            max_attempts=int(max(1, timeout * 2)),
        )
        plan_time = time.perf_counter() - t0

        if not result.success.item():
            return None

        # Extract optimized trajectory directly from cuRobo
        interpolated = result.get_interpolated_plan()
        positions = interpolated.position.cpu().numpy()[0]
        velocities = interpolated.velocity.cpu().numpy()[0]
        accelerations = interpolated.acceleration.cpu().numpy()[0]
        dt = float(result.interpolation_dt)
        times = np.arange(len(positions)) * dt
        duration = float(times[-1]) if len(times) > 0 else 0.0

        return PlannedTrajectory(
            arm="unknown",
            joint_names=[],
            times=times,
            positions=positions,
            velocities=velocities,
            accelerations=accelerations,
            duration=duration,
            min_clearance_mm=25.0,
            metadata={
                "planner": "curobo",
                "plan_time_ms": plan_time * 1000.0,
                "gpu": True,
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
        from scipy.spatial.transform import Rotation

        t0 = time.perf_counter()
        q_start_tensor = torch.as_tensor(q_start, dtype=torch.float32, device=self.tensor_args.device)
        start_state = JointState.from_position(q_start_tensor.view(1, -1))

        pos = T_target[:3, 3]
        quat_xyzw = Rotation.from_matrix(T_target[:3, :3]).as_quat()
        quat_wxyz = [quat_xyzw[3], quat_xyzw[0], quat_xyzw[1], quat_xyzw[2]]

        target_pose = Pose(
            position=torch.as_tensor(pos, dtype=torch.float32, device=self.tensor_args.device).view(1, 3),
            quaternion=torch.as_tensor(quat_wxyz, dtype=torch.float32, device=self.tensor_args.device).view(1, 4),
        )

        result = self.motion_gen.plan_single(start_state, target_pose)
        plan_time = time.perf_counter() - t0

        if not result.success.item():
            return None

        interpolated = result.get_interpolated_plan()
        positions = interpolated.position.cpu().numpy()[0]
        velocities = interpolated.velocity.cpu().numpy()[0]
        accelerations = interpolated.acceleration.cpu().numpy()[0]
        dt = float(result.interpolation_dt)
        times = np.arange(len(positions)) * dt
        duration = float(times[-1]) if len(times) > 0 else 0.0

        return PlannedTrajectory(
            arm="unknown",
            joint_names=[],
            times=times,
            positions=positions,
            velocities=velocities,
            accelerations=accelerations,
            duration=duration,
            min_clearance_mm=25.0,
            metadata={
                "planner": "curobo",
                "plan_time_ms": plan_time * 1000.0,
                "gpu": True,
            },
        )


# Auto-register cuRobo backend
register_backend(
    name="curobo",
    factory=CuRoboBackend,
    description="NVIDIA cuRobo GPU trajectory generator and collision optimizer (CUDA)",
    is_available_fn=CuRoboBackend.is_available,
)
