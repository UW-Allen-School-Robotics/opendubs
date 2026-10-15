#!/usr/bin/env python3
"""Build, check or watch every file derived from the robot description.

One entry point for everything generated under description/, so that a consumer -- CI, the
dashboard's deploy, or a studio bundle that caches a simulation twin -- has one command to run and
one manifest to key on, instead of knowing each generator.

    python3 description/describe.py               build: write every derived file
    python3 description/describe.py --check       exit 1 if any derived file is stale
    python3 description/describe.py --watch       rebuild whenever an input changes (needs watchfiles)
    python3 description/describe.py --manifest    print {"inputs": {path: sha256}, "outputs": {...}}

Exit codes: 0 OK, 1 stale (--check), 2 bad input (a missing input, or a generator that crashed).
Paths in the manifest are repo-relative with forward slashes on every OS, sorted. --manifest hashes
what is on disk and builds nothing: run it after a build or a passing --check.

Each target is a generator that already has its own --check; this wraps them, it does not
reimplement them. A generator script is an INPUT, so changing one invalidates a cache keyed on the
manifest. No ROS is needed, and nothing here is OS-specific.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIS = Path(__file__).resolve()

# name -> (generator, its own inputs besides itself, its outputs). Paths relative to ROOT.
TARGETS = {
    "opendubs-urdf": ("description/opendubs/gen_urdf.py",
                      ["description/opendubs/dimensions.yaml"],
                      ["description/opendubs/opendubs.urdf"]),
    "openarm-chain": ("tools/arm_chain.py",
                      ["description/openarm/urdf/v1.urdf"],
                      ["description/openarm/chain.json"]),
}


def rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


def inputs() -> list[str]:
    files = {rel(THIS)}
    for gen, ins, _ in TARGETS.values():
        files.add(gen)
        files.update(ins)
    return sorted(files)


def outputs() -> list[str]:
    return sorted(o for _, _, outs in TARGETS.values() for o in outs)


def sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def missing_inputs() -> list[str]:
    return [p for p in inputs() if not (ROOT / p).is_file()]


def run(gen: str, check: bool) -> tuple[int, str]:
    """Run one generator; return (status, its output). status: 0 ok, 1 stale, 2 crashed."""
    cmd = [sys.executable, str(ROOT / gen)] + (["--check"] if check else [])
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        return 0, out
    # A clean "stale" exit is a one-line message; a crash is a traceback or a missing import.
    crashed = "Traceback (most recent call last)" in out or "ModuleNotFoundError" in out
    return (2 if crashed or not check else 1), out


def build_or_check(check: bool) -> int:
    gone = missing_inputs()
    if gone:
        for p in gone:
            print(f"MISSING input: {p}", file=sys.stderr)
        return 2
    worst = 0
    for name, (gen, _, _) in TARGETS.items():
        status, out = run(gen, check)
        word = {0: "OK   ", 1: "STALE", 2: "ERROR"}[status]
        first = out.splitlines()[0] if out else ""
        print(f"{word} {name}: {first}")
        if status:
            for line in out.splitlines()[1:]:
                print(f"      {line}")
        worst = max(worst, status)
    return worst


def manifest() -> int:
    gone = missing_inputs() + [p for p in outputs() if not (ROOT / p).is_file()]
    if gone:
        for p in gone:
            print(f"MISSING: {p}", file=sys.stderr)
        return 2
    print(json.dumps({"inputs": {p: sha256(p) for p in inputs()},
                      "outputs": {p: sha256(p) for p in outputs()}}, indent=2, sort_keys=True))
    return 0


def watch() -> int:
    try:
        from watchfiles import watch as wf
    except ImportError:
        print("--watch needs watchfiles: pip install watchfiles", file=sys.stderr)
        return 2
    watched = {(ROOT / p).resolve() for p in inputs()}
    print("building, then watching:", ", ".join(inputs()))
    build_or_check(check=False)
    for changes in wf(*{p.parent for p in watched}):
        if any(Path(path).resolve() in watched for _, path in changes):
            print("-- input changed, rebuilding")
            build_or_check(check=False)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if any derived file is stale")
    mode.add_argument("--watch", action="store_true", help="rebuild whenever an input changes")
    mode.add_argument("--manifest", action="store_true", help="print input and output hashes as JSON")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # a launcher reading a pipe sees each line at once
    if a.manifest:
        return manifest()
    if a.watch:
        return watch()
    return build_or_check(check=a.check)


if __name__ == "__main__":
    sys.exit(main())
