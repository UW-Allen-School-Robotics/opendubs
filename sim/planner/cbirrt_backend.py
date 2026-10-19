"""CBiRRT motion planner backend using sscbirrt."""
from __future__ import annotations

import numpy as np
from sscbirrt import CBiRRT, CBiRRTConfig

from .base import MotionPlannerBackend, register_backend


class CBiRRTBackend(MotionPlannerBackend):
    """Wraps Personal Robotics Lab's sscbirrt CBiRRT planner."""

    def __init__(
        self,
        robot_model,
        collision_checker,
        ik_solver=None,
        step_size: float = 0.05,
        max_iterations: int = 2000,
        smoothing_iterations: int = 50,
    ):
        self.robot_model = robot_model
        self.collision_checker = collision_checker
        self.ik_solver = ik_solver
        self.step_size = step_size
        self.max_iterations = max_iterations
        self.smoothing_iterations = smoothing_iterations

    @property
    def name(self) -> str:
        return "cbirrt"

    def plan(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> list[np.ndarray] | None:
        cfg = CBiRRTConfig(
            step_size=self.step_size,
            max_iterations=self.max_iterations,
            timeout=timeout,
            smoothing_iterations=self.smoothing_iterations,
        )
        planner = CBiRRT(
            robot=self.robot_model,
            ik=self.ik_solver,
            collision_checker=self.collision_checker,
            config=cfg,
        )
        return planner.plan(start=q_start, goal=q_goal)


# Auto-register CBiRRT backend
register_backend(
    name="cbirrt",
    factory=CBiRRTBackend,
    description="PRL CBiRRT sampling-based path planner (CPU)",
    is_available_fn=lambda: True,
)
