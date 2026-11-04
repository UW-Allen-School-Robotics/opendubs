"""The fallback backend: a program's arm and gripper calls become a show for the runner to play.

With ``OPENDUBS_ARCHITECT_BACKEND=show``, the approved program is run once to record it. Its arm
and gripper calls are planned and checked exactly as the live service checks them
(live_arms.ShowRecorder) and written as cues. Everything else (perception, the face, speech) goes to
the robot as usual, or to the dry-run robot at a desk. The show is then compiled and stamped like
any other (``ik.py check``, ``sweep.py stamp``) and played by the runner on each "trick or treat",
with the runner's own gates.

What changes for a program: its targets are fixed when it is recorded, so the arm goes to the same
place every time the show plays. ``place_in_hand`` cannot follow a hand: it offers at the named pose
OFFER_POSE and opens the gripper there.
"""

from __future__ import annotations

import functools
import os

from opendubs_architect import api, live_arms
from opendubs_architect.client import MotionRefused

OFFER_POSE = "offer"
DEFAULT_SHOW = os.path.join(os.path.expanduser("~"), ".cache", "opendubs", "architect.yaml")


def _limits_path() -> str:
    import cue
    path = os.environ.get("OPENDUBS_ARCHITECT_LIMITS") or cue.DEFAULT_LIMITS
    if os.path.isfile(path):
        return path
    fixture = os.path.join(os.path.dirname(cue.__file__), "tests", "joint_limits_fixture.yaml")
    print(f"show backend: no joint limits at {path}; recording against the TEST FIXTURE. "
          "The runner checks the show against the robot's own limits before it plays.")
    return fixture


def make_recorder(path: str | None = None, limits_path: str | None = None, router=None) -> live_arms.ShowRecorder:
    """`router`, or OPENDUBS_ARCHITECT_PLANNER=cbirrt|curobo: a move the straight-line planner refuses
    as too close is routed around the body by the arm motion planner (routing.py)."""
    import cue
    limits = cue.load_limits(limits_path or _limits_path())
    config = live_arms.ArmConfig.load()
    path = path or os.environ.get("OPENDUBS_ARCHITECT_SHOW") or DEFAULT_SHOW
    if router is None and os.environ.get("OPENDUBS_ARCHITECT_PLANNER"):
        from opendubs_architect.routing import Router
        router = Router(os.environ["OPENDUBS_ARCHITECT_PLANNER"])
    return live_arms.ShowRecorder(live_arms.Planner(limits, config, router), config, limits, path)


class Publisher:
    """demo_runner's `publish` for the show path: an approved program, recorded as a show, stamped
    by the clearance sweep, validated, and only then put in `out_dir` as architect-<sha>.yaml.
    From there `show_sync.py push` takes it to the robot, and the Shows page runs it.

    Arm calls are recorded; anything else goes to the dry-run robot, so a program in the arms
    profile records the same show every time it is approved."""

    def __init__(self, out_dir: str, primitives, limits_path: str | None = None, base=None):
        """`base` makes the robot that answers everything but the arm calls while recording
        (a console -> robot); the dry-run robot by default, PerceptionDryRun to look for real."""
        self.out_dir, self.primitives, self.base = out_dir, tuple(primitives), base
        self.limits_path = limits_path or _limits_path()
        os.makedirs(out_dir, exist_ok=True)

    def __call__(self, program) -> dict:
        import cue
        import sweep
        from opendubs_architect.demo_runner import _Quiet
        from opendubs_architect.dryrun import DryRunRobot
        show_id = f"architect-{program.sha}"
        final = os.path.join(self.out_dir, show_id + ".yaml")
        tmp = os.path.join(self.out_dir, f".recording-{program.sha}.yaml")
        recorder = make_recorder(tmp, self.limits_path)
        recorder.show_id, recorder.name = show_id, f"ARCHITECT: {program.summary}"
        recorder.palette = {"label": f"ARCHITECT {program.sha[:6]}", "order": 90}
        robot = ShowRobot((self.base or DryRunRobot)(_Quiet()), recorder)
        ns = {name: getattr(robot, name) for name in self.primitives}
        ns["wait"] = robot.wait
        try:
            try:
                exec(compile(program.code, f"<recording {program.sha}>", "exec"), ns)  # noqa: S102  records only
            except MotionRefused as e:
                raise ValueError(f"a move was refused while recording: {e}") from None
            if not recorder.cues:
                raise ValueError("the program moves nothing, so there is no show")
            res = sweep.run(tmp, self.limits_path, True, force=True)
            if res["min_mm"] < sweep.THRESHOLD_MM:
                raise ValueError(f"the show comes within {res['min_mm']:.0f} mm of the robot's body "
                                 f"(at least {sweep.THRESHOLD_MM:g})")
            tl = cue.load_show(tmp).export(cue.load_limits(self.limits_path), self.limits_path)
            if not tl["valid"]:
                raise ValueError("the show validator refused it: " + "; ".join(p["msg"] for p in tl["problems"][:3]))
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        fixture = os.path.basename(os.path.dirname(self.limits_path)) == "tests"
        return {"path": final, "show_id": show_id, "seconds": recorder.t, "cues": len(recorder.cues),
                "clearance_mm": round(float(res["min_mm"]), 1),
                # "may peak" and the like: shown to the person, who treats them as real
                "warnings": [w["msg"] if isinstance(w, dict) else str(w) for w in tl.get("warnings") or []],
                "limits": "TEST FIXTURE: re-check against the robot's" if fixture else self.limits_path}


def _refused(fn):
    """A live_arms refusal as the MotionRefused a program is told never to catch."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except live_arms.Refused as e:
            raise MotionRefused(str(e)) from None
    return wrapped


class ShowRobot:
    """The live client's (or the dry-run robot's) calls, with the arm and gripper ones recorded."""

    def __init__(self, base, recorder: live_arms.ShowRecorder) -> None:
        self._base, self.recorder = base, recorder

    def __getattr__(self, name):
        return getattr(self._base, name)

    def destroy_node(self) -> None:
        if hasattr(self._base, "destroy_node"):
            self._base.destroy_node()

    # Proprioception: where the recorded show has the arms
    def get_current_joints(self) -> dict:
        import math
        pose = self.recorder._pose()
        return {side: {f"joint{j}": {"position": math.radians(v), "velocity": 0.0, "effort": None}
                       for j, v in enumerate(pose[live_arms.ARM_SLOT[side]], 1)} for side in live_arms.SIDES}

    def get_current_ee_pose(self) -> dict:
        return self.recorder.tool_poses()

    def get_current_gripper_width(self) -> dict:
        return self.recorder.gripper_openings()

    def get_workspace(self) -> dict:
        return self.recorder.config.workspace

    def get_named_poses(self) -> dict:
        return {"poses": self.recorder.named_poses()}

    # Arm and gripper control
    @_refused
    def move_ee_to_pose(self, arm: str, target_pose: dict) -> bool:
        pose = api.check_pose(target_pose)
        p = pose["position"]
        orient = api.approach_of(pose)
        if orient is None:
            o = pose.get("orientation")
            if o and not (o.get("w") == 1.0 and not any(o.get(k) for k in "xyz")):
                orient = o
        self.recorder.move_to_point(api.check_arm(arm), [p["x"], p["y"], p["z"]], approach=orient)
        return True

    def wait(self, seconds: float) -> bool:
        self.recorder.wait(api.check_wait(seconds))
        return True

    @_refused
    def move_ee_to_rel_pose(self, arm: str, delta: dict) -> bool:
        d = api.check_delta(delta)
        self.recorder.move_by(api.check_arm(arm), [d["x"], d["y"], d["z"]])
        return True

    @_refused
    def move_arm_to_named_pose(self, arm: str, name: str) -> bool:
        self.recorder.move_to_named(api.check_arm(arm), name)
        return True

    @_refused
    def set_gripper_width(self, arm: str, opening: float) -> bool:
        self.recorder.set_gripper(api.check_arm(arm), api.check_opening(opening))
        return True

    @_refused
    def reset_robot(self) -> bool:
        self.recorder.stow()
        return True

    @_refused
    def place_in_hand(self, arm: str, timeout_s: float = 15.0) -> dict:
        arm = api.check_arm(arm)
        self.recorder.move_to_named(arm, OFFER_POSE)
        self.recorder.set_gripper(arm, 1.0)
        return {"released": True, "reason": f"recorded: offered at the fixed {OFFER_POSE!r} pose"}



class PerceptionDryRun:
    """The dry-run robot, except that it looks: detect_objects and locate_object ask the perception
    services (perception.py), so a show recorded with the arms_see profile goes where the camera
    saw things when it was approved. Those calls only read; nothing here moves the robot."""

    def __init__(self, console, client=None):
        from opendubs_architect.dryrun import DryRunRobot
        self._dry, self._client = DryRunRobot(console), client

    def __getattr__(self, name):
        return getattr(self._dry, name)

    def _live(self):
        if self._client is None:
            from opendubs_architect.client import OpenDubsRobotClient
            self._client = OpenDubsRobotClient("architect_perception")
        return self._client

    def detect_objects(self, text_prompt, camera="head", x_offset=0.0, y_offset=0.0, z_offset=0.0):
        return self._live().detect_objects(text_prompt, camera, x_offset, y_offset, z_offset)

    def locate_object(self, label, camera="head"):
        return self._live().locate_object(label, camera)
