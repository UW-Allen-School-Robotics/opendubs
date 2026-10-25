"""Call one /perception service and print its result code, message and JSON data.

  python3 perception_call.py grasp_object '{"label": "bottle"}'
  python3 perception_call.py detect '{"labels": ["bottle", "cup"]}'
  python3 perception_call.py status

Needs robot_api_interfaces sourced (tools/perception_call.sh does that, and the transport). Exits
0 on SUCCESS, 1 otherwise.
"""

import argparse
import json
import sys
import time

import rclpy
from rclpy.node import Node

from robot_api_interfaces.msg import ResultCode
from robot_api_interfaces.srv import RobotCommand, RobotQuery

QUERIES = {"status"}
CODES = {getattr(ResultCode, n): n for n in dir(ResultCode) if n.isupper() and isinstance(getattr(ResultCode, n), int)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("service", help="detect | segment | locate | grasp_object | status")
    ap.add_argument("req", nargs="?", default="", help="JSON request (none for status)")
    ap.add_argument("--namespace", default="/perception")
    ap.add_argument("--timeout", type=float, default=30.0)
    args, ros_args = ap.parse_known_args()
    if args.req:
        try:
            json.loads(args.req)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"the request is not JSON: {exc}")

    rclpy.init(args=ros_args)
    node = Node("perception_call")
    name = f"{args.namespace.rstrip('/')}/{args.service}"
    is_query = args.service in QUERIES
    client = node.create_client(RobotQuery if is_query else RobotCommand, name)
    if not client.wait_for_service(timeout_sec=10.0):
        raise SystemExit(f"{name} is not available (is the perception service running? on this host, "
                         "FASTDDS_BUILTIN_TRANSPORTS=UDPv4)")
    request = RobotQuery.Request() if is_query else RobotCommand.Request(req=args.req)
    # A client that calls the moment it sees the service sometimes loses the reply: the server's
    # reply path to it is not matched yet (about 1 call in 20 on opendubs2). Let discovery settle,
    # and resend an unanswered request; every perception call only reads, so a resend is safe.
    # A long-lived client (the robot's own code) matches once and does not need this.
    settle = time.monotonic() + 0.5
    while time.monotonic() < settle:
        rclpy.spin_once(node, timeout_sec=0.05)
    t0 = time.perf_counter()
    res, per_try = None, min(args.timeout, 10.0)
    for attempt in range(1, 4):
        fut = client.call_async(request)
        rclpy.spin_until_future_complete(node, fut, timeout_sec=per_try)
        if fut.result() is not None:
            res = fut.result()
            break
        client.remove_pending_request(fut)
        print(f"no reply from {name} within {per_try:.0f} s (try {attempt} of 3); sending again", file=sys.stderr)
    if res is None:
        raise SystemExit(f"no reply from {name} after 3 tries")
    code = res.result_code.result_code
    print(f"{CODES.get(code, code)}: {res.result_code.message}  ({(time.perf_counter() - t0) * 1000:.0f} ms round trip)")
    try:
        print(json.dumps(json.loads(res.data), indent=1))
    except json.JSONDecodeError:
        print(res.data)
    node.destroy_node()
    rclpy.try_shutdown()
    return 0 if code == ResultCode.SUCCESS else 1


if __name__ == "__main__":
    sys.exit(main())
