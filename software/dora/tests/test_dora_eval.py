"""Tests for isolated Dora evaluation (`software/dora/`) without requiring `dora-rs`."""

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
DORA_DIR = ROOT / "software" / "dora"
if str(DORA_DIR) not in sys.path:
    sys.path.insert(0, str(DORA_DIR))
if str(DORA_DIR / "nodes") not in sys.path:
    sys.path.insert(0, str(DORA_DIR / "nodes"))

import bench_latency  # noqa: E402
import robot_state_node  # noqa: E402
import ros2_bridge_node  # noqa: E402


def test_dataflow_yaml_valid():
    df = yaml.safe_load((DORA_DIR / "dataflow.yml").read_text())
    ids = [n["id"] for n in df["nodes"]]
    assert ids == ["robot_state_source", "ros2_bridge"]
    assert "robot_state" in df["nodes"][0]["outputs"]
    assert df["nodes"][0]["inputs"]["estop"] == "ros2_bridge/estop_cmd"
    assert df["nodes"][1]["inputs"]["robot_state"] == "robot_state_source/robot_state"
    assert df["nodes"][1]["inputs"]["spin_tick"] == "dora/timer/millis/20"
    assert "estop_cmd" in df["nodes"][1]["outputs"]


def test_robot_state_node_and_bridge_roundtrip():
    snap = robot_state_node._mock_snapshot()
    raw = robot_state_node.build_payload(snap, lift_zero_mm=0.0, now=123.0, mock=True)
    decoded = ros2_bridge_node.decode_robot_state(raw)
    assert decoded["stamp"] == 123.0
    assert decoded["mock"] is True
    assert decoded["estopped"] is True
    assert decoded["motion_enabled"] is False
    assert decoded["rc"]["ch5"] == "OFF"

    live_snap = {
        **snap,
        "relay": [True, False, False, False],
        "rc": {"mode": "AUTO", "dead": True, "live": True},
    }
    raw_live = robot_state_node.build_payload(live_snap, lift_zero_mm=0.0, now=124.0, mock=False)
    decoded_live = ros2_bridge_node.decode_robot_state(raw_live)
    assert decoded_live["estopped"] is False
    assert decoded_live["motion_enabled"] is True
    assert decoded_live["rc"]["ch5"] == "AUTO"

    raw_estop = robot_state_node.build_payload(live_snap, lift_zero_mm=0.0, now=125.0, mock=False, software_estop=True)
    decoded_estop = ros2_bridge_node.decode_robot_state(raw_estop)
    assert decoded_estop["estopped"] is True
    assert decoded_estop["motion_enabled"] is False
    assert "SOFTWARE" in decoded_estop["safety"]["source"]


def test_bench_latency_pipe_and_summary():
    import pytest

    summary = bench_latency.summarize_latencies([0.001, 0.002, 0.006], target_hz=200.0)
    assert summary["samples"] == 3
    assert summary["deadline_misses"] == 1
    res = bench_latency.run_pipe_bench(hz=200.0, samples=20, payload_bytes=64)
    assert res["samples"] == 20
    assert res["p50_ms"] > 0.0
    with pytest.raises(ValueError):
        bench_latency.run_pipe_bench(hz=0.0, samples=10)
    with pytest.raises(ValueError):
        bench_latency.run_pipe_bench(hz=200.0, samples=0)
    with pytest.raises(ValueError):
        bench_latency.run_pipe_bench(hz=200.0, samples=10, payload_bytes=-1)


def test_bridge_stale_watchdog_and_malformed_json():
    assert ros2_bridge_node.is_stale(None, 10.0) is True
    assert ros2_bridge_node.is_stale(8.5, 10.0) is False
    assert ros2_bridge_node.is_stale(6.0, 10.0) is True
    import pytest
    with pytest.raises(ValueError):
        ros2_bridge_node.decode_robot_state(b"{not-json")
    with pytest.raises(ValueError):
        ros2_bridge_node.decode_robot_state(b"null")
    with pytest.raises(ValueError):
        ros2_bridge_node.decode_robot_state(b"[1, 2]")
