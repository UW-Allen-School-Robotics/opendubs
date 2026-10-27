"""Is a pose inside the arm's reach: the question the follow behaviours ask of every target, so that a target outside the workspace is not followed.

A pose is reachable when some self-motion loop through it has an arc inside the joint box (`self_motion_charts.manifold.from_charts`, the charts of ssik's closed-form solve for an SRS arm).
That is not what `Manipulator.solve` answers: it returns a few sampled swivel angles and does not test the limits, and it takes 70 ms to say no. This takes about 2 ms either way on the
OpenArm, so the follow behaviours ask it at 50 Hz. A pose that is kinematically unreachable and a pose that is reachable only outside the joint box both answer no.

ssik and self_motion_charts are imported when a `Reach` is built, not here: the arm service runs without them (`build` returns None and says why), and then nothing is gated.
"""
from __future__ import annotations

import numpy as np


def movable_names(system) -> list[str]:
    """The names of the system's movable joints, in joint-index order (`get_joint_names` also lists the fixed ones)."""
    return [name for name in system.get_joint_names() if hasattr(system.get_joint(name), "get_axis")]


class Reach:
    """The reach of one arm's chain, from the zero-pose frames of the model itself (so it agrees with gafro's own forward kinematics by construction)."""

    def __init__(self, system, side: str):
        import ssik
        from self_motion_charts import arms, manifold

        self._arms, self._manifold = arms, manifold
        task = system.get_task_space(side)
        indices = [int(i) for i in task.get_joint_indices()]
        names = movable_names(system)
        names = [names[i] for i in indices]
        dof = system.get_dof()
        frames = system.compute_forward_kinematics(np.zeros(dof))
        poses = [np.asarray(frames.get_joint_pose(name).to_transformation_matrix()) for name in names]
        end = np.asarray(task.compute_ee_motor(np.zeros(len(indices))).to_transformation_matrix())
        axes = []
        for name in names:
            b = np.asarray(system.get_joint(name).get_axis(), dtype=float)
            v = np.array([b[2], -b[1], b[0]])          # the dual of the generator (e12, e13, e23): the axis
            axes.append(v / np.linalg.norm(v))
        transforms = [poses[0]] + [np.linalg.inv(poses[i - 1]) @ poses[i] for i in range(1, len(poses))] + [np.linalg.inv(poses[-1]) @ end]
        self.names = names
        self.lower = np.asarray(system.get_joint_limits_min().coefficients(), dtype=float)[indices]
        self.upper = np.asarray(system.get_joint_limits_max().coefficients(), dtype=float)[indices]
        self.manipulator = ssik.Manipulator.from_transforms(np.array(transforms), joint_axis=np.array(axes), limits=np.stack([self.lower, self.upper], axis=1))
        self.solver = self.manipulator.solver_name

    def reachable(self, motor) -> bool:
        """Whether the hand can hold `motor` (a gafro Motor, in the base frame) with every joint inside its limits."""
        matrix = np.asarray(motor.to_transformation_matrix(), dtype=float)
        try:
            family = self._arms.charts_at(self.manipulator, matrix)
            components = self._manifold.from_charts(family, self.lower, self.upper, exact=False)
        except Exception:  # noqa: BLE001 -- a pose the charts cannot be made at is not one to follow
            return False
        return any(getattr(component, "arcs", None) for component in components)


def build(system, side: str, log=print) -> Reach | None:
    """The `Reach` of `side`, or None (and why, to `log`) where ssik or self_motion_charts is not installed or the arm is not one they chart."""
    try:
        reach = Reach(system, side)
    except Exception as error:  # noqa: BLE001
        log(f"no reach gate ({type(error).__name__}: {error}): a target outside the workspace is followed as far as the joint limits allow")
        return None
    log(f"reach gate: {reach.solver}")
    return reach


class Gate:
    """Whether to follow: the reach test on each target, debounced, so that one tick on the wrong side of the boundary does not switch the behaviour.

    `update(motor)` is one evaluation; `ok` is the debounced answer (True until `ticks` evaluations in a row say no, and back to True after `ticks` in a row say yes).
    """

    def __init__(self, reach, ticks: int = 3):
        self.reach, self.ticks = reach, ticks
        self.ok, self._run = True, 0

    def update(self, motor) -> bool:
        answer = self.reach.reachable(motor)
        self._run = self._run + 1 if answer != self.ok else 0
        if self._run >= self.ticks:
            self.ok, self._run = answer, 0
        return self.ok
