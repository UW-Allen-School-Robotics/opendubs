#!/usr/bin/env python3
"""Build the studio's wheel: opendubs_studio-<version>-py3-none-any.whl.

    python3 software/choreo/studio/build_wheel.py --out <dir>     # needs uv and git

The wheel is the launcher (studio.py) plus a copy of the code it runs, in the repository's own
layout under opendubs_studio/tree/, so every path the code works out from its own location stays
true: software/choreo (composer, bridge, twin), software/face-web (the face page and its door),
software/head-protocol, sim/ and description/.

WHAT GOES IN: only files git tracks — never anything ignored or untracked lying in a checkout —
less what is not the app's to ship:
  * tests (but the arm limits fixture, the composer's TEST limits), and the choreography deploy
    files (the robot's, not a laptop's);
  * software/face-web/vendor/ (MediaPipe, git-ignored anyway: camera tracking is off in the studio);
  * software/face-web/test/ (a bench-only tracking stimulus; the studio never loads it).
The staging tree is built in a temporary folder, never in the checkout. CI writes it with --stage,
runs the Hygiene lane's release gate over it (tools/hygiene/check_tree.py), and builds the wheel
from that same tree with --from-stage, so what was checked is what ships. A release is cut only from
a `studio-v*` tag, which the maintainers push by hand.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SUBTREES = ("software/choreo", "software/face-web", "software/head-protocol", "sim", "description")
EXTRA = ("software/openarm/limits/controllers.yaml",)    # the arm controllers' box and limits (Arms)
EXCLUDE = ("software/choreo/tests/", "software/choreo/deploy/", "software/choreo/studio/",
           "software/face-web/vendor/", "software/face-web/test/", "software/face-web/tests/",
           "software/head-protocol/tests/")
KEEP = ("software/choreo/tests/joint_limits_fixture.yaml",)   # the composer's TEST limits, when no robot's
DEPS = ["pyyaml", "numpy", "mujoco", "glfw", "websockets", "psutil", "platformdirs", "pillow"]

PYPROJECT = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "opendubs-studio"
version = "{version}"
description = "The OpenDubs choreography studio: composer, face twin and MuJoCo body twin"
requires-python = ">=3.11"
dependencies = {deps}
license = {{ text = "See the licence files inside opendubs_studio/tree" }}

[project.scripts]
opendubs-studio = "opendubs_studio.studio:main"

[tool.hatch.build.targets.wheel]
packages = ["opendubs_studio"]
"""


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", *SUBTREES, *EXTRA],
                         capture_output=True, check=True).stdout.decode()
    files = [f for f in out.split("\0") if f]
    return sorted(f for f in files if (f in KEEP or not any(f.startswith(x) for x in EXCLUDE))
                  and "__pycache__" not in f and (REPO / f).is_file())


def version() -> str:
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=10", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "unknown"
    return f"0.1.0+g{sha}"


def stage(dest: Path, ver: str) -> list[str]:
    pkg = dest / "opendubs_studio"
    (pkg / "tree").mkdir(parents=True)
    shutil.copy2(HERE / "studio.py", pkg / "studio.py")
    (pkg / "__init__.py").write_text(f'"""The OpenDubs choreography studio."""\n__version__ = "{ver}"\n')
    files = tracked()
    for f in files:
        to = pkg / "tree" / f
        to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / f, to)
    (dest / "pyproject.toml").write_text(PYPROJECT.format(version=ver, deps=DEPS))
    (dest / "README.md").write_text("The OpenDubs choreography studio. Run `opendubs-studio up`.\n")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=None, help="where the wheel goes (default: a new temporary folder)")
    ap.add_argument("--version", default=None, help="default: 0.1.0+g<commit>")
    ap.add_argument("--list", action="store_true", help="print what would go in, and stop")
    ap.add_argument("--stage", metavar="DIR", help="only write the staging tree to DIR (for the hygiene gate)")
    ap.add_argument("--from-stage", metavar="DIR", help="build the wheel from a staging tree already written")
    a = ap.parse_args()
    if a.list:
        print("\n".join(tracked()))
        return 0
    if a.stage:
        d = Path(a.stage).resolve()
        if d == REPO or REPO in d.parents or (d.exists() and any(d.iterdir())):
            sys.exit(f"--stage {d}: must be a new or empty folder outside the checkout")
        files = stage(d, a.version or version())
        print(f"staged {len(files)} files from the repository in {d}")
        return 0
    if a.from_stage:
        out = Path(a.out or tempfile.mkdtemp(prefix="opendubs-studio-dist-")).resolve()
        r = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), str(Path(a.from_stage).resolve())])
        if r.returncode:
            return r.returncode
        print(f"built {sorted(out.glob('opendubs_studio-*.whl'))[-1]}")
        return 0
    out = Path(a.out or tempfile.mkdtemp(prefix="opendubs-studio-dist-")).resolve()
    if out == REPO or REPO in out.parents and not a.out:
        sys.exit("refusing to build inside the checkout without --out")
    ver = a.version or version()
    with tempfile.TemporaryDirectory(prefix="opendubs-studio-stage-") as td:
        files = stage(Path(td), ver)
        r = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), td])
        if r.returncode:
            return r.returncode
    whl = sorted(out.glob("opendubs_studio-*.whl"))[-1]
    print(f"built {whl} ({whl.stat().st_size / 1e6:.1f} MB, {len(files)} files from the repository)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
