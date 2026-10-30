"""Trick or treat as a plain sequence of service calls to the treat services (treat_services.py, which must be running).
Nothing else: no topics, no polling; each call's data goes into the next call's request.

  treat_follow.sh sequence [options]
  treat_follow.sh call NAME [JSON]        one service, sent once and waited for (`--call`)

  ready -> [the candy] -> find_hand -> plan_treat -> move_joints (move_pose with --no-jaws-level) -> hold -> ready
  the gripper (on by default; --no-gripper leaves it alone): it starts OPEN (the robot runs with it open: nothing here
  opens it first), closes on the candy, and gripper_open after move_joints drops it. A gripper call succeeds whatever
  the gripper was doing and whatever is between the fingers; only an error stops here
  the candy (on by default with the gripper; --no-candy skips it), after the first ready: move_pose from ready straight
  down to the candy, fingers down -> gripper_close -> ready. A failed move down or close ends the run with the arm held
  where it is

The services decide real arm or simulation, and keep the safety: on the real arm a released deadman latches a stop and
every later move is refused, so this sequence ends at the first refusal. Hold the deadman BEFORE starting it (the real
arm): a move is refused unless it is held when the move starts. Exit codes as treat_reach.py: 0 done, 1 a step
failed, 2 no hand held out, 3 nobody found, 4 stopped or the deadman not held, 130 Ctrl-C (`stop` is called).
"""

import argparse
import json
import os
import sys
import time

SUCCESS, NOT_FOUND = 0, 5

# The candy bucket, from Daphne's candy_gafro.py (daphne/halloween, measured on opendubs2 2026-10-28/29): the bucket
# stage's right edge, the rim at z 0.553 and the candy piled 2 cm below it, in her chassis frame. Here in arm_mount,
# move_pose's frame: her chassis frame less (0.423, 0, 0.3575) m, not turned.
CANDY_XY = (0.282, -0.07)        # over the bucket, near its centre (hers 0.705, -0.07): the open fingers clear the wall
CANDY_Z = 0.2567                 # 8 cm above the pile, 6 cm above the rim (hers 0.545, i.e. 0.1875 here; at 0.535 the
                                 # open fingers pushed into it). 2026-10-30: straight from ready, 0.2067 drove the
                                 # fingers into the candy and the move ended 18.9 mm off; 0.2267, then 3 cm higher at
                                 # the user's word. --candy-z
# The fingers (the hand's +z) 20 deg off vertical, forward and left, as hers: (0.242, 0.242, -0.940). The smallest turn
# from the ready hand that points them so (76 deg), w, x, y, z. The arm service's simulator reached every pose of the
# pick with it (2026-10-30: from ready straight to the candy 3.3 mm and 0.9 deg off; on the way the hand came within 5
# cm of the rim's height only within 4.4 cm of the bucket's axis, and was 6.4 cm above it or more anywhere farther out;
# from there straight to ready it rose first: within 5 cm of the rim's height only within 5.3 cm of the axis, and 10.7
# cm above it by 10 cm out).
CANDY_ORIENTATION = [0.022, 0.6025, 0.779, 0.1723]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find-s", type=float, default=20.0, help="s to find and centre a person")
    ap.add_argument("--wait-s", type=float, default=10.0, help="s for a hand held out and still")
    ap.add_argument("--treat-s", type=float, default=5.0, help="s at the treat before going back to ready")
    ap.add_argument("--no-jaws-level", dest="jaws", action="store_false", help="move_pose to the IK's pose instead")
    ap.add_argument("--speed", type=float, default=None, help="rad/s (the services cap it on the real arm)")
    ap.add_argument("--no-return", dest="return_home", action="store_false", help="stay at the treat")
    ap.add_argument("--gripper", action="store_true", default=True,
                    help="(the default) close the gripper on the candy and open it at the treat, through the arm "
                         "service's close_gripper / open_gripper; it starts open")
    ap.add_argument("--no-gripper", dest="gripper", action="store_false", help="leave the gripper alone")
    ap.add_argument("--candy", action="store_true", default=True,
                    help="(the default, with the gripper) take a candy from the bucket after the first ready")
    ap.add_argument("--no-candy", dest="candy", action="store_false", help="no candy pick")
    ap.add_argument("--candy-z", type=float, default=CANDY_Z,
                    help=f"m, arm_mount: how low the hand goes into the bucket (default {CANDY_Z}; the rim is at 0.196, "
                         "the pile about 2 cm below it)")
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

    def call(name, req=None):
        """(result code, message, data) of /perception/treat/<name>: sent once, waited for. Each client is made once and
        given half a second after the service is found: a request sent the moment it is can lose its reply (the
        first gripper_close in the simulation waited 150 s for one that never came; perception_call.py settles too)."""
        kind = RobotQuery if name == "status" else RobotCommand
        client = clients.get(name)
        if client is None:
            client = clients[name] = node.create_client(kind, f"/perception/treat/{name}")
            if not client.wait_for_service(timeout_sec=10.0):
                raise SystemExit(f"no /perception/treat/{name}: are the treat services up? (treat_follow.sh services)")
            settle = time.monotonic() + 0.5
            while time.monotonic() < settle:
                rclpy.spin_once(node, timeout_sec=0.05)
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

        if args.gripper and args.candy:
            code = take_candy(call, args, speed)
            if code:
                return code

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
        if args.gripper:
            code, _, data = call("gripper_open")
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


def take_candy(call, args, speed):
    """From ready straight down to the candy (the gripper already open), close, straight back to ready: 0, or the exit
    code of the step that failed. A failed move down or close leaves the arm where it is."""
    def pose(z):
        code, _, data = call("move_pose", {"position": [CANDY_XY[0], CANDY_XY[1], z], "orientation": CANDY_ORIENTATION,
                                           **speed})
        return 0 if code == SUCCESS else (4 if data.get("stopped") else 1)

    code = pose(args.candy_z)
    if code:
        return code
    code, _, data = call("gripper_close")
    if code != SUCCESS:
        return 4 if data.get("stopped") else 1
    code, _, data = call("ready")
    return 0 if code == SUCCESS else (4 if data.get("stopped") else 1)


if __name__ == "__main__":
    sys.exit(main())
