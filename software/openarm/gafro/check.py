#!/usr/bin/env python3
"""Check the arm's gafro config against the real model: every controller in controllers.yaml builds for both arms, and the head cap in
hardware.example.yaml is the choreography's, mirrored, and inside the model's joint ranges.

    ~/coding/lab/venv/bin/python software/openarm/gafro/check.py [--model path/to/openarm_bimanual_decorated.xml]

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


def head_cap_agrees(system, hardware: dict) -> list[str]:
    """The J2 cap is the choreography's measured contact less its margin, mirrored, and inside the model's range."""
    import cue

    cap = math.radians(cue.SHOULDER_HEAD_CONTACT_DEG - cue.SHOULDER_MARGIN_DEG)
    problems = []

    for side, bound, sign in (("left", "lower_limits", -1.0), ("right", "upper_limits", 1.0)):
        group = hardware.get(f"{side}_arm")
        if group is None:
            problems.append(f"hardware.example.yaml has no {side}_arm")
            continue
        value = float(group[bound][1])
        if abs(value - sign * cap) > 1e-4:
            problems.append(f"{side}_arm {bound}[1] is {value:.4f}, the choreography's cap is {sign * cap:.4f} "
                            f"({cue.SHOULDER_HEAD_CONTACT_DEG:g} - {cue.SHOULDER_MARGIN_DEG:g} deg)")
        limits = system.get_joint(f"openarm_{side}_joint2").get_limits()
        if not (limits.position_lower <= value <= limits.position_upper):
            problems.append(f"{side}_arm {bound}[1] = {value:.4f} is outside the model's range "
                            f"[{limits.position_lower:.4f}, {limits.position_upper:.4f}]")

        # Only joint 2 is capped here; every other limit must be open.
        other = "upper_limits" if bound == "lower_limits" else "lower_limits"
        open_bound = -math.inf if bound == "lower_limits" else math.inf
        if any(v != open_bound for i, v in enumerate(group[bound]) if i != 1) or any(abs(v) != math.inf for v in group[other]):
            problems.append(f"{side}_arm: only joint 2's {bound} should be set")
    return problems


def groups_exist(system, hardware: dict) -> list[str]:
    known = {group.get_name() for group in system.get_control_groups()}
    return [f"hardware group '{name}' is not a control group of the model ({sorted(known)})" for name in hardware if name not in known]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help="the arm model (default: openarm_assets' generated bimanual model)")
    args = parser.parse_args()

    import gafro as g
    if args.model is None:
        from openarm_assets import get_generated_model_path
        args.model = str(get_generated_model_path("bimanual"))

    system = g.SystemSerialization.load(args.model)
    controllers = yaml.safe_load(open(os.path.join(HERE, "controllers.yaml")))["controllers"]
    hardware = yaml.safe_load(open(os.path.join(HERE, "hardware.example.yaml")))

    print(f"model: {args.model}")
    problems = groups_exist(system, hardware) + head_cap_agrees(system, hardware) + controllers_build(system, controllers)

    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
