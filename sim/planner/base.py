"""Base definitions and registry for OpenArm motion planning backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable
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

    @property
    def name(self) -> str:
        """Identifier of this planner backend (e.g. 'cbirrt', 'curobo', 'custom')."""
        ...

    def plan(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> list[np.ndarray] | PlannedTrajectory | None:
        """Plan a path or trajectory from q_start to q_goal in joint space.

        Args:
            q_start: Starting joint configuration (DOF,)
            q_goal: Target joint configuration (DOF,)
            timeout: Maximum planning time in seconds

        Returns:
            Either:
            - A list of waypoint arrays [q0, q1, ..., qN] to be retimed by OpenArmPlanner
            - A pre-timed PlannedTrajectory (e.g. from cuRobo or a trajectory optimizer)
            - None if planning failed.
        """
        ...


# Global registry of planner backend factories
_BACKEND_REGISTRY: dict[str, dict] = {}


def register_backend(
    name: str,
    factory: Callable[..., MotionPlannerBackend],
    description: str = "",
    is_available_fn: Callable[[], bool] | None = None,
) -> None:
    """Register a motion planner backend factory.

    Args:
        name: Unique backend name (e.g. 'cbirrt', 'curobo')
        factory: Callable taking (**kwargs) and returning a MotionPlannerBackend instance
        description: Brief human-readable description
        is_available_fn: Optional callable returning whether prerequisites are met
    """
    _BACKEND_REGISTRY[name.lower()] = {
        "factory": factory,
        "description": description,
        "is_available_fn": is_available_fn or (lambda: True),
    }


def get_available_backends() -> dict[str, dict]:
    """Return dictionary of registered backends with availability status."""
    result = {}
    for name, info in _BACKEND_REGISTRY.items():
        is_avail = False
        try:
            is_avail = bool(info["is_available_fn"]())
        except Exception:
            is_avail = False
        result[name] = {
            "name": name,
            "description": info["description"],
            "available": is_avail,
        }
    return result


def create_backend(name: str, **kwargs) -> MotionPlannerBackend:
    """Instantiate a registered motion planner backend by name."""
    name_clean = name.lower()
    if name_clean not in _BACKEND_REGISTRY:
        avail = list(_BACKEND_REGISTRY.keys())
        raise ValueError(f"Unknown planner backend {name!r}. Registered backends: {avail}")

    info = _BACKEND_REGISTRY[name_clean]
    if not info["is_available_fn"]():
        raise RuntimeError(
            f"Planner backend {name!r} is not available in the current environment. "
            f"Description: {info['description']}"
        )

    return info["factory"](**kwargs)
