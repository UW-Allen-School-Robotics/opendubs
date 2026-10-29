"""Trick or treat in sequence over the treat services (treat_services.py, which must be running): the steps of
treat_reach.py, each a service call.

  treat_follow.sh sequence [options]

  (real) wait for the deadman -> ready -> find_hand (centre the person, then a hand held out and still) -> plan_treat
  (the goal toward that palm, jaws level) -> move_joints (one continuous move there; move_pose with --no-jaws-level)
  -> --treat-s at the treat -> ready.

Whether the moves go to the real arm or the simulation is the services' choice (treat_services.py --real); this reads it
from /perception/treat/status. On the real arm nothing is called until the deadman is held, and if the permit is lost at
any point (the services latch a stop) the sequence calls `stop` and ends: it never asks for another move after that.
Exit codes as treat_reach.py: 0 done, 1 a step failed, 2 no hand held out, 3 nobody found, 4 stopped (the deadman,
CH5 or the e-stop), 130 Ctrl-C (`stop` called: the arm holds).
"""

import argparse
import json
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find-s", type=float, default=20.0)
    ap.add_argument("--wait-s", type=float, default=10.0)
    ap.add_argument("--treat-s", type=float, default=5.0)
    ap.add_argument("--no-jaws-level", dest="jaws", action="store_false", help="move_pose to the IK's pose instead")
    ap.add_argument("--speed", type=float, default=None, help="rad/s (the services cap it on the real arm)")
    ap.add_argument("--no-return", dest="return_home", action="store_false", help="stay at the treat")
    ap.add_argument("--deadman-wait-s", type=float, default=120.0)
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
    clients = {}

    def call(name, req=None, quiet=False):
        """(result code, message, data) of /perception/treat/<name>."""
        kind = RobotQuery if name == "status" else RobotCommand
        client = clients.get(name) or node.create_client(kind, f"/perception/treat/{name}")
        clients[name] = client
        if not client.wait_for_service(timeout_sec=10.0):
            raise SystemExit(f"no /perception/treat/{name}: are the treat services up? (treat_follow.sh services)")
        request = RobotQuery.Request() if kind is RobotQuery else RobotCommand.Request(req=json.dumps(req or {}))
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=args.call_s)
        if not future.done():
            raise SystemExit(f"/perception/treat/{name}: no answer in {args.call_s:.0f} s")
        r = future.result()
        data = json.loads(r.data) if r.data else {}
        if not quiet:
            print(f"{name}: {r.result_code.message}", flush=True)
        return r.result_code.result_code, r.result_code.message, data

    if args.call:
        try:
            req = json.loads(args.call[1]) if len(args.call) > 1 else {}
        except ValueError as error:
            raise SystemExit(f"bad JSON: {error}")
        code, _, data = call(args.call[0], req)
        print(json.dumps(data, indent=1))
        rclpy.try_shutdown()
        return code

    def permit_ok():
        _, _, st = call("status", quiet=True)
        return (st.get("permit") or {}).get("ok", False) and not st.get("stopped"), st

    try:
        _, _, st = call("status", quiet=True)
        real = st.get("mode") == "real"
        print(f"treat services: {st.get('mode')}" + (f", head {st['head']['mode']}" if st.get("head") else ", no head"),
              flush=True)
        if real:
            print("REAL ARM. HOLD THE DEADMAN NOW (CH5 MANUAL or AUTO, the e-stop clear). Nothing is called until it is "
                  "held; letting go stops the arm and ends this.", flush=True)
            deadline, last = time.monotonic() + args.deadman_wait_s, None
            while True:
                ok, st = permit_ok()
                if ok:
                    break
                why = st.get("stopped") or (st.get("permit") or {}).get("why")
                if why != last:
                    print(f"  waiting: {why}", flush=True)
                    last = why
                if time.monotonic() > deadline:
                    print("the deadman was not held: nothing was called", flush=True)
                    return 4
                time.sleep(0.2)
            print("deadman held: starting", flush=True)

        def stopped(message):
            return message.startswith("STOPPED") or message.startswith("stopped") or "motion not permitted" in message

        code, message, _ = call("ready")
        if code != 0:
            return 4 if stopped(message) else 1
        code, message, found = call("find_hand", {"find_s": args.find_s, "wait_s": args.wait_s})
        if code != 0:
            return 3 if "nobody" in message else 2
        code, message, plan = call("plan_treat", {"palm": found["palm"], "jaws_level": args.jaws})
        if code != 0:
            return 1
        speed = {"speed": args.speed} if args.speed else {}
        if args.jaws:
            code, message, _ = call("move_joints", {"joints": plan["joints"], **speed})
        else:
            code, message, _ = call("move_pose", {**plan["pose"], **speed})
        if code != 0:
            if stopped(message):
                return 4
            if args.return_home:
                call("ready")
            return 1
        if not args.return_home:
            return 0
        print(f"holding {args.treat_s:.0f} s for the treat to be taken, then back to ready", flush=True)
        end = time.monotonic() + args.treat_s
        while time.monotonic() < end:
            if real:
                ok, st = permit_ok()
                if not ok:
                    call("stop")
                    print(f"STOPPED: {st.get('stopped') or (st.get('permit') or {}).get('why')}. Nothing more is called.",
                          flush=True)
                    return 4
            time.sleep(0.2)
        code, message, _ = call("ready")
        return 0 if code == 0 else (4 if stopped(message) else 1)
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
