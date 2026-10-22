"""OpenDubsConfig: the ARCHITECT (ssa) robot backend for OpenDubs.

ssa loads it by name when ``SSA_ROBOT_PLUGINS`` names it:

    SSA_ROBOT_PLUGINS=opendubs=opendubs_architect.config:OpenDubsConfig \\
        python3 scripts/ssa_cli.py --robot opendubs --dry-run "Hand a candy to the person"

``--dry-run`` swaps the live ROS 2 client for ``dryrun.DryRunRobot``.
"""

from __future__ import annotations

from pathlib import Path

from ssa.robots.base import RobotConfig

from opendubs_architect import api
from opendubs_architect.dryrun import DryRunRobot, namespace_for

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


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
        return namespace_for(DryRunRobot(console))

    def make_live_namespace(self) -> tuple[dict, object]:
        from opendubs_architect.client import OpenDubsRobotClient

        client = OpenDubsRobotClient()
        return namespace_for(client), client

    def shutdown(self, client) -> None:
        client.destroy_node()
