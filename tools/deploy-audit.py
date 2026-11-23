#!/usr/bin/env python3
"""Compare what is RUNNING on a robot against what is in the repo.

Files reach the robots through deploy-all.sh, deploy.sh and, too often, by hand in the middle of
debugging something else. So the repo and the robots drift, silently, and the drift is only
discovered when it breaks something. deploy-all.sh reads the PER_ROBOT list below
(--rsync-protect), so the audit and the deploy agree on which files are a robot's own.

It has already cost us. `platform = espressif32` was pinned to 7.0.1 in PR #9 on
2026-09-01 because an unpinned platform breaks the M5StamPLC build; opendubs2 never
received that change, and on 2026-09-08 building an unrelated project pulled a newer
platform and broke the StamPLC build outright. The fix had been merged for a week.

    tools/deploy-audit.py <robot>              what differs, classified
    tools/deploy-audit.py <robot> --diff PATH  the actual difference for one file
    tools/deploy-audit.py --list               the mapping, no robot needed
    tools/deploy-audit.py --rsync-protect DIR  rsync filters keeping DIR's per-robot files

WHY NOT JUST RSYNC. Some deployed files are SUPPOSED to differ. Drive calibration,
relay assignment and ESC spans are properties of an individual machine, discovered on
it, and overwriting them with repo values silently mis-configures the robot. Those
paths are marked PER_ROBOT below: this tool reports them for a human to read, and never
suggests they be synced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SYNC = "sync"            # should be identical; a difference is drift
PER_ROBOT = "per-robot"  # expected to differ; report, never auto-sync

# (repo path relative to ROOT, path on the robot, kind). Directories are compared by the
# files the REPO has — a robot-only file is reported separately rather than deleted.
MAP = [
    ("firmware/rc_serial_probe/src",            "/opt/opendubs/rc_serial_probe/src",            SYNC),
    ("firmware/common",                         "/opt/opendubs/common",                         SYNC),  # deploy-all.sh; the shared signed-update check
    ("firmware/rc_serial_probe/src/robot_config.h", "/opt/opendubs/rc_serial_probe/src/robot_config.h", PER_ROBOT),  # never in the repo
    ("firmware/rc_serial_probe/platformio.ini", "/opt/opendubs/rc_serial_probe/platformio.ini", PER_ROBOT),
    ("software/kiosk-dashboard/backend",        "/opt/opendubs/kiosk-dashboard/backend",        SYNC),
    ("software/kiosk-dashboard/frontend",       "/opt/opendubs/kiosk-dashboard/frontend",       SYNC),
    ("software/ros",                            "/opt/opendubs/ros",                            SYNC),
    ("description/opendubs",                    "/opt/opendubs/description",                    SYNC),  # deploy-all.sh; opendubs.urdf
    ("software/stack",                          "/opt/opendubs/stack",                          SYNC),
    ("software/face-web",                       "/opt/opendubs/face-web",                       SYNC),  # deploy.sh, /face/
    ("software/voice",                          "/opt/opendubs/voice",                          SYNC),  # deploy-all.sh; run by stack/voice
    ("software/head-protocol",                  "/opt/opendubs/head-protocol",                  SYNC),  # deploy-all.sh; the dashboard and the runner import it
    ("tools/network",                           "/opt/opendubs/network",                        SYNC),  # deploy-all.sh; opendubs-netmon
    ("software/openarm",                        "/opt/opendubs/openarm",                        SYNC),
]

# Repo paths a deploy deliberately leaves on the dev machine, so their absence on the robot is not
# reported. Keep this in step with the excludes in the deploy scripts that ship each tree.
NOT_SHIPPED = (
    "software/face-web/kws/",   # the wake-word build script; deploy.sh ships only its output, vendor/kws
)

# Never compared: build output, caches, editor droppings, and the ad-hoc backups we leave
# behind on the robot mid-debug (main.cpp.pre-mast, platformio.ini.bak, ...).
SKIP_SUFFIX = (".pyc", ".bin", ".elf", ".o")
SKIP_PART = (".pio", "__pycache__", ".git", ".bak", ".pre-", ".orig", "node_modules")


def skip(name: str) -> bool:
    return name.endswith(SKIP_SUFFIX) or any(p in name for p in SKIP_PART)


def repo_files(rel: str):
    p = ROOT / rel
    if p.is_file():
        return {rel: p}
    if not p.is_dir():
        return {}
    out = {}
    for f in sorted(p.rglob("*")):
        if f.is_file() and not skip(str(f.relative_to(ROOT))):
            out[str(f.relative_to(ROOT))] = f
    return out


def md5(path: pathlib.Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def remote_md5(robot: str, paths: list[str]) -> dict[str, str]:
    """One ssh round trip for every file — a per-file ssh makes this unusable over the
    robot's link."""
    if not paths:
        return {}
    script = "for f in " + " ".join(f"'{p}'" for p in paths) + \
             "; do if [ -f \"$f\" ]; then md5sum \"$f\"; else echo \"MISSING $f\"; fi; done"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", robot, script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ssh to {robot} failed: {(r.stderr or '').strip() or r.returncode}")
    out = {}
    for line in r.stdout.splitlines():
        if line.startswith("MISSING "):
            out[line[8:]] = None
        elif line.strip():
            h, _, f = line.partition("  ")
            out[f.strip()] = h.strip()
    return out


def build_pairs():
    """repo file -> (robot path, kind). Later MAP entries win, so a specific file can
    override the directory it sits in."""
    pairs = {}
    for rel, remote, kind in MAP:
        files = repo_files(rel)
        if (ROOT / rel).is_file():
            pairs[rel] = (remote, kind)
            continue
        for f in files:
            if f.startswith(NOT_SHIPPED):
                continue
            sub = f[len(rel) + 1:]
            pairs[f] = (f"{remote}/{sub}", kind)
    for rel, remote, kind in MAP:
        if (ROOT / rel).is_file():
            pairs[rel] = (remote, kind)
    return pairs


# Systemd units in /etc/systemd/system/ that come from OS packages rather than this repo.
OS_SYSTEMD_UNITS = frozenset({
    "containerd.service",
    "docker.service",
    "snapd.service",
})

# Repo subtrees whose .service files belong to a separate host (not the Thor).
NON_THOR_SERVICE_PREFIXES = (
    "notes/",
    "software/stage-pi/",
)


def expected_systemd_units(root: pathlib.Path = ROOT) -> set[str]:
    """Every systemd unit name (*.service) that the repo ships for the Thor."""
    units = set()
    for p in root.rglob("*.service"):
        rel = str(p.relative_to(root))
        if rel.startswith(NON_THOR_SERVICE_PREFIXES) or skip(rel):
            continue
        units.add(p.name)
    return units


def _flat_yaml(text: str) -> dict:
    """The records' own subset of YAML, for a python3 without PyYAML (the Mac's PATH python, which runs
    deploy-all.sh): top-level `key: value`, `key: []`, and under a bare `key:` either `- item` lines or
    one level of `name: value` lines. Comments and quotes are stripped."""
    def val(v: str):
        v = v.split(" #", 1)[0].strip().strip("\"'")
        return [] if v == "[]" else v
    data, key = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            k, _, v = line.partition(":")
            key = k.strip()
            data[key] = val(v) if v.strip() else None
        elif key is not None and line.strip().startswith("- "):
            data[key] = (data[key] or []) + [val(line.strip()[2:])]
        elif key is not None and ":" in line:
            k, _, v = line.strip().partition(":")
            data[key] = {**(data[key] or {}), k.strip(): val(v)}
    return data


def _read_yaml(text: str):
    try:
        import yaml   # optional: --rsync-protect and the audit run on a python3 without it
    except ImportError:
        return _flat_yaml(text)
    return yaml.safe_load(text)


def load_robot_record(unit: str, root: pathlib.Path = ROOT) -> dict | None:
    """Load robots/<unit>/robot.yaml if present; raise ValueError if malformed."""
    path = root / "robots" / unit / "robot.yaml"
    if not path.is_file():
        return None
    data = _read_yaml(path.read_text())
    if not isinstance(data, dict) or not data.get("unit"):
        raise ValueError(f"{path}: expected a mapping with 'unit'")
    return data


def list_robot_records(root: pathlib.Path = ROOT) -> dict[str, dict]:
    """Load all robots/<unit>/robot.yaml records keyed by unit name."""
    records = {}
    rdir = root / "robots"
    if not rdir.is_dir():
        return records
    for p in sorted(rdir.glob("*/robot.yaml")):
        unit = p.parent.name
        rec = load_robot_record(unit, root=root)
        if rec is not None:
            records[unit] = rec
    return records


def expected_containers(record: dict | None) -> set[str]:
    """Container names expected on a robot from its record (plus base optional ones)."""
    if record and isinstance(record.get("containers"), list):
        return {str(c) for c in record["containers"]}
    return {"openarm"}


def parse_runtime_probe(stdout: str) -> tuple[list[dict], list[dict], str | None]:
    """Parse batched SSH output of custom /etc/systemd/system/*.service units, docker ps -a, and /etc/opendubs/profile."""
    units, containers, active_profile = [], [], None
    section = None
    for line in stdout.splitlines():
        if line == "---UNITS---":
            section = "units"
            continue
        if line == "---CONTAINERS---":
            section = "containers"
            continue
        if line == "---PROFILE---":
            section = "profile"
            continue
        if not line.strip():
            continue
        if section == "units":
            parts = line.split("\t", 3)
            if len(parts) == 4:
                units.append({
                    "name": parts[0],
                    "enabled": parts[1],
                    "active": parts[2],
                    "exec_start": parts[3],
                })
        elif section == "containers":
            parts = line.split("\t", 2)
            if len(parts) == 3:
                containers.append({
                    "name": parts[0],
                    "image": parts[1],
                    "status": parts[2],
                })
        elif section == "profile" and active_profile is None:
            active_profile = line.strip()
    return units, containers, active_profile


def classify_runtime(
    units: list[dict],
    containers: list[dict],
    known_units: set[str],
    known_containers: set[str],
) -> tuple[list[dict], list[dict]]:
    """Return (unowned_or_suspect_units, unexpected_containers)."""
    suspect_units = []
    for u in units:
        name = u["name"]
        exec_start = u.get("exec_start", "")
        from_home = "/home/" in exec_start
        # Template instances like openarm-can@arm_left.service match openarm-can@.service
        template = re.sub(r"@[^.]+\.service$", "@.service", name)
        owned = name in known_units or template in known_units or name in OS_SYSTEMD_UNITS
        if not owned or from_home:
            suspect_units.append({**u, "reason": "runs from /home" if from_home else "not in repo"})
    unexpected_containers = [c for c in containers if c["name"] not in known_containers]
    return suspect_units, unexpected_containers


def remote_runtime_probe(robot: str) -> tuple[list[dict], list[dict], str | None]:
    """Inspect /etc/systemd/system/*.service, docker containers, and /etc/opendubs/profile in one SSH round trip."""
    script = (
        "echo '---UNITS---'; "
        "for f in /etc/systemd/system/*.service; do "
        "[ -f \"$f\" ] && [ ! -L \"$f\" ] || continue; "
        "n=$(basename \"$f\"); "
        "en=$(systemctl is-enabled \"$n\" 2>/dev/null) || [ -n \"$en\" ] || en=unknown; "
        "ac=$(systemctl is-active \"$n\" 2>/dev/null) || [ -n \"$ac\" ] || ac=unknown; "
        "ex=$(grep -m1 '^ExecStart=' \"$f\" 2>/dev/null | cut -d= -f2- | tr '\\t' ' '); "
        "printf '%s\\t%s\\t%s\\t%s\\n' \"$n\" \"$en\" \"$ac\" \"$ex\"; "
        "done; "
        "echo '---CONTAINERS---'; "
        "if command -v docker >/dev/null 2>&1; then "
        "docker ps -a --format '{{.Names}}\\t{{.Image}}\\t{{.Status}}' 2>/dev/null || true; "
        "fi; "
        "echo '---PROFILE---'; "
        "cat /etc/opendubs/profile 2>/dev/null || true"
    )
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", robot, script],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ssh to {robot} failed: {(r.stderr or '').strip() or r.returncode}")
    return parse_runtime_probe(r.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("robot", nargs="?", help="ssh host, e.g. opendubs2")
    ap.add_argument("--diff", metavar="PATH", help="show the difference for one repo path")
    ap.add_argument("--list", action="store_true", help="print the mapping and exit")
    ap.add_argument("--check-records", action="store_true", help="validate robots/*/robot.yaml records offline and exit")
    ap.add_argument("--rsync-protect", metavar="DIR",
                    help="print rsync filter rules that keep DIR's PER_ROBOT files and the "
                         "robot-side backups from being overwritten or deleted (deploy-all.sh)")
    a = ap.parse_args()

    if a.rsync_protect:
        # One list, read by the audit AND by the deploy: a path marked PER_ROBOT here is never
        # rsynced over. --exclude also shields the receiver's copy from --delete.
        base = a.rsync_protect.rstrip("/") + "/"
        for rel, _remote, kind in MAP:
            if kind == PER_ROBOT and rel.startswith(base):
                print(f"--exclude=/{rel[len(base):]}")
        for part in (".bak", ".pre-", ".orig"):   # backups left mid-debug (see SKIP_PART)
            print(f"--exclude=*{part}*")
        return

    if a.check_records:
        tools_dir = str(pathlib.Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import profiles

        profs, _ = profiles.check_all(ROOT)
        records = list_robot_records()
        for unit, rec in sorted(records.items()):
            print(f"  {unit}: rev={rec.get('revision', '?')} profile={rec.get('profile', '?')} modules={len(rec.get('modules', []))}")
        print(f"\n{len(records)} robot record(s), {len(profs)} profile(s) OK")
        return

    pairs = build_pairs()

    if a.list:
        for rel, (remote, kind) in sorted(pairs.items()):
            print(f"  {kind:9} {rel}  ->  {remote}")
        print(f"\n{len(pairs)} files mapped, {len(expected_systemd_units())} systemd units, {len(list_robot_records())} robot records")
        return
    if not a.robot:
        ap.error("a robot is required (or --list)")

    if a.diff:
        remote, _ = pairs.get(a.diff, (None, None))
        if not remote:
            sys.exit(f"{a.diff} is not a deployed path — try --list")
        subprocess.run(f"ssh {a.robot} cat '{remote}' | diff -u - '{ROOT / a.diff}' "
                       f"--label robot:{remote} --label repo:{a.diff}", shell=True)
        return

    remote_hashes = remote_md5(a.robot, [r for r, _ in pairs.values()])
    same, drift, review, missing = [], [], [], []
    for rel, (remote, kind) in sorted(pairs.items()):
        rh = remote_hashes.get(remote, None)
        lh = md5(ROOT / rel)
        if rh is None:
            missing.append((rel, remote))
        elif rh == lh:
            same.append(rel)
        elif kind == PER_ROBOT:
            review.append((rel, remote))
        else:
            drift.append((rel, remote))

    print(f"\n{a.robot}:  {len(same)} identical, {len(drift)} DRIFTED, "
          f"{len(review)} per-robot, {len(missing)} not deployed\n")
    # What the last deploy-all.sh carried (its /opt/opendubs/DEPLOYED), so a branch on the robot is
    # never mistaken for main. "identical" above is against THIS checkout, whatever it is.
    marker = subprocess.run(["ssh", a.robot, "cat /opt/opendubs/DEPLOYED 2>/dev/null"],
                            capture_output=True, text=True).stdout
    last = dict(l.split("=", 1) for l in marker.splitlines() if "=" in l)
    unit_name = (last.get("robot") or a.robot).split("@")[-1].removesuffix(".local")
    record = load_robot_record(unit_name)
    if record:
        print(f"  record ({unit_name}): revision={record.get('revision', '?')}, "
              f"profile={record.get('profile', '?')}, modules={', '.join(record.get('modules', []))}\n")
    else:
        print(f"  record ({unit_name}): NONE (no robots/{unit_name}/robot.yaml in repo)\n")
    if last.get("source"):
        flag = "" if last["source"].startswith("main @") else "  <-- NOT main"
        print(f"  last deploy: {last['source']}, {last.get('when', '?')}{flag}\n")
    else:
        print("  last deploy: unknown (no /opt/opendubs/DEPLOYED; deployed before deploy-all.sh wrote it)\n")

    units, containers, active_profile = remote_runtime_probe(a.robot)
    if active_profile:
        expected_prof = record.get("profile") if record else None
        mismatch = f"  <-- MISMATCH (record expects {expected_prof})" if expected_prof and active_profile != expected_prof else ""
        print(f"  active profile: {active_profile}{mismatch}\n")
    suspect_units, unexpected_containers = classify_runtime(
        units, containers, expected_systemd_units(), expected_containers(record)
    )
    # The tablet's kiosk app is not a deployed file but a signed build (deploy-all.sh's deploy_kiosk):
    # compare the version the robot serves with the one this checkout's source says.
    gradle = (ROOT / "software/tablet-kiosk/app/build.gradle.kts").read_text()
    want = re.search(r"^\s*versionCode\s*=\s*(\d+)", gradle, re.M)
    wname = re.search(r'^\s*versionName\s*=\s*"([^"]*)"', gradle, re.M)
    served = subprocess.run(["ssh", a.robot, "cat /var/lib/opendubs/kiosk/latest.json 2>/dev/null"],
                            capture_output=True, text=True).stdout
    try:
        sv = json.loads(served)
        scode, sname = int(sv["versionCode"]), sv.get("versionName", "?")
    except (ValueError, KeyError, TypeError):
        scode, sname = 0, "nothing"
    if want:
        behind = "  <-- BEHIND: the tablet will not update until a build is published" if scode < int(want.group(1)) else ""
        print(f"  kiosk app: robot serves {sname} ({scode}), this checkout is "
              f"{wname.group(1) if wname else '?'} ({want.group(1)}){behind}\n")
    if drift:
        print("  DRIFTED — repo and robot disagree, and they should not:")
        for rel, _ in drift:
            print(f"    {rel}")
        print("    (tools/deploy-audit.py %s --diff <path> to see one)\n" % a.robot)
    if missing:
        print("  NOT DEPLOYED — in the repo, absent on the robot:")
        for rel, remote in missing:
            print(f"    {rel}  ->  {remote}")
        print()
    if review:
        print("  PER-ROBOT — expected to differ; read before touching:")
        for rel, _ in review:
            print(f"    {rel}")
        print()
    if suspect_units:
        print("  UNOWNED / SUSPECT SYSTEMD UNITS — not installed by this repo (or running from /home):")
        for u in suspect_units:
            print(f"    {u['name']} ({u['enabled']}/{u['active']}, {u['reason']}): {u['exec_start']}")
        print()
    if unexpected_containers:
        print("  UNEXPECTED DOCKER CONTAINERS — not in the robot record:")
        for c in unexpected_containers:
            print(f"    {c['name']} ({c['image']}): {c['status']}")
        print()
    if not drift and not missing and not suspect_units and not unexpected_containers:
        print("  nothing drifted.\n")
    sys.exit(1 if (drift or suspect_units or unexpected_containers) else 0)


if __name__ == "__main__":
    main()
