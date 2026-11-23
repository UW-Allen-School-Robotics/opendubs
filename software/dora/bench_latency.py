#!/usr/bin/env python3
"""Latency & period-jitter benchmark for comparing IPC transports (pipe / Dora / ROS 2).

Measures round-trip transport latency and loop period stability at target control rates
(e.g. 200 Hz or 500 Hz for arm SysID/torque control).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import time


def summarize_latencies(latencies_s: list[float], target_hz: float) -> dict:
    """Compute p50/p95/p99/max (ms) and deadline-miss count against `1 / target_hz`."""
    if not latencies_s:
        raise ValueError("latencies_s must be non-empty")
    if target_hz <= 0:
        raise ValueError("target_hz must be positive")
    ordered = sorted(float(x) for x in latencies_s)
    n = len(ordered)

    def pct(q: float) -> float:
        idx = min(n - 1, int(round(q * (n - 1))))
        return ordered[idx] * 1e3

    budget_s = 1.0 / float(target_hz)
    misses = sum(1 for x in ordered if x > budget_s)
    return {
        "samples": n,
        "target_hz": float(target_hz),
        "budget_ms": round(budget_s * 1e3, 3),
        "p50_ms": round(pct(0.50), 4),
        "p95_ms": round(pct(0.95), 4),
        "p99_ms": round(pct(0.99), 4),
        "max_ms": round(ordered[-1] * 1e3, 4),
        "deadline_misses": misses,
    }


def _pipe_echo(conn):
    while True:
        msg = conn.recv_bytes()
        if not msg:
            break
        conn.send_bytes(msg)


def _validate_bench_args(hz: float, samples: int, payload_bytes: int) -> None:
    if hz <= 0:
        raise ValueError("hz must be positive")
    if samples <= 0:
        raise ValueError("samples must be positive")
    if payload_bytes < 0:
        raise ValueError("payload_bytes must be non-negative")


def run_pipe_bench(hz: float = 200.0, samples: int = 200, payload_bytes: int = 128) -> dict:
    """Cross-process multiprocessing.Pipe round-trip baseline (no external deps)."""
    _validate_bench_args(hz, samples, payload_bytes)
    parent, child = multiprocessing.Pipe()
    proc = multiprocessing.Process(target=_pipe_echo, args=(child,), daemon=True)
    proc.start()
    payload = b"x" * payload_bytes
    latencies = []
    period = 1.0 / float(hz)
    try:
        for _ in range(samples):
            t0 = time.perf_counter()
            parent.send_bytes(payload)
            _ = parent.recv_bytes()
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
            rem = period - (t1 - t0)
            if rem > 0:
                time.sleep(rem)
    finally:
        try:
            parent.send_bytes(b"")
        except OSError:
            pass
        proc.join(timeout=2.0)
        if proc.is_alive():
            proc.terminate()
    return summarize_latencies(latencies, target_hz=hz)


def _ros2_echo_worker(topic_in: str, topic_out: str, stop_evt):
    import rclpy
    from std_msgs.msg import String

    rclpy.init()
    node = rclpy.create_node("dora_eval_ros2_echo")
    pub = node.create_publisher(String, topic_out, 10)
    node.create_subscription(String, topic_in, lambda msg: pub.publish(msg), 10)
    try:
        while not stop_evt.is_set():
            rclpy.spin_once(node, timeout_sec=0.005)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def run_ros2_bench(hz: float = 200.0, samples: int = 200, payload_bytes: int = 128) -> dict:
    """Cross-process ROS 2 topic round-trip latency baseline (requires `rclpy`, e.g. on Thor)."""
    _validate_bench_args(hz, samples, payload_bytes)
    import rclpy
    from std_msgs.msg import String

    topic_ping = "/opendubs/eval/ping"
    topic_pong = "/opendubs/eval/pong"
    stop_evt = multiprocessing.Event()
    echo_proc = multiprocessing.Process(
        target=_ros2_echo_worker, args=(topic_ping, topic_pong, stop_evt), daemon=True
    )
    echo_proc.start()

    rclpy.init()
    node = rclpy.create_node("dora_eval_ros2_bench")
    pub = node.create_publisher(String, topic_ping, 10)
    got = {"tag": None}
    node.create_subscription(String, topic_pong, lambda msg: got.__setitem__("tag", msg.data.split(":", 1)[0]), 10)
    latencies = []
    period = 1.0 / float(hz)
    try:
        deadline = time.monotonic() + 5.0
        while pub.get_subscription_count() < 1 and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
        if pub.get_subscription_count() < 1:
            raise TimeoutError("ROS 2 echo subscriber did not match within 5 s")

        # Warm up both ping and pong endpoints before recording timed samples
        warm_deadline = time.monotonic() + 5.0
        warm_msg = "warm:" + "x" * max(0, payload_bytes - 5)
        while got["tag"] != "warm" and time.monotonic() < warm_deadline:
            pub.publish(String(data=warm_msg))
            rclpy.spin_once(node, timeout_sec=0.05)
        if got["tag"] != "warm":
            raise TimeoutError("ROS 2 echo pong endpoint did not match within 5 s")

        for idx in range(samples):
            tag = str(idx)
            msg = f"{tag}:" + "x" * max(0, payload_bytes - len(tag) - 1)
            t0 = time.perf_counter()
            pub.publish(String(data=msg))
            wait_until = time.monotonic() + 2.0
            while got["tag"] != tag:
                if time.monotonic() >= wait_until:
                    raise TimeoutError("ROS 2 echo reply timed out")
                rclpy.spin_once(node, timeout_sec=0.001)
            t1 = time.perf_counter()
            latencies.append(t1 - t0)
            rem = period - (t1 - t0)
            if rem > 0:
                time.sleep(rem)
    finally:
        stop_evt.set()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        echo_proc.join(timeout=2.0)
        if echo_proc.is_alive():
            echo_proc.terminate()
    return summarize_latencies(latencies, target_hz=hz)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transport", choices=("pipe", "ros2"), default="pipe")
    ap.add_argument("--hz", type=float, default=200.0)
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--bytes", type=int, default=128)
    a = ap.parse_args()

    runner = run_pipe_bench if a.transport == "pipe" else run_ros2_bench
    result = runner(hz=a.hz, samples=a.samples, payload_bytes=a.bytes)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
