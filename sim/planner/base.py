"""Base definitions for OpenArm motion planning."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
import numpy as np


@dataclass
class PlannedTrajectory:
    """Retimed joint-space trajectory."""
    arm: str
    joint_names: list[str]
    times: np.ndarray          # (N,) seconds from start
    positions: np.ndarray      # (N, DOF) radians
    velocities: np.ndarray     # (N, DOF) rad/s
    accelerations: np.ndarray  # (N, DOF) rad/s^2
    duration: float
    min_clearance_mm: float
    metadata: dict = field(default_factory=dict)

    def sample(self, t: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Sample (pos, vel, acc) at time t (clamped to [0, duration])."""
        t_clamped = max(0.0, min(float(t), self.duration))
        idx = np.searchsorted(self.times, t_clamped)
        if idx == 0:
            return self.positions[0], self.velocities[0], self.accelerations[0]
        if idx >= len(self.times):
            return self.positions[-1], self.velocities[-1], self.accelerations[-1]
        
        # Linear interpolation between nearest samples
        t0, t1 = self.times[idx - 1], self.times[idx]
        dt = t1 - t0
        alpha = (t_clamped - t0) / dt if dt > 1e-6 else 0.0
        pos = (1.0 - alpha) * self.positions[idx - 1] + alpha * self.positions[idx]
        vel = (1.0 - alpha) * self.velocities[idx - 1] + alpha * self.velocities[idx]
        acc = (1.0 - alpha) * self.accelerations[idx - 1] + alpha * self.accelerations[idx]
        return pos, vel, acc


@runtime_checkable
class MotionPlannerBackend(Protocol):
    """Interface that any motion planner (CBiRRT, custom, cuRobo, etc.) must implement."""

    def plan(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> list[np.ndarray] | None:
        """Plan a path from q_start to q_goal in joint space.

        Args:
            q_start: Starting joint configuration (DOF,)
            q_goal: Target joint configuration (DOF,)
            timeout: Maximum planning time in seconds

        Returns:
            List of waypoint arrays [q0, q1, ..., qN], or None if no path found.
        """
        ...
