"""Tests for robot_state state building and Z-axis lease/command helpers."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import robot_state  # noqa: E402
from base_selector import Lease, parse  # noqa: E402


def test_build_robot_state_from_raw_stamplc_snapshot():
    raw_snap = {
        "fresh": True,
        "di": [True, False, False, False, False, False, False, False],  # DI0 HIGH = e-stop released; DI1 LOW = not welded
        "relay": [True, False, False, False],  # R0 closed = contactor energized
        "rc": {"mode": "AUTO", "dead": True, "live": True},
        "enc_count": 1234,
        "lift": {"live": True, "height_mm": 250.0},
        "drive": {"pinch": "ok"},
        "power_v": 24.1,
        "temp_c": 31.5,
        "pdh": {"v": 24.0, "i": 3.2, "p": 76.8},
    }
    st = robot_state.build_robot_state(raw_snap, lift_zero_mm=100.0, now=1000.0)
    assert st["estopped"] is False
    assert st["motion_enabled"] is True
    assert st["safety"]["welded"] is False
    assert st["power"]["bus_v"] == 24.1
    assert st["power"]["pdh"]["v"] == 24.0
    assert st["zaxis"]["encoder"] == 1234
    assert st["zaxis"]["travel_m"] == pytest.approx(0.15, abs=1e-6)

    # Physical e-stop button pressed (DI0 LOW)
    btn_snap = {**raw_snap, "di": [False, False, False, False, False, False, False, False]}
    st_btn = robot_state.build_robot_state(btn_snap, now=1001.0)
    assert st_btn["estopped"] is True
    assert st_btn["motion_enabled"] is False
    assert "BUTTON" in st_btn["safety"]["source"]

    # Stale telemetry forces fail-safe estopped=True
    stale_snap = {**raw_snap, "fresh": False}
    st_stale = robot_state.build_robot_state(stale_snap, now=1002.0)
    assert st_stale["estopped"] is True
    assert st_stale["motion_enabled"] is False


def test_build_robot_state_safe_and_stale():
    snap = {
        "safety": {"fresh": True, "estopped": False, "motion_enabled": True, "welded": False, "source": "ok"},
        "rc": {"mode": "AUTO", "dead": True, "live": True},
        "encoder": {"count": 1234},
        "lift": {"fresh": True, "height_mm": 250.0},
        "drive": {"pinch": "ok"},
        "io": {"bus_v": 24.1, "temp_c": 31.5},
        "power": {"pdh": {"v": 24.0, "i": 3.2, "p": 76.8}},
    }
    st = robot_state.build_robot_state(snap, lift_zero_mm=100.0, now=1000.0)
    assert st["estopped"] is False
    assert st["motion_enabled"] is True
    assert st["rc"] == {"ch5": "AUTO", "deadman": True, "live": True}
    assert st["zaxis"]["travel_m"] == pytest.approx(0.15, abs=1e-6)
    assert st["zaxis"]["pinch"] == "ok"

    # Stale safety forces estopped=True and motion_enabled=False
    stale_snap = {**snap, "safety": {**snap["safety"], "fresh": False}}
    st_stale = robot_state.build_robot_state(stale_snap, lift_zero_mm=100.0, now=1001.0)
    assert st_stale["estopped"] is True
    assert st_stale["motion_enabled"] is False


def test_zaxis_cmd_to_mast():
    assert robot_state.zaxis_cmd_to_mast("up") == "MAST CMD 1.00"
    assert robot_state.zaxis_cmd_to_mast("DOWN") == "MAST CMD -1.00"
    assert robot_state.zaxis_cmd_to_mast(" stop ") == "MAST CMD 0.00"
    with pytest.raises(ValueError):
        robot_state.zaxis_cmd_to_mast("jump")


def test_zaxis_lease_arbitration():
    lease = Lease()
    ok, reason = lease.claim("manip", 2.0, now=10.0)
    assert ok and reason == "granted"
    ok2, reason2 = lease.claim("show", 2.0, now=10.5)
    assert not ok2 and "held by manip" in reason2
    assert lease.current(12.1) is None
    ok3, _ = lease.claim("show", 2.0, now=12.2)
    assert ok3
    assert lease.release("*", now=12.5) is True
    assert lease.current(12.5) is None


def test_can_clear_estop_guard():
    base = {
        "fresh": True,
        "di": [True, False, False, False, False, False, False, False],
        "relay": [False, False, False, False],
        "rc": {"mode": "OFF", "dead": False, "live": True},
    }
    assert robot_state.can_clear_estop(base)[0] is True
    assert robot_state.can_clear_estop({**base, "fresh": False})[0] is False
    assert robot_state.can_clear_estop({**base, "di": [False, False, False, False, False, False, False, False]})[0] is False
    assert robot_state.can_clear_estop({**base, "di": [True, True, False, False, False, False, False, False]})[0] is False
    assert robot_state.can_clear_estop({**base, "rc": {"mode": "AUTO", "dead": False, "live": True}})[0] is False

    st = robot_state.build_robot_state(base, software_estop=True, now=1.0)
    assert st["estopped"] is True
    assert "SOFTWARE" in st["safety"]["source"]


def test_check_zaxis_motion_allowed_blocks_estop():
    healthy = {
        "fresh": True,
        "di": [True, False, False, False, False, False, False, False],
        "relay": [True, False, False, False],
        "rc": {"mode": "AUTO", "dead": True, "live": True},
    }
    assert robot_state.check_zaxis_motion_allowed(healthy) == (True, "ok")
    ok_sw, reason_sw = robot_state.check_zaxis_motion_allowed(healthy, software_estop=True)
    assert ok_sw is False and "SOFTWARE" in reason_sw
    ok_btn, reason_btn = robot_state.check_zaxis_motion_allowed(
        {**healthy, "di": [False, False, False, False, False, False, False, False]}
    )
    assert ok_btn is False and "BUTTON" in reason_btn
    ok_plc, reason_plc = robot_state.check_zaxis_motion_allowed(
        {**healthy, "rc": {"mode": "ESTOP", "dead": False, "live": True}}
    )
    assert ok_plc is False and "STAMPLC" in reason_plc
    ok_stale, _ = robot_state.check_zaxis_motion_allowed({**healthy, "fresh": False})
    assert ok_stale is False

