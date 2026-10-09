#!/usr/bin/env python3
"""File checks for the hygiene workflow: ignore rules, file size, image metadata.

    python3 tools/hygiene/check_files.py --base <git ref> [--size warn|block] [--exif warn|block]

* **Ignore rules** (always blocking): nothing tracked may match the repo's ignore rules
  (`git ls-files -ci --exclude-standard`). A match means a build output, download, cache or
  secret file was committed, or an ignore rule now contradicts the tree.
* **File size**: a file added or changed since `--base` that is larger than the limit
  (2 MiB) fails unless it is listed in `tools/hygiene/size-allow.txt`. Large files belong in a
  release or on the shared Drive, not in git history, where they stay forever.
* **Image metadata**: an image added or changed since `--base` must not carry GPS coordinates.
  Camera make, model and serial number are reported as notes. Strip metadata but keep
  `Orientation` and the ICC profile, or a phone photo can display sideways:

      exiftool -all= -tagsfromfile @ -Orientation -icc_profile -overwrite_original <file>

  Needs `exiftool` on PATH.

Exit status is 1 if any blocking check fails, else 0. Under GitHub Actions each finding is also
printed as a workflow annotation. Run it locally the same way before pushing.
"""

import argparse
import fnmatch
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOW = ROOT / "tools/hygiene/size-allow.txt"
LIMIT = 2 * 1024 * 1024
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff")
GPS_TAGS = ("GPSLatitude", "GPSLongitude", "GPSPosition", "GPSCoordinates")
CAMERA_TAGS = ("Make", "Model", "SerialNumber", "LensSerialNumber", "OwnerName")

ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
failed = False


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout


def report(level, path, msg, block):
    """Print one finding. `level` is error/warning/notice; a blocking error sets the exit status."""
    global failed
    if level == "error" and not block:
        level = "warning"
    if level == "error":
        failed = True
    if ACTIONS:
        print(f"::{level} file={path}::{msg}")
    print(f"{level.upper():8} {path}: {msg}")


def changed_files(base):
    out = git("diff", "--name-only", "--diff-filter=ACMR", "-z", f"{base}...HEAD")
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def size_allowed():
    if not ALLOW.exists():
        return []
    lines = (l.split("#", 1)[0].strip() for l in ALLOW.read_text().splitlines())
    return [l for l in lines if l]


def check_ignored():
    tracked_ignored = [p for p in git("ls-files", "-ci", "--exclude-standard", "-z").split("\0") if p]
    for p in tracked_ignored:
        report("error", p, "tracked, but the ignore rules say it should not be: remove it from git "
               "(git rm --cached) or fix the ignore rule", block=True)
    return len(tracked_ignored)


def check_sizes(files, block):
    allowed = size_allowed()
    n = 0
    for p in files:
        size = (ROOT / p).stat().st_size
        if size > LIMIT and not any(fnmatch.fnmatch(p, pat) for pat in allowed):
            n += 1
            report("error", p, f"{size / 1048576:.1f} MiB is over the {LIMIT // 1048576} MiB limit: "
                   "shrink it, keep it outside git, or add it to tools/hygiene/size-allow.txt with a "
                   "reason", block)
    return n


def check_exif(files, block):
    images = [p for p in files if p.lower().endswith(IMAGES)]
    if not images:
        return 0
    if not shutil.which("exiftool"):
        report("error", "tools/hygiene/check_files.py", "exiftool is not installed", block=True)
        return 1
    out = subprocess.run(["exiftool", "-j", "-q", *(f"-{t}" for t in GPS_TAGS + CAMERA_TAGS),
                          *images], cwd=ROOT, capture_output=True, text=True).stdout
    n = 0
    for rec in json.loads(out or "[]"):
        p = rec.get("SourceFile")
        # Never print the coordinates or the serial number: the log is visible to every reader.
        if any(t in rec for t in GPS_TAGS):
            n += 1
            report("error", p, "carries GPS coordinates: strip its metadata (see this script's "
                   "docstring for the command that keeps Orientation)", block)
        cam = [t for t in CAMERA_TAGS if t in rec]
        if cam:
            report("notice", p, "carries camera metadata (" + ", ".join(cam) + "): consider "
                   "stripping it", block=False)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="git ref the change is compared against")
    ap.add_argument("--size", choices=("warn", "block"), default="warn")
    ap.add_argument("--exif", choices=("warn", "block"), default="warn")
    a = ap.parse_args()

    files = changed_files(a.base)
    n_ign = check_ignored()
    n_size = check_sizes(files, a.size == "block")
    n_exif = check_exif(files, a.exif == "block")
    print(f"hygiene files: {len(files)} changed file(s) checked; "
          f"{n_ign} ignored-but-tracked, {n_size} oversized, {n_exif} with GPS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
