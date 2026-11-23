"""Tests for operational profiles (`robots/profiles/*.yaml` and `tools/profiles.py`)."""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_profiles():
    spec = importlib.util.spec_from_file_location("profiles", ROOT / "tools" / "profiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_profiles_and_records_cross_check():
    pr = _load_profiles()
    profiles, records = pr.check_all(ROOT)
    assert "demo" in profiles
    assert "sensor-rig" in profiles
    assert "opendubs1" in records and "opendubs2" in records


def test_resolve_profile_override_and_fallback():
    pr = _load_profiles()
    assert pr.resolve_profile("opendubs2", root=ROOT) == "demo"
    assert pr.resolve_profile("opendubs2", override="sensor-rig", root=ROOT) == "sensor-rig"
    assert pr.resolve_profile("nonexistent_unit", root=ROOT) == "demo"
    with pytest.raises(FileNotFoundError):
        pr.resolve_profile("opendubs2", override="does-not-exist", root=ROOT)


def test_render_apply_script_for_sensor_rig():
    pr = _load_profiles()
    script = pr.render_apply_script(pr.load_profile("sensor-rig", root=ROOT))
    assert "/etc/opendubs/profile" in script
    assert "sudo touch '/etc/opendubs/slam.enabled'" in script
    assert "sudo rm -f '/etc/opendubs/nav2.enabled'" in script
    assert "sudo systemctl stop 'opendubs-dance-tracker'" in script
    assert "sudo systemctl restart 'opendubs-slam'" in script


def test_flat_yaml_matches_pyyaml_on_all_profiles():
    import yaml

    pr = _load_profiles()
    for path in sorted((ROOT / "robots" / "profiles").glob("*.yaml")):
        text = path.read_text()
        assert pr._flat_yaml(text) == yaml.safe_load(text)
