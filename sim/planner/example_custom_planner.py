#!/usr/bin/env python3
"""Example demonstrating how to write and plug in your own custom motion planner."""
from __future__ import annotations

import os
import sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(SIM_DIR)
for p in (REPO, SIM_DIR, os.path.join(SIM_DIR, "ik_artifacts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from sim.planner import OpenArmPlanner, MotionPlannerBackend, PlannedTrajectory


class MyCustomInterpolationPlanner(MotionPlannerBackend):
    """A simple custom motion planner that performs collision-checked linear joint interpolation.

    You can replace this with:
      - Your own custom RRT / PRM implementation
      - cuRobo GPU trajectory generator
      - TrajOpt / OMPL / MoveIt interface
    """

    def __init__(self, collision_checker, steps: int = 30):
        self.collision_checker = collision_checker
        self.steps = steps

    def plan(
        self,
        q_start: np.ndarray,
        q_goal: np.ndarray,
        timeout: float = 5.0,
    ) -> list[np.ndarray] | None:
        """Custom plan method. Returns list of waypoints, or None if in collision."""
        print(f"[CustomPlanner] Planning from {np.round(np.degrees(q_start), 1)} to {np.round(np.degrees(q_goal), 1)}...")
        
        # Generate linear waypoints in joint space
        alphas = np.linspace(0.0, 1.0, self.steps)
        path = [(1.0 - a) * q_start + a * q_goal for a in alphas]

        # Verify each waypoint is collision-free
        for i, q in enumerate(path):
            if not self.collision_checker.is_valid(q):
                print(f"[CustomPlanner] Waypoint {i}/{self.steps} collided! Rejecting path.")
                return None

        print(f"[CustomPlanner] Success! Generated {len(path)} collision-free waypoints.")
        return path


def main():
    print("=== Testing Custom Motion Planner Plugin ===")

    # 1. Initialize the OpenArm planner
    planner = OpenArmPlanner(arm="left")

    # 2. Inject your custom planner backend
    my_backend = MyCustomInterpolationPlanner(
        collision_checker=planner.collision_checker,
        steps=25,
    )
    planner.set_backend(my_backend)
    print("Injected MyCustomInterpolationPlanner successfully!")

    # 3. Plan to a joint goal
    q_start = np.zeros(7)
    q_goal = np.radians([0.0, -20.0, 0.0, 45.0, 0.0, 15.0, 0.0])

    traj = planner.plan_to_joint_goal(q_start, q_goal)
    if traj is not None:
        print(f"\n[✓] Custom planner produced valid trajectory!")
        print(f"    - Duration:      {traj.duration:.2f} s")
        print(f"    - Min clearance: {traj.min_clearance_mm:.1f} mm")
        print(f"    - Waypoints:     {len(traj.positions)}")


if __name__ == "__main__":
    main()
