import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import robot_state


def test_required_module_missing_surfaces_as_degraded_not_blocking():
    out = robot_state.derive(
        booting=False, estop_sources=[], authority="OFF", deadman=False,
        link=True, faults=["lift module not responding"], mast_fault=True,
    )
    assert out["state"] == "DEGRADED"
    assert "lift module not responding" in out["reason"]
    assert out["subsystems"]["mast"]["state"] == "FAULT"
    # DEGRADED must stay non-blocking — a silent required module warns, it doesn't stop drive
    # (docs/interfaces/payload-comms.md "Module presence and required-module alerting").
    base_idx = robot_state.SUBSYS_KEYS.index("base")
    assert robot_state.ENABLE["DEGRADED"][base_idx] != "off"


def test_lift_fault_alone_does_not_force_degraded():
    out = robot_state.derive(
        booting=False, estop_sources=[], authority="OFF", deadman=False,
        link=True, faults=[], mast_fault=True,
    )
    assert out["state"] != "DEGRADED"
    assert out["subsystems"]["mast"]["state"] == "FAULT"
