#!/usr/bin/env python3
"""What the arm's moves share: the controller builder, the collision check of a straight joint-space line, the permit keeper and the stow pose.

The torque sequence that lived here (engage from stow, handover to `track`, `--park`) is gone with its controllers: torque control does not move this arm. A move is
played in position mode, by arm_service.py (`activate`, `park`, over trajectory.py) and the tools. This module is what they take from it.
"""
from __future__ import annotations

import os
import sys
import threading

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import guards  # noqa: E402

# This repository's own gafro model of the robot: what the tools bind when no --model is given. Four directories up from this file is the repository root.
DEFAULT_MODEL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "description", "openarm", "gafro", "openarm_bimanual.xml")
MARGIN = 0.003          # how much nearer than at its closer end a pair may come along the path, m
CLEARANCE = 0.02        # a pair further apart than this along the whole path is clear, however much nearer than at its ends, m
MIN_DURATION = 2.0      # s, however short the travel


class EngageRefused(guards.Refused):
    """The sequence did not start, or did not finish: the message says why and nothing was left running."""


def load_controllers(path: str | None = None) -> dict:
    with open(path or os.path.join(HERE, "controllers.yaml")) as handle:
        return yaml.safe_load(handle)["controllers"]


def minimum_jerk(fraction: float) -> float:
    """0 to 1 with zero speed at both ends: the arm leaves and arrives at rest."""
    return fraction ** 3 * (10.0 - 15.0 * fraction + 6.0 * fraction ** 2)


def duration_for(start: np.ndarray, target: np.ndarray, speed: float) -> float:
    """Peak joint speed of a minimum-jerk move is 1.875 times its average, so the largest travel sets the time."""
    return max(MIN_DURATION, 1.875 * float(np.max(np.abs(target - start))) / speed)


class PathCheck:
    """Distances between the model's collision geometry along a joint-space line, by MuJoCo's signed distance."""

    def __init__(self, model_path: str, joint_names: list[str]):
        import itertools
        import mujoco

        self.mujoco = mujoco
        self.model = mujoco.MjModel.from_xml_path(model_path)
        self.data = mujoco.MjData(self.model)
        ids = [mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in joint_names]
        self.addresses = self.model.jnt_qposadr[ids]
        self.rest = self.model.key_qpos[0].copy() if self.model.nkey else np.zeros(self.model.nq)

        excluded = set()
        for index in range(self.model.nexclude):
            signature = int(self.model.exclude_signature[index])
            excluded.add(tuple(sorted(((signature >> 16) - 1, (signature & 0xFFFF) - 1))))

        geoms = [g for g in range(self.model.ngeom) if self.model.geom_contype[g] and self.model.geom_conaffinity[g]]
        parent = self.model.body_parentid
        self.pairs = []
        for first, second in itertools.combinations(geoms, 2):
            a, b = int(self.model.geom_bodyid[first]), int(self.model.geom_bodyid[second])
            if a == b or parent[a] == b or parent[b] == a or tuple(sorted((a, b))) in excluded:
                continue
            self.pairs.append((first, second))

    def distances(self, configuration: np.ndarray) -> np.ndarray:
        self.data.qpos[:] = self.rest
        self.data.qpos[self.addresses] = configuration
        self.mujoco.mj_kinematics(self.model, self.data)
        return np.array([self.mujoco.mj_geomDistance(self.model, self.data, a, b, 0.3, None) for a, b in self.pairs])

    def refusal(self, start: np.ndarray, target: np.ndarray, steps: int = 60) -> str | None:
        """The pair that comes nearer along the line than at its closer end, or None."""
        at_start, at_target = self.distances(start), self.distances(target)
        allowed = np.minimum(np.minimum(at_start, at_target) - MARGIN, CLEARANCE)

        for fraction in np.linspace(0.0, 1.0, steps + 1)[1:-1]:
            here = self.distances(start + (target - start) * fraction)
            worse = np.where(here < allowed)[0]
            if worse.size:
                first, second = self.pairs[worse[np.argmin(here[worse] - allowed[worse])]]
                return (f"{self.model.geom(first).name} and {self.model.geom(second).name} come to {1000 * here[worse].min():.0f} mm "
                        f"({fraction:.0%} of the way), nearer than at either end")
        return None


def build(name: str, controllers: dict, system, side: str, configuration: np.ndarray, pose, reference, posture, rate=None):
    from gafro.control import spec
    from gafro.robot import JointDomain

    task_space = system.get_task_space(side)
    description = controllers[name]
    controller, _ = spec.build(
        description, dict(description.get("inputs", {})),
        dict(system=system, task_space=task_space, task_space_name=side, domain=JointDomain.from_task_space(system, task_space),
             configuration=configuration, reference=pose, reference_source=reference, posture_source=posture,
             posture_initial=configuration.copy(), posture_rate_source=rate),
        name)
    return controller


class PermitKeeper:
    """Keeps the driver's permit alive while the sequence runs: the arm holds unless a short permit is being renewed (`require_permit`), so
    a sequence that stops, dies or is interrupted leaves the arm holding within `lease` seconds. Revoked on exit.

    A no-op without `groups` (the simulator has no permit). The driver names its permit after its control group (`left_arm`, `right_arm`).

    `allowed`, if given, is asked before every renewal (guards.motion_gate: CH5 MANUAL or AUTO, the e-stop clear and the deadman, CH6, held): when it says no, or raises, the
    renewal is withheld and the arm holds within `lease`. So a sequence in progress stops when CH5 goes to OFF, the e-stop is pressed or the deadman is released, instead of
    finishing its move.
    `grant` and `revoke` replace the driver's calls, for tests.
    """

    def __init__(self, groups=(), lease: float = 0.5, period: float = 0.1, allowed=None, grant=None, revoke=None):
        self.groups, self.lease, self.period = tuple(groups), lease, period
        self.allowed = allowed
        self.withheld = 0                      # renewals not made because `allowed` said no: how often the sequence was cut short
        self._grant_fn, self._revoke_fn = grant, revoke
        self._stop = threading.Event()
        self._thread = None

    def _grant(self, group):
        if self._grant_fn is not None:
            return self._grant_fn(group, self.lease)
        import openarm_driver

        return openarm_driver.grant_permit(group, self.lease)

    def _revoke(self, group):
        if self._revoke_fn is not None:
            return self._revoke_fn(group)
        import openarm_driver

        return openarm_driver.revoke_permit(group)

    def _may_renew(self) -> bool:
        if self.allowed is None:
            return True
        try:
            return bool(self.allowed())
        except Exception:  # noqa: BLE001 -- a gate that cannot answer is a gate that says no
            return False

    def _run(self):
        while not self._stop.is_set():
            if self._may_renew():
                for group in self.groups:
                    self._grant(group)
            else:
                self.withheld += 1
            self._stop.wait(self.period)

    def start(self):
        if self.groups and self._thread is None:
            if not self._may_renew():
                raise guards.Refused("CH5 does not permit motion, or the e-stop is not clear, or the dashboard did not answer: no permit was granted")
            for group in self.groups:
                self._grant(group)   # before the loop, so an unusable driver fails here and not later
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self):
        """Stop renewing and revoke now: the arms hold from the next cycle."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None

            for group in self.groups:
                self._revoke(group)

    def lapse(self):
        """Stop renewing and let the permit run out by itself, as it does when whatever renews it dies."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def permit_groups(side: str) -> tuple[str, ...]:
    return ("left_arm", "right_arm") if side == "both" else (f"{side}_arm",)


STOW_CONF = os.environ.get("OPENARM_STOW_CONF", "/etc/opendubs/openarm-stow.conf")


def stow_pose(side: str, path: str | None = None) -> np.ndarray | None:
    """One arm's stow pose in radians from the per-robot file (`LEFT_STOW=0,-20,0,95,0,15,0`, degrees), or None if the file or the line is not there."""
    try:
        with open(path or STOW_CONF) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None

    value = None
    for line in lines:
        key, _, rest = line.partition("=")
        if key.strip().upper() == f"{side.upper()}_STOW" and rest.strip():
            value = rest.strip()
    if value is None:
        return None

    degrees = [float(part) for part in value.replace(" ", "").split(",")]
    if len(degrees) != 7:
        raise EngageRefused(f"{side}: the stow pose has {len(degrees)} angles, an arm has 7")
    return np.radians(degrees)
