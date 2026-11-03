"""What the arm service does on request, behind ONE action: `ArmCommand` on `/<side>_arm/command` (ros_actions.serve_command). `run` is all the action calls.

  reach_pose     {"position": [x, y, z], "orientation": [w, x, y, z]?, "speed"?, "duration"?, "tolerance_mm"?, "tolerance_deg"?}    reach_pose.py
  ready          {}                                  the model's `ready` pose, where hand tracking (follow) starts
  candy_grasp    {"basket": {"position": [x, y, z], "orientation"?}?, "approach"?, "lift"?, "speed"?, "then": "ready" or "stay"}   candy_grasp.yaml
  open_gripper   {}                                  the finger to its full travel
  close_gripper  {}                                  close on what is between the fingers (success, data["holding"], data["position"] its width); shut on nothing is a failure

The gripper is also the `gripper_cmd` action (ros_actions.serve_gripper), position in metres; candy_grasp drives it inside the sequence. One command at a time: `run` takes the arm with `ArmService.begin` and is refused while anything else runs (a trajectory, `activate`, `follow`, another command), and `hold` ends it.
The poses are in the arm model's frame, the one `<side>/target` is in (+x forward, +y left, +z up, metres; quaternions w, x, y, z). ROS is not imported here, so `run` is tested on the simulator.
"""
from __future__ import annotations

import dataclasses
import json
import os

import numpy as np
import yaml

import gripper
import reach_pose
from reach_pose import FAILURE, INVALID_INPUT, SUCCESS, Outcome

HERE = os.path.dirname(os.path.abspath(__file__))
CANDY = os.path.join(HERE, "candy_grasp.yaml")


def _body(params) -> dict:
    try:
        body = json.loads(params) if isinstance(params, str) and params.strip() else (params or {})
    except json.JSONDecodeError as error:
        raise ValueError(f"the params are not JSON: {error}") from None
    if not isinstance(body, dict):
        raise ValueError("the params are a JSON object")
    return body


def _finger(service, metres: float, close: bool) -> Outcome:
    result = service.grip(metres)
    data = {"position": result.position, "effort": result.effort, "stalled": result.stalled, "holding": bool(close and result.stalled)}
    if result.cancelled:
        return Outcome(FAILURE, result.message, data)
    if result.reached or (close and result.stalled):
        return Outcome(SUCCESS, "closed on something" if data["holding"] else "reached", data)
    return Outcome(FAILURE, result.message, data)


def open_gripper(service, _, progress) -> Outcome:
    progress("open the gripper")
    return _finger(service, gripper.TRAVEL, close=False)


def close_gripper(service, _, progress) -> Outcome:
    """Close fully. Stopped by something between the fingers is the grasp: a success with `holding` true and the finger position (the object's width). Shut on nothing is a failure,
    as in candy_grasp: nothing was grasped. The gripper stays closed either way."""
    progress("close the gripper")
    outcome = _finger(service, 0.0, close=True)
    if outcome.ok and not outcome.data["holding"]:
        return Outcome(FAILURE, "closed on nothing: no object between the fingers", outcome.data)
    return outcome


def ready_target(service):
    """The arm's joints at the model's `ready` keyframe, or None."""
    system = service.robot.get_system()
    keyframe = system.get_keyframe("ready")
    if keyframe is None:
        return None
    indices = np.asarray(system.get_task_space(service.args.side).get_joint_indices())
    return np.asarray(keyframe.qpos, dtype=float)[: len(np.asarray(service.robot.get_state().get_position()))][indices]


def go_ready(service, _, progress) -> Outcome:
    target = ready_target(service)
    if target is None:
        return Outcome(FAILURE, "the model has no keyframe 'ready'")
    progress("to ready")
    return reach_pose.move_to_joints(service, target)


def candy_settings(side: str, body: dict) -> dict:
    """candy_grasp.yaml's entry for `side`, over which the params' own fields go. ValueError says what is missing or wrong."""
    with open(CANDY) as stream:
        settings = dict((yaml.safe_load(stream) or {}).get(side) or {})
    for key in ("basket", "approach", "lift", "speed"):
        if body.get(key) is not None:
            settings[key] = body[key]
    if not settings.get("basket"):
        raise ValueError(f"the candy basket is not taught for the {side} arm: put its hand pose in {os.path.basename(CANDY)}, or send {{\"basket\": {{\"position\": [x, y, z]}}}}")
    return settings


def parse_candy(service, params) -> dict:
    body = _body(params)
    settings = candy_settings(service.args.side, body)
    spot = settings["basket"] if isinstance(settings["basket"], dict) else {"position": settings["basket"]}
    basket = reach_pose.parse({"position": spot.get("position"), "orientation": spot.get("orientation"), **({"speed": settings["speed"]} if settings.get("speed") else {})},
                              service.limits)
    then = body.get("then", "ready")
    if then not in ("ready", "stay"):
        raise ValueError('"then" is "ready" or "stay"')
    return {"basket": basket, "approach": float(settings.get("approach", 0.08)), "lift": float(settings.get("lift", 0.10)), "then": then}


def candy_grasp(service, plan, progress) -> Outcome:
    """Open, come down on the basket from above, close, lift, and (unless `then` is `stay`) go to `ready`. A close that stops on nothing is a failure: no candy was picked.

    Any step that fails ends the whole thing, leaving the arm where it is and the gripper as it was; `hold` does the same between and inside the steps."""
    basket = plan["basket"]
    if basket.orientation is None:
        basket.orientation = reach_pose.hand(service.robot, service.args.side)[2][3:]        # held from the start, for every step

    def above(height):
        return dataclasses.replace(basket, position=basket.position + np.array([0.0, 0.0, height]))

    def step(name, run):
        if service.cancel.is_set():
            return Outcome(FAILURE, f"candy_grasp cancelled before: {name}")
        progress(name)
        outcome = run()
        return outcome if outcome.ok else Outcome(FAILURE, f"candy_grasp stopped at {name}: {outcome.message}", {"step": name, **outcome.data})

    for name, run in (("open the gripper", lambda: _finger(service, gripper.TRAVEL, close=False)),
                      ("above the basket", lambda: reach_pose.move_to(service, above(plan["approach"]))),
                      ("down to the basket", lambda: reach_pose.move_to(service, basket))):
        outcome = step(name, run)
        if not outcome.ok:
            return outcome
    closed = step("close the gripper", lambda: _finger(service, 0.0, close=True))
    if not closed.ok:
        return closed
    if not closed.data["holding"]:
        _finger(service, gripper.TRAVEL, close=False)
        return Outcome(FAILURE, "candy_grasp: the gripper closed on nothing, so no candy was picked (it is open again, at the basket)", {"step": "close the gripper", **closed.data})
    outcome = step("lift", lambda: reach_pose.move_to(service, above(plan["lift"])))
    if outcome.ok and plan["then"] == "ready":
        outcome = step("to ready", lambda: go_ready(service, None, progress))
    if not outcome.ok:
        return outcome
    return Outcome(SUCCESS, "candy picked" + (" and the arm is at ready" if plan["then"] == "ready" else ""), {"holding": True, "then": plan["then"]})


@dataclasses.dataclass
class Command:
    parse: object          # (service, params) -> what `run` is given; raises ValueError for params that are not valid
    run: object            # (service, parsed, progress) -> Outcome
    own_move: bool = True  # False: not a move of the arm (the gripper), so the gate does not ask for the head's clearance


def _no_params(service, params):
    _body(params)


COMMANDS = {
    "reach_pose": Command(lambda service, params: reach_pose.parse(params, service.limits), lambda service, request, progress: reach_pose.move_to(service, request, progress)),
    "ready": Command(_no_params, go_ready),
    "candy_grasp": Command(parse_candy, candy_grasp),
    "open_gripper": Command(_no_params, open_gripper, own_move=False),
    "close_gripper": Command(_no_params, close_gripper, own_move=False),
}


def check(service, name: str, params):
    """Why a goal cannot be accepted (an unknown name, params that are not valid, the arm busy), or None: the action's goal callback. The arm is taken, atomically, only in `run`."""
    command = COMMANDS.get(name)
    if command is None:
        return f"unknown command {name!r} (one of {', '.join(COMMANDS)})"
    try:
        command.parse(service, params)
    except ValueError as error:
        return str(error)
    return f"refused: {service.running} is running" if service.running else None


def run(service, name: str, params="", progress=lambda text: None) -> Outcome:
    """Take the arm for command `name`, run it, give it back. Never raises: the arm holds where it is and the outcome says why."""
    command = COMMANDS.get(name)
    if command is None:
        return Outcome(INVALID_INPUT, f"unknown command {name!r} (one of {', '.join(COMMANDS)})")
    try:
        parsed = command.parse(service, params)
    except ValueError as error:
        return Outcome(INVALID_INPUT, str(error))
    refusal = service.begin(name, own_move=command.own_move)
    if refusal:
        return Outcome(FAILURE, f"refused: {refusal}")
    try:
        outcome = command.run(service, parsed, progress)
    except Exception as error:  # noqa: BLE001 -- the arm holds; the service stays up
        outcome = Outcome(FAILURE, f"{type(error).__name__}: {error}")
    finally:
        service.end()
    outcome.cancelled = not outcome.ok and (service.cancel.is_set() or service.grip_cancel.is_set())
    return outcome
