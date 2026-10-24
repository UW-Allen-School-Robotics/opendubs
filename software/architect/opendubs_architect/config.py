"""OpenDubsConfig: the ARCHITECT (ssa) robot backend for OpenDubs.

ssa loads it by name when ``SSA_ROBOT_PLUGINS`` names it:

    SSA_ROBOT_PLUGINS=opendubs=opendubs_architect.config:OpenDubsConfig \\
        python3 scripts/ssa_cli.py --robot opendubs --dry-run "Hand a candy to the person"

``--dry-run`` swaps the live ROS 2 client for ``dryrun.DryRunRobot``.

``OPENDUBS_ARCHITECT_BACKEND`` says what carries out the program's arm and gripper calls:

    live  (default)  the robot's live arm services (robot_node.py over live_arms.LiveArms), one
                     checked move per call, as the program runs
    show             recorded into a show for the runner to play (show_backend.py), the fallback
"""

from __future__ import annotations

import os
from pathlib import Path

from ssa.robots.base import RobotConfig

from opendubs_architect import api
from opendubs_architect.dryrun import DryRunRobot, namespace_for

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
BACKENDS = ("live", "show")


def backend() -> str:
    name = os.environ.get("OPENDUBS_ARCHITECT_BACKEND") or "live"
    if name not in BACKENDS:
        raise ValueError(f"OPENDUBS_ARCHITECT_BACKEND must be one of {', '.join(BACKENDS)}, not {name!r}")
    return name


def _with_backend(robot):
    """The robot as the program sees it: as it is (live), or with its arm calls recorded (show)."""
    if backend() == "live":
        return robot
    from opendubs_architect.show_backend import ShowRobot, make_recorder
    return ShowRobot(robot, make_recorder())


class OpenDubsConfig(RobotConfig):
    name = "opendubs"
    display_name = "OpenDubs (two OpenArm arms, Orbbec head camera, ROS 2 Jazzy on a Jetson AGX Thor)"

    #: Agent tools this robot does not offer. run_ros2_command runs a shell command
    #: after a y/n; on a robot with children in front of it there is no shell for a model.
    disabled_agent_tools = frozenset({"run_ros2_command"})

    #: The arm services plan, check the workspace and refuse on the robot; ssa's
    #: client-side CuRobo wrapper (a stub that always passes) is not installed.
    validates_motion_on_robot = True

    @property
    def api_spec(self) -> str:
        return api.API_SPEC

    @property
    def skills_dir(self) -> Path:
        return _SKILLS_DIR

    def make_dry_run_namespace(self, console) -> dict:
        return namespace_for(_with_backend(DryRunRobot(console)))

    def make_live_namespace(self) -> tuple[dict, object]:
        from opendubs_architect.client import OpenDubsRobotClient

        client = _with_backend(OpenDubsRobotClient())
        return namespace_for(client), client

    def shutdown(self, client) -> None:
        client.destroy_node()
