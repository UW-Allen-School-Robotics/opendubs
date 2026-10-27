"""Joint limits for the bring-up tools: the model's own, the `range` of each joint in the MJCF.

What this robot's surroundings allow that the arm alone does not (cables behind the arm, a rest, a mount) is written into the model, in the joint's range, where
every consumer of the model reads it: these tools, the controllers' joint-limit stages and the simulator. There is no second file. The Choreography lane's validator
reads its own file (`OPENDUBS_ARM_LIMITS`); the tools here validate a show against the model instead, with `of_model`.
"""
from __future__ import annotations

NOTE = "Joint limits: the model's own ranges. Cables, a rest or a mount beside this arm are in them only if the model's joint ranges say so."


def of_model(system) -> dict:
    """joint name -> (lower, upper) in radians for every actuated joint of `system`: the format the Choreography validator takes (`cue.load_limits`)."""
    lower = system.get_joint_limits_min().coefficients()
    upper = system.get_joint_limits_max().coefficients()
    return {joint.get_name(): (float(lower[joint.get_index()]), float(upper[joint.get_index()])) for joint in system.get_joints() if joint.is_actuated()}
