"""Trick or treat as a plain sequence of service calls to the treat services (treat_services.py, which must be running).
Nothing else: no topics, no polling; each call's data goes into the next call's request.

  treat_follow.sh sequence [options]
  treat_follow.sh call NAME [JSON]        one service, sent once and waited for (`--call`)

  ready -> find_hand -> plan_treat -> move_joints (move_pose with --no-jaws-level) -> hold -> ready

The services decide real arm or simulation, and keep the safety: on the real arm a released deadman latches a stop and
every later move is refused, so this sequence ends at the first refusal. Hold the deadman BEFORE starting it (the real
arm): a move is refused unless it is held when the move starts. Exit codes as treat_reach.py: 0 done, 1 a step
failed, 2 no hand held out, 3 nobody found, 4 stopped or the deadman not held, 130 Ctrl-C (`stop` is called).
"""

import argparse
import json
import os
import sys

SUCCESS, NOT_FOUND = 0, 5


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find-s", type=float, default=20.0, help="s to find and centre a person")
    ap.add_argument("--wait-s", type=float, default=10.0, help="s for a hand held out and still")
    ap.add_argument("--treat-s", type=float, default=5.0, help="s at the treat before going back to ready")
    ap.add_argument("--no-jaws-level", dest="jaws", action="store_false", help="move_pose to the IK's pose instead")
    ap.add_argument("--speed", type=float, default=None, help="rad/s (the services cap it on the real arm)")
    ap.add_argument("--no-return", dest="return_home", action="store_false", help="stay at the treat")
    ap.add_argument("--call-s", type=float, default=150.0, help="s to wait for one service call")
    ap.add_argument("--call", nargs="+", metavar=("NAME", "JSON"),
                    help="call ONE service and print its answer (sent once, never resent: perception_call.sh resends an "
                         "unanswered request every 10 s, which a move that takes longer must not get)")
    args = ap.parse_args()

    os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
    import rclpy
    from robot_api_interfaces.srv import RobotCommand, RobotQuery
    rclpy.init()
    node = rclpy.create_node("treat_sequence")

    def call(name, req=None):
        """(result code, message, data) of /perception/treat/<name>: sent once, waited for."""
        kind = RobotQuery if name == "status" else RobotCommand
        client = node.create_client(kind, f"/perception/treat/{name}")
        if not client.wait_for_service(timeout_sec=10.0):
            raise SystemExit(f"no /perception/treat/{name}: are the treat services up? (treat_follow.sh services)")
        request = RobotQuery.Request() if kind is RobotQuery else RobotCommand.Request(req=json.dumps(req or {}))
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=args.call_s)
        if not future.done():
            raise SystemExit(f"/perception/treat/{name}: no answer in {args.call_s:.0f} s")
        r = future.result()
        print(f"{name}: {r.result_code.message}", flush=True)
        return r.result_code.result_code, r.result_code.message, (json.loads(r.data) if r.data else {})

    try:
        if args.call:
            try:
                req = json.loads(args.call[1]) if len(args.call) > 1 else {}
            except ValueError as error:
                raise SystemExit(f"bad JSON: {error}")
            code, _, data = call(args.call[0], req)
            print(json.dumps(data, indent=1))
            return code

        speed = {"speed": args.speed} if args.speed else {}

        code, _, data = call("ready")
        if code != SUCCESS:
            return 4 if data.get("stopped") else 1

        code, message, hand = call("find_hand", {"find_s": args.find_s, "wait_s": args.wait_s})
        if code != SUCCESS:
            return 3 if message.startswith("nobody") else 2

        code, _, plan = call("plan_treat", {"palm": hand["palm"], "jaws_level": args.jaws})
        if code != SUCCESS:
            return 1

        if args.jaws:
            code, _, data = call("move_joints", {"joints": plan["joints"], **speed})
        else:
            code, _, data = call("move_pose", {**plan["pose"], **speed})
        if code != SUCCESS:
            if data.get("stopped"):
                return 4
            if args.return_home:
                call("ready")
            return 1
        if not args.return_home:
            return 0

        code, _, data = call("hold", {"seconds": args.treat_s})
        if code != SUCCESS:
            return 4 if data.get("stopped") else 1

        code, _, data = call("ready")
        if code != SUCCESS:
            return 4 if data.get("stopped") else 1
        return 0
    except KeyboardInterrupt:
        try:
            call("stop")
        except SystemExit:
            pass
        return 130
    finally:
        rclpy.try_shutdown()


if __name__ == "__main__":
    sys.exit(main())
