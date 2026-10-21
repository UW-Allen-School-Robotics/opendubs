#!/usr/bin/env python3
"""Check the arm's gafro config against the real model (description/openarm/gafro/): every controller in controllers.yaml builds for both
arms, the model carries the head cap on joint 2 (the choreography's, mirrored) and nothing else changed, and the hardware config adds
no second cap.

    ~/coding/lab/venv/bin/python software/openarm/gafro/check.py [--model path/to/model.xml]

Needs gafro (the public one) and openarm_assets importable. It builds the controllers; it does not run them, and it does not touch
an arm or the simulator.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "software", "choreo"))


def controllers_build(system, controllers: dict) -> list[str]:
    """Build every controller for each arm. Returns the problems found."""
    import gafro as g
    from gafro import control
    from gafro.control import spec
    from gafro.robot import JointDomain

    problems = []
    for side in ("left", "right"):
        task_space = system.get_task_space(side)
        domain = JointDomain.from_task_space(system, task_space)
        q = np.asarray(system.get_default_configuration(), dtype=float)
        arm = q[np.asarray(task_space.get_joint_indices())]
        pose = task_space.compute_ee_motor(arm)

        reference = control.MotorReferenceSource(pose, horizon=0.02)
        posture = control.JointReferenceSource(q.copy(), horizon=0.02)

        for name, description in controllers.items():
            supplied = {key: kind for key, kind in description.get("inputs", {}).items()}
            try:
                controller, _ = spec.build(
                    description, supplied,
                    dict(system=system, task_space=task_space, task_space_name=side, domain=domain, configuration=q,
                         reference=pose, reference_source=reference, posture_source=posture, posture_initial=q.copy()),
                    name)
                print(f"  {side:5} {name:10} builds ({str(controller.get_command_mode()).rsplit('.', 1)[-1].lower()} command)")
            except Exception as error:  # noqa: BLE001 -- report whatever the build says
                problems.append(f"{side} {name}: {error}")
    return problems


def head_cap_in_model(system, hardware: dict) -> list[str]:
    """The model carries the head cap -- the choreography's measured contact less its margin, mirrored -- on joint 2 only, and the
    hardware config adds no second one. The joint's own limits are what the controllers' joint-limit guard reads."""
    import cue
    import openarm_assets.assembly as assembly

    cap = math.radians(cue.SHOULDER_HEAD_CONTACT_DEG - cue.SHOULDER_MARGIN_DEG)
    base = assembly.build_openarm()
    problems = []

    for side, bound, sign in (("left", "position_lower", -1.0), ("right", "position_upper", 1.0)):
        for joint in range(1, 8):
            name = f"openarm_{side}_joint{joint}"
            have = system.get_joint(name).get_limits()
            arm = base.joint(name).range

            if joint == 2:
                got = getattr(have, bound)
                if abs(got - sign * cap) > 1e-4:
                    problems.append(f"{name}: {bound} is {got:.4f}, the choreography's cap is {sign * cap:.4f} "
                                    f"({cue.SHOULDER_HEAD_CONTACT_DEG:g} - {cue.SHOULDER_MARGIN_DEG:g} deg)")
                other = "position_upper" if bound == "position_lower" else "position_lower"
                if abs(getattr(have, other) - arm[0 if other == "position_lower" else 1]) > 1e-4:
                    problems.append(f"{name}: {other} should be the arm's own, not capped")
            elif abs(have.position_lower - arm[0]) > 1e-4 or abs(have.position_upper - arm[1]) > 1e-4:
                problems.append(f"{name}: the range should be the arm's own [{arm[0]:.4f}, {arm[1]:.4f}], only joint 2 is capped")

        group = hardware.get(f"{side}_arm", {})
        for key in ("lower_limits", "upper_limits"):
            if any(abs(v) != math.inf for v in group.get(key, [])):
                problems.append(f"{side}_arm sets {key} in hardware.example.yaml: the cap is the model's, a second one would drift")
    return problems


def guard_reacts_to_cap(model: str, controllers: dict) -> list[str]:
    """The `track` controller's joint-limit guard sees the cap: at the same state, the torque on joint 2 near the cap differs from the one
    the arm's own, uncapped model gives, and far from it is the same."""
    import gafro as g
    from gafro import control
    from gafro.control import spec
    from gafro.robot import JointDomain
    from openarm_assets import get_generated_model_path

    cap = math.radians(__import__("cue").SHOULDER_HEAD_CONTACT_DEG - __import__("cue").SHOULDER_MARGIN_DEG)
    description = controllers["track"]

    def torque_on_j2(path: str, j2: float) -> float:
        system = g.SystemSerialization.load(path)
        task_space = system.get_task_space("right")
        domain = JointDomain.from_task_space(system, task_space)
        q = np.asarray(system.get_default_configuration(), dtype=float)
        index = np.asarray(task_space.get_joint_indices())
        q[index] = np.array([0.2, j2, 0.1, 1.2, 0.0, 0.3, 0.0])
        pose = task_space.compute_ee_motor(q[index])
        controller, _ = spec.build(
            description, {"reference": "pose", "posture": "joints"},
            dict(system=system, task_space=task_space, task_space_name="right", domain=domain, configuration=q, reference=pose,
                 reference_source=control.MotorReferenceSource(pose, horizon=0.02),
                 posture_source=control.JointReferenceSource(q.copy(), horizon=0.02), posture_initial=q.copy()), "track")
        state = control.SystemState(system.get_dof())
        state.set_position(q)
        state.set_velocity(np.zeros(system.get_dof()))
        state.set_torque(np.zeros(system.get_dof()))
        return float(controller.step(state, 0.002)[1])

    arm = str(get_generated_model_path("bimanual"))
    far, near = 1.0, cap - 0.1
    problems = []
    if abs(torque_on_j2(model, far) - torque_on_j2(arm, far)) > 1e-6:
        problems.append("far from the cap the capped and the arm's own model give different joint 2 torques")
    difference = torque_on_j2(model, near) - torque_on_j2(arm, near)
    print(f"  guard: 0.1 rad from the cap the joint 2 torque is {difference:+.2f} N m from what the uncapped model gives")
    if difference > -1.0:  # the right arm's cap is an upper bound: the guard pushes the joint back down
        problems.append(f"0.1 rad from the cap the guard changes the joint 2 torque by {difference:+.2f} N m: it does not see the cap")
    return problems


def ready_keyframe(system) -> list[str]:
    """The model's `ready` keyframe is the robot's ready pose (tools/arm_gafro_model.py), not the arm's own nearly straight one."""
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import arm_gafro_model as generator

    keyframe = system.get_keyframe("ready")
    if keyframe is None:
        return ["the model has no ready keyframe"]
    names = [a.joint_name for a in system.get_actuators()]
    qpos = np.asarray(keyframe.qpos, dtype=float)
    problems = []
    for name, value in generator.ready_pose().items():
        got = float(qpos[names.index(name)])
        if abs(got - value) > 1e-6:
            problems.append(f"ready keyframe: {name} is {got:.4f}, the ready pose has {value:.4f} (regenerate with tools/arm_gafro_model.py)")
    return problems


def groups_exist(system, hardware: dict) -> list[str]:
    known = {group.get_name() for group in system.get_control_groups()}
    return [f"hardware group '{name}' is not a control group of the model ({sorted(known)})" for name in hardware if name not in known]


def bimanual_engage(system, controllers: dict) -> list[str]:
    """The `bimanual` task space spans both arms (14 joints) and `engage` builds over it: `engage.py --side both` is one controller over both,
    because a RobotInterface runs one and a stage binds to one task space."""
    from gafro.control import spec
    from gafro.robot import JointDomain
    import gafro.control as control

    if "bimanual" not in system.get_task_space_names():
        return ["no `bimanual` task space in the model (openarm_assets declares it when both arms are built)"]

    task_space = system.get_task_space("bimanual")
    arms = sorted(i for side in ("left", "right") for i in system.get_task_space(side).get_joint_indices())
    if sorted(task_space.get_joint_indices()) != arms:
        return ["the `bimanual` task space is not exactly the two arms' joints"]

    q = np.asarray(system.get_default_configuration(), dtype=float)
    right = system.get_task_space("right")
    pose = right.compute_ee_motor(q[np.asarray(right.get_joint_indices())])
    description = controllers["engage"]
    try:
        spec.build(description, dict(description["inputs"]),
                   dict(system=system, task_space=task_space, task_space_name="bimanual", domain=JointDomain.from_task_space(system, task_space),
                        configuration=q, reference=pose, reference_source=control.MotorReferenceSource(pose, horizon=0.02),
                        posture_source=control.JointReferenceSource(q.copy(), horizon=0.02), posture_initial=q.copy()),
                   "engage")
    except Exception as error:  # noqa: BLE001 -- report whatever the build says
        return [f"bimanual engage: {error}"]
    print("  both  engage     builds over the bimanual task space (14 joints)")
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=os.path.join(REPO, "description", "openarm", "gafro", "openarm_bimanual.xml"),
                        help="the arm model (default: this robot's, built by tools/arm_gafro_model.py)")
    args = parser.parse_args()

    import gafro as g

    system = g.SystemSerialization.load(args.model)
    controllers = yaml.safe_load(open(os.path.join(HERE, "controllers.yaml")))["controllers"]
    hardware = yaml.safe_load(open(os.path.join(HERE, "hardware.example.yaml")))

    print(f"model: {args.model}")
    problems = groups_exist(system, hardware) + head_cap_in_model(system, hardware) + controllers_build(system, controllers) + guard_reacts_to_cap(args.model, controllers) + ready_keyframe(system) + bimanual_engage(system, controllers)

    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
