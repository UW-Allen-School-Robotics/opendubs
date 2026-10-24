"""OpenDubsConfig: the ARCHITECT (ssa) robot backend for OpenDubs.

ssa loads it by name when ``SSA_ROBOT_PLUGINS`` names it:

    SSA_ROBOT_PLUGINS=opendubs=opendubs_architect.config:OpenDubsConfig \\
        python3 scripts/ssa_cli.py --robot opendubs --dry-run "Hand a candy to the person"

``--dry-run`` swaps the live ROS 2 client for ``dryrun.DryRunRobot``.

``OPENDUBS_ARCHITECT_BACKEND`` says what carries out the program's arm and gripper calls:

    live  (default)  the robot's live arm services (robot_node.py over live_arms.LiveArms), one
                     checked move per call, as the program runs
    show             recorded into a show for the runner to play (show_backend.py), the fallback

``OPENDUBS_ARCHITECT_PROFILE`` says which primitives the model is offered (api.PROFILES):
``full`` (default), or ``arms``: no perception, handoff, speech or navigation, the first profile
for the physical robot. With ``OPENDUBS_ARCHITECT_DATA`` set, the profile's skill docs are copied
into ``<data>/skills-<profile>/`` and ssa keeps its library there, so the repository can be
mounted read-only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ssa.robots.base import RobotConfig

from opendubs_architect import api
from opendubs_architect.dryrun import DryRunRobot, namespace_for

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
BACKENDS = ("live", "show")
#: The skill docs each profile loads; None is all of them.
PROFILE_SKILLS = {"full": None, "arms": ("motion.md", "program.md")}


def profile() -> str:
    name = os.environ.get("OPENDUBS_ARCHITECT_PROFILE") or "full"
    if name not in api.PROFILES:
        raise ValueError(f"OPENDUBS_ARCHITECT_PROFILE must be one of {', '.join(api.PROFILES)}, not {name!r}")
    return name


def primitives() -> tuple:
    return api.PROFILES[profile()]


def _profile_namespace(robot) -> dict:
    return {k: v for k, v in namespace_for(robot).items() if k in primitives()}


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
    #: With no perception (the arms profile), the scene description tool has nothing to call.
    @property
    def disabled_agent_tools(self) -> frozenset:
        off = {"run_ros2_command"}
        if "get_vqa_response" not in primitives():
            off.add("get_scene_description")
        return frozenset(off)

    #: The arm services plan, check the workspace and refuse on the robot; ssa's
    #: client-side CuRobo wrapper (a stub that always passes) is not installed.
    validates_motion_on_robot = True

    @property
    def api_spec(self) -> str:
        return api.spec_for(primitives())

    @property
    def skills_dir(self) -> Path:
        data, name = os.environ.get("OPENDUBS_ARCHITECT_DATA"), profile()
        if not data and PROFILE_SKILLS[name] is None:
            return _SKILLS_DIR
        if not data:
            raise ValueError(f"the {name} profile needs OPENDUBS_ARCHITECT_DATA, where its skill docs are copied")
        out = Path(data) / f"skills-{name}"
        out.mkdir(parents=True, exist_ok=True)
        wanted = PROFILE_SKILLS[name] or tuple(p.name for p in _SKILLS_DIR.glob("*.md"))
        for old in out.glob("*.md"):
            if old.name not in wanted:
                old.unlink()
        for doc in wanted:
            shutil.copyfile(_SKILLS_DIR / doc, out / doc)
        return out

    def make_dry_run_namespace(self, console) -> dict:
        return _profile_namespace(_with_backend(DryRunRobot(console)))

    def make_live_namespace(self) -> tuple[dict, object]:
        from opendubs_architect.client import OpenDubsRobotClient

        client = _with_backend(OpenDubsRobotClient())
        return _profile_namespace(client), client

    def shutdown(self, client) -> None:
        client.destroy_node()
