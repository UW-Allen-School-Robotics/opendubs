"""Routing a move around the robot's body with the arm motion planner, for a recorded show.

live_arms.Planner moves an arm along a smoothed straight line in joint space and refuses a move that
would pass within ik.CLEAR_MIN_MM of the body or the other arm. With a Router, a move refused for
that reason is handed to the arm motion planner (sim/planner: CBiRRT on the CPU, or cuRobo on the
GPU; branch feat/arm-motion-planner-ui, sim/planner/INSTRUCTIONS.md), and its path comes back as a
few waypoints. Planner.plan then checks every leg between them with its own clearance check, for
every head pose in the config, exactly as it checks a straight move; a route that fails is refused.

FOR A SHOW ONLY. The waypoints become arm cues of the recorded show (live_arms.ShowRecorder), which
the publish-time clearance sweep and validator check as the runner will play it. The live arm
service is never given a Router, and a routed Move refuses to become live points.

    OPENDUBS_PLANNER_ROOT   a checkout of the planner branch (its sim/planner is imported from there)
    OPENDUBS_ARCHITECT_PLANNER   cbirrt or curobo turns routing on in show_backend.make_recorder

The planner's joints are model radians, which are radians of live_arms' signed degrees (the same
left-arm mirror as live_arms.to_signed; arm_planner_bridge.choreo_to_model_rad).
"""
from __future__ import annotations

import math
import os
import sys

BACKENDS = ("cbirrt", "curobo")
WAYPOINTS = 4          # taken from the planned path, evenly by sample; the runner splines through them
MARGIN_MM = 5.0        # the planner keeps this much more than ik.CLEAR_MIN_MM, so its route passes our check
TIMEOUT_S = 6.0        # the composer's own planning timeout (arm_planner_bridge.plan_arm_move)
ATTEMPTS = 2           # CBiRRT samples at random: on the twin one try in eight found no path that the next found


class Router:
    """`router(side, start_signed_deg, goal_signed_deg) -> [waypoint signed deg, ...] | None`.
    `planner_for(side)` is a seam for the tests."""

    def __init__(self, backend: str = "cbirrt", root: str | None = None, waypoints: int = WAYPOINTS,
                 timeout_s: float = TIMEOUT_S, planner_for=None):
        if backend not in BACKENDS:
            raise ValueError(f"planner must be one of {', '.join(BACKENDS)}, not {backend!r}")
        self.backend, self.waypoints, self.timeout_s = backend, waypoints, timeout_s
        self._planners: dict = {}
        self.last_error: str | None = None      # why the last call found no route, for the refusal
        if planner_for is None:
            root = root or os.environ.get("OPENDUBS_PLANNER_ROOT")
            if not root or not os.path.isdir(os.path.join(root, "sim", "planner")):
                raise RuntimeError("routing needs the arm motion planner: set OPENDUBS_PLANNER_ROOT to a "
                                   "checkout of feat/arm-motion-planner-ui")
            # This tree's own twin first: sim/planner puts its branch's sim/ at the FRONT of sys.path, and
            # whichever scene and clearance load first are the ones every later check uses.
            import clearance  # noqa: F401
            import ik
            import scene  # noqa: F401
            if root not in sys.path:
                sys.path.append(root)
            from sim.planner import OpenArmPlanner, get_available_backends
            if not get_available_backends().get(backend, {}).get("available"):
                raise RuntimeError(f"the {backend} planner is not available here (sim.planner.get_available_backends)")
            planner_for = lambda side: OpenArmPlanner(arm=side, backend=backend,  # noqa: E731
                                                      min_clearance_mm=ik.CLEAR_MIN_MM + MARGIN_MM)
        self.planner_for = planner_for

    def __call__(self, side: str, start: list, goal: list) -> list | None:
        import numpy as np
        planner = self._planners.get(side) or self._planners.setdefault(side, self.planner_for(side))
        traj = None
        for _ in range(ATTEMPTS):
            try:
                traj = planner.plan_to_joint_goal(np.radians(start), np.radians(goal), timeout=self.timeout_s)
            except (ValueError, RuntimeError) as e:     # e.g. "Goal configuration is in collision": no way around
                self.last_error = str(e)
                return None
            if traj is not None and len(traj.positions) >= 3:
                break
        if traj is None or len(traj.positions) < 3:
            self.last_error = "the planner found no path"
            return None
        n = len(traj.positions)
        picks = sorted({min(n - 2, max(1, round(n * k / (self.waypoints + 1)))) for k in range(1, self.waypoints + 1)})
        return [[round(math.degrees(float(v)), 2) for v in traj.positions[i]] for i in picks]
