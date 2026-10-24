"""The robot side of the ARCHITECT arm API: a ROS 2 node serving the arm primitives under /robot/
(api.PRIMITIVES) over live_arms.LiveArms.

    python3 -m opendubs_architect.robot_node                                  # moves nothing
    python3 -m opendubs_architect.robot_node --arms ros --robot dashboard     # the real arms

The defaults move nothing, as the runner's do: `--arms fake` follows every goal at once in memory,
and `--robot fake` is a robot with the deadman held in AUTO. Only `--arms ros` sends goals to the
arm controllers, and then only through LiveArms' checks and gates; `--robot dashboard` reads the
real e-stop, CH5 and deadman from the dashboard (runner.DashboardRobotState). `--arms ros` with
`--robot fake` is refused: the real arms never move on a fake deadman.

Requests are answered one at a time (a single-threaded executor), so two moves never overlap.
Needs ROS 2 Jazzy sourced, and robot_api_interfaces (software/architect/ros) built in a sourced
workspace. NOT YET FOR THE ROBOT: live_arms.py says what is missing.
"""

from __future__ import annotations

import argparse
import json
import sys

from opendubs_architect import api, live_arms

#: The primitives this node serves; the rest of /robot/ is served elsewhere.
HANDLED = ("move_ee_to_pose", "move_ee_to_rel_pose", "move_arm_to_named_pose", "set_gripper_width",
           "reset_robot", "get_current_joints", "get_current_ee_pose", "get_current_gripper_width",
           "get_workspace", "get_named_poses")


def _xyz(d: dict) -> list:
    return [float(d[k]) for k in "xyz"]


def _moved(move) -> dict:
    return {"seconds": move.seconds, "clearance_mm": move.clearance_mm}


def handlers(arms: live_arms.LiveArms) -> dict:
    """{primitive: fn(payload dict) -> reply dict}."""
    return {
        "move_ee_to_pose": lambda p: _moved(arms.move_to_point(
            api.check_arm(p["arm"]), _xyz(api.check_pose(p["target_pose"])["position"]))),
        "move_ee_to_rel_pose": lambda p: _moved(arms.move_by(
            api.check_arm(p["arm"]), _xyz(api.check_delta(p["delta"])))),
        "move_arm_to_named_pose": lambda p: _moved(arms.move_to_named(api.check_arm(p["arm"]), str(p["name"]))),
        "set_gripper_width": lambda p: arms.set_gripper(api.check_arm(p["arm"]), api.check_opening(p["opening"])) or {},
        "reset_robot": lambda p: arms.stow() or {},
        "get_current_joints": lambda p: arms.joints(),
        "get_current_ee_pose": lambda p: arms.tool_poses(),
        "get_current_gripper_width": lambda p: arms.gripper_openings(),
        "get_workspace": lambda p: arms.config.workspace,
        "get_named_poses": lambda p: {"poses": arms.named_poses()},
    }


def answer(fn, payload: dict) -> tuple:
    """(result code, message, reply dict) for one request: a refusal is REFUSED, a bad argument
    INVALID_INPUT, and anything else that goes wrong FAILURE, with the reason."""
    try:
        return api.SUCCESS, "", fn(payload)
    except live_arms.Refused as e:
        return api.REFUSED, str(e), {}
    except (ValueError, KeyError, TypeError) as e:
        return api.INVALID_INPUT, f"{type(e).__name__}: {e}", {}
    except Exception as e:  # noqa: BLE001  a failure is an answer, never a dead service
        return api.FAILURE, f"{type(e).__name__}: {e}", {}


def build(args) -> live_arms.LiveArms:
    import cue
    import runner
    if args.arms == "ros" and args.robot == "fake":
        sys.exit("--arms ros needs --robot dashboard: the real arms never move on a fake deadman")
    limits = cue.load_limits(args.limits)
    config = live_arms.ArmConfig.load(args.config)
    arms = (live_arms.FollowingFakeArms() if args.arms == "fake"
            else live_arms.arm_output.RosArms(node_name="opendubs_architect_arms"))
    state = runner.FakeRobotState() if args.robot == "fake" else runner.DashboardRobotState()
    return live_arms.LiveArms(arms, state, live_arms.Planner(limits, config), config, limits)


def main(argv=None) -> int:
    import cue
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arms", choices=("fake", "ros"), default="fake")
    ap.add_argument("--robot", choices=("fake", "dashboard"), default="fake")
    ap.add_argument("--limits", default=cue.DEFAULT_LIMITS)
    ap.add_argument("--config", default=None, help=f"the arm config (default {live_arms.DEFAULT_CONFIG})")
    args = ap.parse_args(argv)
    arms = build(args)

    import rclpy
    from robot_api_interfaces.srv import RobotCommand, RobotQuery
    if not rclpy.ok():
        rclpy.init()
    node = rclpy.create_node("opendubs_architect_robot")
    table = handlers(arms)
    kinds = {name: (service, kind) for name, service, kind in api.PRIMITIVES}

    def serve(name, kind):
        def cb(req, resp):
            payload = json.loads(req.req or "{}") if kind == "command" else {}
            code, message, data = answer(table[name], payload)
            resp.result_code.result_code, resp.result_code.message = code, message
            resp.data = json.dumps(data)
            node.get_logger().info(f"{name} {payload} -> {code} {message}")
            return resp
        return cb

    for name in HANDLED:
        service, kind = kinds[name]
        node.create_service(RobotCommand if kind == "command" else RobotQuery, "/robot/" + service, serve(name, kind))
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        arms.arms.cancel_all()
        node.destroy_node()
    return 0


if __name__ == "__main__":
    sys.exit(main())
