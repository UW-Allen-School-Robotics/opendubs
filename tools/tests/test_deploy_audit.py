"""Tests for robot records and runtime provenance checks in tools/deploy-audit.py."""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_deploy_audit():
    spec = importlib.util.spec_from_file_location("deploy_audit", ROOT / "tools" / "deploy-audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_robot_records_load():
    da = _load_deploy_audit()
    records = da.list_robot_records(ROOT)
    assert "opendubs1" in records
    assert "opendubs2" in records
    assert records["opendubs2"]["revision"] == "rev-A"
    assert "openarm" in da.expected_containers(records["opendubs2"])


def test_expected_systemd_units_discovers_thor_only():
    da = _load_deploy_audit()
    units = da.expected_systemd_units(ROOT)
    assert "opendubs-dash.service" in units
    assert "openarm-can@.service" in units
    assert "stage-agent.service" not in units
    assert "opendubs-camera.service" not in units


def test_classify_runtime_flags_unowned_home_services_and_containers():
    da = _load_deploy_audit()
    probe = "\n".join([
        "---UNITS---",
        "opendubs-dash.service\tenabled\tactive\t/opt/opendubs/kiosk-dashboard/run.sh",
        "openarm-can@arm_left.service\tenabled\tactive\t/sbin/ip link set arm_left up",
        "docker.service\tenabled\tactive\t/usr/bin/dockerd",
        "student-exp.service\tenabled\tactivating\t/home/student/run_loop.sh",
        "mystery-loop.service\tenabled\tactive\t/usr/local/bin/mystery",
        "---CONTAINERS---",
        "openarm\topenarm-ros2:opendubs2\tUp 2 hours",
        "rogue-vla\tvla:latest\tUp 10 minutes",
        "---PROFILE---",
        "sensor-rig",
    ])
    units, containers, active_profile = da.parse_runtime_probe(probe)
    assert active_profile == "sensor-rig"
    suspect_units, unexpected_containers = da.classify_runtime(
        units,
        containers,
        da.expected_systemd_units(ROOT),
        {"openarm"},
    )
    assert [u["name"] for u in suspect_units] == ["student-exp.service", "mystery-loop.service"]
    assert suspect_units[0]["reason"] == "runs from /home"
    assert suspect_units[1]["reason"] == "not in repo"
    assert [c["name"] for c in unexpected_containers] == ["rogue-vla"]


def test_flat_reader_matches_pyyaml_on_records():
    """deploy-all.sh runs the audit on a python3 without PyYAML; its fallback must read every record as PyYAML does."""
    import yaml
    da = _load_deploy_audit()
    paths = sorted((ROOT / "robots").glob("*/robot.yaml"))
    assert paths
    for p in paths:
        assert da._flat_yaml(p.read_text()) == yaml.safe_load(p.read_text()), p
