#!/usr/bin/env python3
"""Dora -> ROS 2 bridge node for `/robot/state`, `/robot/estopped`, and `/robot/motion_enabled`.

Keeps existing ROS 2 consumers (Foxglove, rosbridge, Nav2, HallPass) working unchanged
when `robot_state` is produced inside a Dora dataflow.
"""
from __future__ import annotations

import json
import time

STALE_S = 3.0


def decode_robot_state(raw: bytes | str) -> dict:
    """Decode UTF-8 JSON bytes or string emitted by `robot_state_node.py`."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw = bytes(raw).decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object, got {type(data).__name__}")
    return data


def is_stale(last_seen: float | None, now: float, stale_s: float = STALE_S) -> bool:
    """True if no fresh `robot_state` payload arrived within `stale_s` seconds."""
    if last_seen is None:
        return True
    return (now - last_seen) > stale_s


def main():
    import pyarrow as pa
    import rclpy
    from dora import Node
    from std_msgs.msg import Bool, String
    from std_srvs.srv import Trigger

    rclpy.init()
    ros_node = rclpy.create_node("dora_robot_state_bridge")
    p_state = ros_node.create_publisher(String, "/robot/state", 10)
    p_estop = ros_node.create_publisher(Bool, "/robot/estopped", 10)
    p_motion = ros_node.create_publisher(Bool, "/robot/motion_enabled", 10)
    p_rc = ros_node.create_publisher(String, "/robot/rc", 10)

    dora_node = Node()
    last_seen = None
    last_watchdog_pub = 0.0

    def publish_failsafe():
        p_estop.publish(Bool(data=True))
        p_motion.publish(Bool(data=False))

    def on_estop(req, resp):
        publish_failsafe()
        dora_node.send_output("estop_cmd", pa.array([b"ESTOP"]))
        resp.success = True
        resp.message = "ESTOP forwarded to Dora robot_state_source"
        return resp

    ros_node.create_service(Trigger, "/robot/estop", on_estop)

    try:
        for event in dora_node:
            if event["type"] != "INPUT":
                if event["type"] == "STOP" or (
                    event["type"] == "INPUT_CLOSED" and event.get("id") == "robot_state"
                ):
                    publish_failsafe()
                rclpy.spin_once(ros_node, timeout_sec=0.0)
                continue
            eid = event["id"]
            if eid == "spin_tick":
                now = time.monotonic()
                if is_stale(last_seen, now) and (now - last_watchdog_pub) >= 0.5:
                    publish_failsafe()
                    last_watchdog_pub = now
                rclpy.spin_once(ros_node, timeout_sec=0.0)
                continue
            if eid != "robot_state":
                rclpy.spin_once(ros_node, timeout_sec=0.0)
                continue

            val = event.get("value")
            if val is None or len(val) == 0:
                publish_failsafe()
                rclpy.spin_once(ros_node, timeout_sec=0.0)
                continue
            try:
                raw = val[0].as_py()
                state = decode_robot_state(raw)
            except (ValueError, TypeError, UnicodeDecodeError):
                publish_failsafe()
                rclpy.spin_once(ros_node, timeout_sec=0.0)
                continue

            last_seen = time.monotonic()
            is_mock = bool(state.get("mock", False))
            estopped = True if is_mock else bool(state.get("estopped", True))
            motion_enabled = False if is_mock else bool(state.get("motion_enabled", False))
            state["estopped"] = estopped
            state["motion_enabled"] = motion_enabled
            p_state.publish(String(data=json.dumps(state)))
            p_estop.publish(Bool(data=estopped))
            p_motion.publish(Bool(data=motion_enabled))
            if state.get("rc") is not None:
                p_rc.publish(String(data=json.dumps(state["rc"])))
            rclpy.spin_once(ros_node, timeout_sec=0.0)
    finally:
        if rclpy.ok():
            publish_failsafe()
        ros_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
