#!/usr/bin/env python3
"""Operational profiles (`robots/profiles/*.yaml`) for OpenDubs robots.

Profiles declare which `/etc/opendubs/*.enabled` ConditionPathExists flags and optional
systemd units belong to a mode (`demo`, `sensor-rig`, ...), so switching a robot between
outreach demos and research rigs is explicit, reproducible, and recorded on the unit.
"""
from __future__ import annotations

import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VALID_FLAG = re.compile(r"^[a-z0-9][a-z0-9_-]*\.enabled$")
VALID_UNIT = re.compile(r"^[a-z0-9][a-z0-9@._-]*$")


def _flat_yaml(text: str) -> dict:
    """Parse the profiles/records YAML subset without requiring PyYAML (for Mac PATH python3)."""
    def scalar(v: str):
        v = v.split(" #", 1)[0].strip().strip("\"'")
        if v == "[]":
            return []
        if v == "true":
            return True
        if v == "false":
            return False
        return v

    data: dict = {}
    top_key = None
    sub_key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        if indent == 0:
            k, _, v = stripped.partition(":")
            top_key = k.strip()
            sub_key = None
            data[top_key] = scalar(v) if v.strip() else {}
        elif top_key is not None:
            if stripped.startswith("- "):
                item = scalar(stripped[2:])
                if sub_key is not None:
                    cur = data[top_key].setdefault(sub_key, [])
                    cur.append(item)
                else:
                    if not isinstance(data[top_key], list):
                        data[top_key] = []
                    data[top_key].append(item)
            elif ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                if v.strip():
                    data[top_key][k] = scalar(v)
                    sub_key = None
                else:
                    sub_key = k
                    data[top_key][sub_key] = []
    return data


def _read_yaml(text: str):
    try:
        import yaml  # optional: deploy-all.sh runs on Mac PATH python3 without PyYAML
    except ImportError:
        return _flat_yaml(text)
    return yaml.safe_load(text)


def load_profile(name: str, root: pathlib.Path = ROOT) -> dict:
    """Load and validate `robots/profiles/<name>.yaml`."""
    if not VALID_NAME.match(name):
        raise ValueError(f"invalid profile name {name!r}")
    path = root / "robots" / "profiles" / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"profile {name!r} not found at {path}")
    data = _read_yaml(path.read_text())
    if not isinstance(data, dict) or data.get("profile") != name:
        raise ValueError(f"{path}: expected mapping with 'profile: {name}'")
    flags = data.get("flags") or {}
    if not isinstance(flags, dict):
        raise ValueError(f"{path}: 'flags' must be a mapping")
    for flag, val in flags.items():
        if not VALID_FLAG.match(str(flag)):
            raise ValueError(f"{path}: invalid flag name {flag!r} (must end in .enabled)")
        if not isinstance(val, bool):
            raise ValueError(f"{path}: flag {flag!r} must be true or false")
    services = data.get("services") or {}
    if not isinstance(services, dict):
        raise ValueError(f"{path}: 'services' must be a mapping")
    for action in ("start", "stop"):
        items = services.get(action) or []
        if not isinstance(items, list):
            raise ValueError(f"{path}: services.{action} must be a list")
        for u in items:
            if not VALID_UNIT.match(str(u)):
                raise ValueError(f"{path}: invalid unit name {u!r}")
    return data


def list_profiles(root: pathlib.Path = ROOT) -> dict[str, dict]:
    """Load all `robots/profiles/*.yaml` keyed by profile name."""
    pdir = root / "robots" / "profiles"
    out = {}
    if not pdir.is_dir():
        return out
    for p in sorted(pdir.glob("*.yaml")):
        out[p.stem] = load_profile(p.stem, root=root)
    return out


def resolve_profile(unit: str | None, override: str | None = None, root: pathlib.Path = ROOT) -> str:
    """Choose the profile for `unit`: explicit override -> `robots/<unit>/robot.yaml` -> 'demo'."""
    if override:
        load_profile(override, root=root)
        return override
    if unit:
        rec_path = root / "robots" / unit / "robot.yaml"
        if rec_path.is_file():
            rec = _read_yaml(rec_path.read_text()) or {}
            prof = rec.get("profile")
            if prof:
                load_profile(str(prof), root=root)
                return str(prof)
    return "demo"


def render_apply_script(profile: dict) -> str:
    """Emit a safe, idempotent shell script to apply `profile` on a Thor."""
    name = profile["profile"]
    lines = [
        "set -euo pipefail",
        "sudo install -d -m 0755 /etc/opendubs",
        f"printf '%s\\n' '{name}' | sudo tee /etc/opendubs/profile >/dev/null",
    ]
    for flag, enabled in sorted((profile.get("flags") or {}).items()):
        target = f"/etc/opendubs/{flag}"
        if enabled:
            lines.append(f"sudo touch '{target}'")
        else:
            lines.append(f"sudo rm -f '{target}'")
    services = profile.get("services") or {}
    for unit in services.get("stop") or []:
        lines.append(f"sudo systemctl stop '{unit}' >/dev/null 2>&1 || true")
    for unit in services.get("start") or []:
        lines.append(f"sudo systemctl restart '{unit}' >/dev/null 2>&1 || true")
    return "\n".join(lines) + "\n"


def check_all(root: pathlib.Path = ROOT) -> tuple[dict[str, dict], list[str]]:
    """Validate all profiles and verify every robot record names an existing profile."""
    profiles = list_profiles(root=root)
    checked_records = []
    rdir = root / "robots"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*/robot.yaml")):
            rec = _read_yaml(p.read_text()) or {}
            prof = rec.get("profile")
            if prof not in profiles:
                raise ValueError(f"{p}: references unknown profile {prof!r}")
            checked_records.append(p.parent.name)
    return profiles, checked_records


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="list available profiles")
    ap.add_argument("--check", action="store_true", help="validate all profiles and robot records")
    ap.add_argument("--resolve", metavar="UNIT", help="print resolved profile name for UNIT")
    ap.add_argument("--override", metavar="PROFILE", help="explicit profile override for --resolve")
    ap.add_argument("--apply-script", metavar="PROFILE", help="print shell script to apply PROFILE")
    a = ap.parse_args()

    if a.check:
        profiles, records = check_all()
        print(f"{len(profiles)} profile(s), {len(records)} robot record(s) OK")
        return
    if a.list:
        for name, data in sorted(list_profiles().items()):
            print(f"  {name:14} {data.get('description', '')}")
        return
    if a.resolve is not None:
        print(resolve_profile(a.resolve or None, override=a.override))
        return
    if a.apply_script:
        print(render_apply_script(load_profile(a.apply_script)), end="")
        return
    ap.print_help()


if __name__ == "__main__":
    main()
