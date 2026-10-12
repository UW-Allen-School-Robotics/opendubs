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
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

SYNC = "sync"            # should be identical; a difference is drift
PER_ROBOT = "per-robot"  # expected to differ; report, never auto-sync

# (repo path relative to ROOT, path on the robot, kind). Directories are compared by the
# files the REPO has — a robot-only file is reported separately rather than deleted.
MAP = [
    ("firmware/rc_serial_probe/src",            "/opt/opendubs/rc_serial_probe/src",            SYNC),
    ("firmware/rc_serial_probe/src/main.cpp",   "/opt/opendubs/rc_serial_probe/src/main.cpp",   PER_ROBOT),
    ("firmware/rc_serial_probe/platformio.ini", "/opt/opendubs/rc_serial_probe/platformio.ini", PER_ROBOT),
    ("software/kiosk-dashboard/backend",        "/opt/opendubs/kiosk-dashboard/backend",        SYNC),
    ("software/kiosk-dashboard/frontend",       "/opt/opendubs/kiosk-dashboard/frontend",       SYNC),
    ("software/ros",                            "/opt/opendubs/ros",                            SYNC),
    ("software/openarm",                        "/opt/opendubs/openarm",                        SYNC),
]

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
            sub = f[len(rel) + 1:]
            pairs[f] = (f"{remote}/{sub}", kind)
    for rel, remote, kind in MAP:
        if (ROOT / rel).is_file():
            pairs[rel] = (remote, kind)
    return pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("robot", nargs="?", help="ssh host, e.g. opendubs2")
    ap.add_argument("--diff", metavar="PATH", help="show the difference for one repo path")
    ap.add_argument("--list", action="store_true", help="print the mapping and exit")
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

    pairs = build_pairs()

    if a.list:
        for rel, (remote, kind) in sorted(pairs.items()):
            print(f"  {kind:9} {rel}  ->  {remote}")
        print(f"\n{len(pairs)} files mapped")
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
    if not drift and not missing:
        print("  nothing drifted.\n")
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
