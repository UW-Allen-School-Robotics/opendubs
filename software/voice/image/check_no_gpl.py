#!/usr/bin/env python3
"""Refuse an environment or image that contains GPL code: the voice is English only, no GPL.

    check_no_gpl.py [--root DIR ...]      exit 0 if clean, 1 with every finding listed

The project team decided (2026-10-14) that no GPL component ships with the voice. The ones that
matter are espeak-ng (GPL-3.0+) and phonemizer (GPL-3.0+), which Kokoro's default install pulls in
through misaki's `[en]` extra, and which `espeakng-loader` bundles as a binary that declares no
licence at all. This check runs as the last step of the image build (Dockerfile.voice), so an
image that fails it is never produced, and in CI against the pinned speech requirements.

What it refuses:

1. **Named packages**, whatever their metadata says: phonemizer, phonemizer-fork, espeakng-loader.
2. **Any installed Python distribution whose licence metadata says GPL or AGPL** (License-Expression,
   License, or the trove classifiers). LGPL is a different licence and is allowed **only** when
   listed in ALLOWED_LGPL with its reason, so a new one is reviewed rather than slipped in.
3. **espeak-ng on disk**: any `libespeak*` library or `espeak-ng-data` directory under the Python
   paths and the --root directories (default: /usr/lib, /usr/local/lib, /usr/share, /opt).
4. **espeak-ng from the system package manager**: any espeak package in /var/lib/dpkg/status.

Standard library only.
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import re
import sys
import sysconfig

BANNED = {"phonemizer", "phonemizer-fork", "espeakng-loader", "espeak-ng", "espeak"}
# LGPL packages reviewed and allowed, with why. A Python package imported unmodified from its own
# installed files keeps LGPL's terms easy to meet: its licence ships with it and it can be replaced.
ALLOWED_LGPL = {
    "num2words": "spells numbers for misaki's English G2P; LGPL-2.1, used unmodified",
}
LGPL = re.compile(r"LGPL|Lesser General Public|Library General Public|Library or Lesser", re.I)
GPL = re.compile(r"\bA?GPL|General Public License|Affero", re.I)
NOT_GPL = re.compile(r"GPL[- ]compatible", re.I)
FILES = re.compile(r"^libespeak", re.I)
DIRS = {"espeak-ng-data", "espeak-data"}
DEFAULT_ROOTS = ("/usr/lib", "/usr/local/lib", "/usr/share", "/opt")
DPKG_STATUS = "/var/lib/dpkg/status"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_text(dist) -> str:
    m = dist.metadata
    parts = [m.get("License-Expression") or "", (m.get("License") or "")[:400]]
    parts += [c for c in (m.get_all("Classifier") or []) if c.startswith("License")]
    return " | ".join(p for p in parts if p)


def classify(text: str) -> str:
    """'gpl', 'lgpl' or 'ok' for a distribution's licence metadata."""
    t = NOT_GPL.sub("", text)
    if LGPL.search(t):
        rest = LGPL.sub("", t)            # "GPL" left after removing the LGPL phrases is a real GPL
        return "gpl" if re.search(r"\bA?GPL\b|Affero", rest) else "lgpl"
    return "gpl" if GPL.search(t) else "ok"


def check_distributions(dists=None) -> list[str]:
    found = []
    for d in dists if dists is not None else md.distributions():
        name = canonical(d.metadata.get("Name") or "")
        if not name:
            continue
        if name in BANNED:
            found.append(f"package {name} is banned (GPL espeak-ng / phonemizer)")
            continue
        kind = classify(licence_text(d))
        if kind == "gpl":
            found.append(f"package {name} is GPL-licensed: {licence_text(d)[:120]}")
        elif kind == "lgpl" and name not in ALLOWED_LGPL:
            found.append(f"package {name} is LGPL and not reviewed (add it to ALLOWED_LGPL with a reason, "
                         f"or remove it): {licence_text(d)[:120]}")
    return found


def check_files(roots) -> list[str]:
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            for d in list(dirnames):
                if d in DIRS:
                    found.append(f"espeak-ng data at {os.path.join(dirpath, d)}")
                    dirnames.remove(d)
            for f in filenames:
                if FILES.match(f):
                    found.append(f"espeak-ng library at {os.path.join(dirpath, f)}")
    return found


def check_dpkg(status: str = DPKG_STATUS) -> list[str]:
    if not os.path.exists(status):
        return []
    with open(status, encoding="utf-8", errors="replace") as f:
        pkgs = re.findall(r"^Package: (\S+)\n(?:.+\n)*?Status: (.+)$", f.read(), re.M)
    return [f"system package {p} is installed" for p, st in pkgs
            if "espeak" in p and "installed" in st and "not-installed" not in st]


def python_paths() -> list[str]:
    paths = {sysconfig.get_paths()[k] for k in ("purelib", "platlib")}
    return sorted(p for p in paths if p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", action="append", help="also scan this directory (repeatable)")
    ap.add_argument("--no-system", action="store_true", help="skip the system directories and dpkg (CI)")
    a = ap.parse_args(argv)
    roots = python_paths() + (a.root or []) + ([] if a.no_system else list(DEFAULT_ROOTS))
    findings = check_distributions() + check_files(roots) + ([] if a.no_system else check_dpkg())
    if findings:
        print("check_no_gpl: REFUSED. The voice is English only and ships no GPL code:", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"check_no_gpl: OK ({sum(1 for _ in md.distributions())} packages, {len(roots)} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
