"""Is a pose inside the arm's reach: the question the follow behaviours ask of every target, so that a target outside the workspace is not followed.

A pose is reachable when some self-motion loop through it has an arc inside the joint box (`self_motion_charts.manifold.from_charts`, the charts of ssik's closed-form solve for an SRS arm)
AND some posture on that arc keeps the arm clear of itself: gafro's own `SelfCollisionObjective` over the model's collision cover (298 pairs on the OpenArm, 0.2 ms a posture, so the arc is
tested every sixth sample first and stops at the first clear one) must report every pair at least `clearance` metres apart, the other arm and the body at the posture `rest` they are in.
That is not what `Manipulator.solve` answers: it returns a few sampled swivel angles and does not test the limits, and it takes 70 ms to say no. The charts take about 2 ms on the
OpenArm, so the follow behaviours ask at 50 Hz. A pose that is kinematically unreachable, one reachable only outside the joint box and one reachable only by a self-collision all answer no.

ssik and self_motion_charts are imported when a `Reach` is built, not here: the arm service runs without them (`build` returns None and says why), and then nothing is gated.
"""
from __future__ import annotations

import numpy as np

CLEARANCE = 0.0                 # m: the least gap between any two balls of the model's collision cover. 0: the cover must not interpenetrate. Normal OpenArm postures have 7-18 mm; in
                                # simulation the cover overlaps by up to 16 mm on poses that track without contact, and the arm stopped on the force limit at 48-50 mm: `--reach-clearance`
STRIDE = 6                      # the arc's samples are tested every this many first


def movable_names(system) -> list[str]:
    """The names of the system's movable joints, in joint-index order (`get_joint_names` also lists the fixed ones)."""
    return [name for name in system.get_joint_names() if hasattr(system.get_joint(name), "get_axis")]


class Reach:
    """The reach of one arm's chain, from the zero-pose frames of the model itself (so it agrees with gafro's own forward kinematics by construction)."""

    def __init__(self, system, side: str, clearance: float = CLEARANCE):
        import ssik
        from gafro import control
        from gafro.objective import SelfCollisionObjective
        from self_motion_charts import arms, manifold

        self._arms, self._manifold, self.clearance = arms, manifold, clearance
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
        self.names, self.indices = names, indices
        self._objective = SelfCollisionObjective(system, task, 0.0)       # margin 0: the clearances are signed distances, the floor is `clearance`
        self._state = control.SystemState(dof)
        self._state.set_velocity(np.zeros(dof))
        self._state.set_torque(np.zeros(dof))
        self._rest = np.zeros(dof)
        self.lower = np.asarray(system.get_joint_limits_min().coefficients(), dtype=float)[indices]
        self.upper = np.asarray(system.get_joint_limits_max().coefficients(), dtype=float)[indices]
        self.manipulator = ssik.Manipulator.from_transforms(np.array(transforms), joint_axis=np.array(axes), limits=np.stack([self.lower, self.upper], axis=1))
        self.solver = self.manipulator.solver_name

    def clear(self, q_arm, rest=None) -> bool:
        """Whether the arm at `q_arm` (its joints), with everything else at `rest` (a whole-system configuration; zeros by default), is `clearance` clear of itself."""
        full = np.array(self._rest if rest is None else rest, dtype=float)
        full[self.indices] = q_arm
        self._state.set_position(full)
        return float(np.asarray(self._objective.get_clearances(self._state), dtype=float).min()) >= self.clearance

    def reachable(self, motor, rest=None) -> bool:
        """Whether the hand can hold `motor` (a gafro Motor, in the base frame) with every joint inside its limits and the arm clear of itself (`rest`: where the rest of the system is)."""
        matrix = np.asarray(motor.to_transformation_matrix(), dtype=float)
        try:
            family = self._arms.charts_at(self.manipulator, matrix)
            components = self._manifold.from_charts(family, self.lower, self.upper, exact=False)
            arcs = [arc for component in components for arc in getattr(component, "arcs", None) or []]
            for coarse in (True, False):        # a coarse look first: most reachable poses have a clear posture among the arc's samples
                for arc in arcs:
                    configurations = list(arc.configurations)
                    for k in range(len(configurations)):
                        if (k % STRIDE == 0) == coarse and self.clear(np.asarray(configurations[k], dtype=float), rest):
                            return True
        except Exception:  # noqa: BLE001 -- a pose the charts cannot be made at is not one to follow
            return False
        return False

    def clear_postures(self, motor, rest=None) -> list:
        """Every clear posture on the self-motion arcs through `motor`: the arm's seven joints, inside the limits and `clearance` clear of itself (empty where `reachable` says no)."""
        found = []
        try:
            family = self._arms.charts_at(self.manipulator, np.asarray(motor.to_transformation_matrix(), dtype=float))
            for component in self._manifold.from_charts(family, self.lower, self.upper, exact=False):
                for arc in getattr(component, "arcs", None) or []:
                    found += [np.asarray(q, dtype=float) for q in arc.configurations if self.clear(np.asarray(q, dtype=float), rest)]
        except Exception:  # noqa: BLE001 -- a pose the charts cannot be made at has no posture
            return []
        return found

    def best_posture(self, motor, q_now, rest=None, margin: float = 0.05):
        """The posture for `motor` to plan to: of the clear ones with at least `margin` rad to every joint limit, the nearest to `q_now` (the shortest move); where none has the margin, the
        one with the most. None if the pose is unreachable."""
        postures = self.clear_postures(motor, rest)
        if not postures:
            return None
        table = np.array(postures)
        room = np.minimum(table - self.lower, self.upper - table).min(axis=1)
        roomy = np.where(room >= margin)[0]
        if not roomy.size:
            return table[int(np.argmax(room))]
        return table[roomy[int(np.argmin(np.linalg.norm(table[roomy] - np.asarray(q_now, dtype=float), axis=1)))]]


def build(system, side: str, log=print, clearance: float = CLEARANCE) -> Reach | None:
    """The `Reach` of `side`, or None (and why, to `log`) where ssik or self_motion_charts is not installed or the arm is not one they chart."""
    try:
        reach = Reach(system, side, clearance)
    except Exception as error:  # noqa: BLE001
        log(f"no reach gate ({type(error).__name__}: {error}): a target outside the workspace is followed as far as the joint limits allow")
        return None
    log(f"reach gate: {reach.solver}, clearance {clearance:g} m")
    return reach


class Gate:
    """Whether to follow: the reach test on each target, debounced, so that one tick on the wrong side of the boundary does not switch the behaviour.

    `update(motor)` is one evaluation; `ok` is the debounced answer (True until `ticks` evaluations in a row say no, and back to True after `ticks` in a row say yes).
    """

    def __init__(self, reach, ticks: int = 3):
        self.reach, self.ticks = reach, ticks
        self.ok, self._run = True, 0

    def update(self, motor, rest=None) -> bool:
        answer = self.reach.reachable(motor, rest)
        self._run = self._run + 1 if answer != self.ok else 0
        if self._run >= self.ticks:
            self.ok, self._run = answer, 0
        return self.ok
