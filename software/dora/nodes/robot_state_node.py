#!/usr/bin/env python3
"""Dora node that publishes canonical `/robot/state` JSON from `stamplc.reader`.

Reuses `build_robot_state` from `software/ros/robot_state.py` so safety derivation
lives in one place. When `OPENDUBS_DORA_MOCK=1`, emits a synthetic healthy state
for offline dataflow/latency testing without StamPLC hardware.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _candidate in (REPO_ROOT / "software" / "ros", pathlib.Path("/opt/opendubs/ros")):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from robot_state import build_robot_state  # noqa: E402


def _mock_snapshot() -> dict:
    # Safe-by-default mock snapshot: contactor open, RC OFF, deadman released.
    return {
        "fresh": True,
        "di": [True, False, False, False, False, False, False, False],
        "relay": [False, False, False, False],
        "rc": {"mode": "OFF", "dead": False, "live": False},
        "enc_count": 0,
        "lift": {"live": False, "height_mm": None},
        "drive": {"pinch": "ok"},
        "power_v": 24.0,
        "temp_c": 30.0,
        "pdh": {},
    }


def build_payload(
    snap: dict,
    lift_zero_mm: float | None = None,
    now: float | None = None,
    mock: bool = False,
    software_estop: bool = False,
) -> bytes:
    """Serialize `build_robot_state(snap)` as UTF-8 JSON bytes for Dora output."""
    state = build_robot_state(snap, lift_zero_mm=lift_zero_mm, now=now, software_estop=software_estop)
    if mock:
        # Never allow mock mode to publish motion_enabled=True or estopped=False.
        state["mock"] = True
        state["estopped"] = True
        state["motion_enabled"] = False
    return json.dumps(state).encode("utf-8")


def main():
    import pyarrow as pa
    from dora import Node

    use_mock = os.environ.get("OPENDUBS_DORA_MOCK", "0") == "1"
    z_env = os.environ.get("OPENDUBS_LIFT_TOF_ZERO_MM", "").strip()
    lift_zero_mm = float(z_env) if z_env else None
    sw_estop = False

    reader = None
    if not use_mock:
        candidates = [
            os.environ.get("OPENDUBS_DASH_BACKEND", ""),
            str(REPO_ROOT / "software" / "kiosk-dashboard" / "backend"),
            "/opt/opendubs/kiosk-dashboard/backend",
        ]
        for backend in candidates:
            if backend and pathlib.Path(backend).is_dir():
                if backend not in sys.path:
                    sys.path.insert(0, backend)
                break
        import stamplc  # noqa: E402

        reader = stamplc.reader

    node = Node()
    try:
        for event in node:
            if event["type"] != "INPUT":
                continue
            if event["id"] == "estop":
                sw_estop = True
                if reader is not None:
                    reader.send("MAST CMD 0.00")
                    reader.send("ESTOP")
            if event["id"] in ("tick", "estop"):
                snap = _mock_snapshot() if reader is None else reader.snapshot()
                payload = build_payload(
                    snap, lift_zero_mm=lift_zero_mm, now=time.time(), mock=use_mock, software_estop=sw_estop
                )
                node.send_output("robot_state", pa.array([payload]))
    finally:
        if reader is not None:
            reader.send("MAST CMD 0.00")


if __name__ == "__main__":
    main()
