"""Where the arm should be a moment from now: the posture reference of `follow_pose`, planned over a short horizon of hand poses on the arm's self-motion charts.

The posture stage of `follow_pose` pulls the redundancy toward a posture; held at the start posture it knows nothing of what the hand is about to do. Here the horizon is the
hand's next poses -- from where it is toward the target at the pose stage's speed limit, then the target's own twist carried on -- and the posture is the global route through
the chart bundle of those poses (`self_motion_charts.path.PathRedundancy`: dynamic programming over every pose's loops, holdable postures only, least joint travel). Where a naive
resolved-rate controller meets a joint stop or a near-singular wrist partway along a long sweep, the route chose the other arc before it got there. Measured on the OpenArm in
simulation, over random hand paths of about 1 rad of joint displacement: naive IK reached 20-22 of 25 poses on two of ten where the route reached all 25, and held sigma_min
0.059 against 0.027 on another; on paths of 0.5 rad all routes agree, so this changes nothing for small tracking motions.

Only numpy and `self_motion_charts` (and `reach.Reach` for the ssik chain) are used: nothing here knows of the arm service, so it moves with `reach.py` to a repository of its own.
"""
from __future__ import annotations

import types

import numpy as np

STEP = 0.1                      # s between the horizon's poses
HORIZON = 10                    # poses ahead (1 s)
LOOKAHEAD = 3                   # the posture asked for is the route's, this many poses ahead (0.3 s)
SPEED = (0.5, 1.5)              # m/s, rad/s: the pose stage's own limits (controllers.yaml), how fast the hand approaches a far target


class Planner:
    def __init__(self, reach, system, side: str, horizon: int = HORIZON, step: float = STEP, lookahead: int = LOOKAHEAD, samples: int = 96, threads: int = 2):
        from self_motion_charts import path

        self._path, self.horizon, self.step, self.lookahead, self.samples, self.threads = path, horizon, step, lookahead, samples, threads
        task = system.get_task_space(side)
        self.indices = [int(i) for i in task.get_joint_indices()]
        names = list(reach.names)
        # The few things smc's path module reads of a model. bridge.Model builds them from a description file; here the arm is the live system the service already holds.
        self.model = types.SimpleNamespace(system=system, arm=types.SimpleNamespace(chain_name=task.get_kinematic_chain_name()), manipulator=reach.manipulator,
                                           lower=reach.lower, upper=reach.upper, joint_names=lambda: names,
                                           self_motion=reach.manipulator.self_motion, fk=reach.manipulator.fk)

    def poses(self, hand, target, twist) -> list:
        """The horizon: the hand toward `target` (gafro Motors) at the speed limits, then `target` moving with `twist` (tool frame, the order of Motor.log)."""
        from gafro import Motor

        error = np.asarray(hand.inverse().multiply(target).log(), dtype=float).ravel()
        steps = max(1, int(np.ceil(max(np.linalg.norm(error[3:]) / (SPEED[0] * self.step), np.linalg.norm(error[:3]) / (SPEED[1] * self.step)))))
        out = []
        for k in range(self.horizon + 1):
            if k <= steps:
                pose = hand.multiply(Motor.exp(*(error * min(k / steps, 1.0))))
            else:
                pose = target.multiply(Motor.exp(*(np.asarray(twist, dtype=float) * (k - steps) * self.step)))
            out.append(np.asarray(pose.to_transformation_matrix(), dtype=float))
        return out

    def posture(self, q_arm, hand, target, twist):
        """The arm's posture (its joints) `lookahead` poses along the global route, or None where the route does not get that far (the target leaves the reach: the gate's business)."""
        route = self._path.PathRedundancy(self.model, self.poses(hand, target, twist), np.asarray(q_arm, dtype=float), samples=self.samples, threads=self.threads, kinetic=False,
                                          routes=False)
        try:
            best = route.optimal("global", [route.start()], "joint")
        except ValueError:      # no holdable sample near the arm (it stands on a joint stop): no plan, the posture stays
            return None
        k = min(self.lookahead, int(best.reached) - 1)
        return np.asarray(best.q[k], dtype=float) if k >= 1 else None


def build(reach, system, side: str, log=print) -> Planner | None:
    try:
        return Planner(reach, system, side)
    except Exception as error:  # noqa: BLE001
        log(f"no posture planner ({type(error).__name__}: {error}): the posture stays where the run started it")
        return None
