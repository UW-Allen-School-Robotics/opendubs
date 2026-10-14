#!/usr/bin/env python3
"""Hygiene gate for a directory that is about to be shipped (a release bundle's staging tree).

    python3 tools/hygiene/check_tree.py --root <staging dir> --allow-no-denylist [--map STAGED=REPO ...]

--allow-no-denylist is the project's standard call: no deny-list is configured (see Personal names).

The PR checks (check_files.py, check_names.py, gitleaks in hygiene.yml) look at what a change adds
to git. A bundle is different: it ships files as they are on disk, including anything generated or
copied in while staging. This runs the same rules over EVERY file under --root, and fails if any of
them trips:

* **Secrets:** `gitleaks dir` with the project config (tools/hygiene/gitleaks.toml), fully
  redacted, so a match never reaches the log. Needs gitleaks on PATH
  (tools/hygiene/install-gitleaks.sh installs the pinned build).
* **Git-ignored files:** a staged file the repo's ignore rules exclude, such as show music, a
  vendor download, a build output or a key file. It is evaluated with the repo's own .gitignore
  files, nested ones included, at the file's repo path.
* **Personal names:** the deny-list from the HYGIENE_NAME_DENYLIST environment variable, the same
  Actions secret the PR check uses. Copyright lines in licence files are exempt. Findings print the
  file and line, never the word.
  **The deny-list is not configured, by the maintainers' decision (2026-10-14):** names are caught
  by review and by the no-personal-names rule, not by CI. So callers pass --allow-no-denylist, and
  the check reports "skipped: no deny-list configured" and passes. Without the flag, an unset
  deny-list fails, so a caller has to opt in to running without one.
* **File size:** over 2 MiB, unless the file's repo path is in tools/hygiene/size-allow.txt.
* **Image metadata:** any image carrying GPS coordinates. Needs exiftool on PATH.

**Repo paths.** The ignore rules and the size allow-list are written against repo paths. If the
staging tree mirrors the repo's layout (`<root>/software/choreo/...`), nothing else is needed.
If it renames directories, say how with --map, once per prefix, most specific first:

    --map opendubs_studio/choreo=software/choreo --map opendubs_studio/sim=sim

A staged file whose repo path git does not track is listed as a notice. It is not an error,
because generated files are legitimate, but the list shows what the bundle adds beyond the repo.

Exit status is 1 if anything fails, else 0. Run it locally the same way before tagging.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_files import CAMERA_TAGS, GPS_TAGS, IMAGES, LIMIT, size_allowed  # noqa: E402
from check_names import COPYRIGHT, LICENCE_FILE, load_denylist  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIG = REPO / "tools/hygiene/gitleaks.toml"
ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
failures = 0


def report(level, path, msg, line=None):
    global failures
    if level == "error":
        failures += 1
    loc = f"{path}:{line}" if line else path
    if ACTIONS:
        print(f"::{level} title=bundle hygiene::{loc}: {msg}")
    print(f"{level.upper():8} {loc}: {msg}")


def staged_files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink())


def to_repo(rel, maps):
    for staged, repo in maps:
        if rel == staged or rel.startswith(staged + "/"):
            return repo + rel[len(staged):]
    return rel


def git_lines(*args, stdin=None):
    r = subprocess.run(["git", "-C", str(REPO), *args], input=stdin, capture_output=True,
                       text=True)
    return [l for l in r.stdout.split("\0") if l]


def check_secrets(root):
    if not shutil.which("gitleaks"):
        report("error", "tools/hygiene/check_tree.py", "gitleaks is not on PATH "
               "(tools/hygiene/install-gitleaks.sh installs it)")
        return
    r = subprocess.run(["gitleaks", "dir", "--no-banner", "--redact=100", "--verbose",
                        "--config", str(CONFIG), str(root)], capture_output=True, text=True)
    if r.returncode != 0:
        # Redacted output: rule, file and line only. Print it so the finding can be located.
        print(r.stdout + r.stderr)
        report("error", str(root), "gitleaks found a secret (see the redacted report above)")


def check_ignored(files, maps):
    repo_of = {to_repo(f, maps): f for f in files}
    ignored = git_lines("check-ignore", "--no-index", "-z", "--stdin", stdin="\0".join(repo_of))
    for rp in ignored:
        report("error", repo_of.get(rp, rp), f"excluded by the repo's ignore rules (as {rp}); "
               "a bundle must not ship it")
    tracked = set(git_lines("ls-files", "-z"))
    # The ignore rules only mean something at the right repo path: a nested rule (show music under
    # software/choreo/shows/audio/) is missed entirely if the staged layout renames that directory
    # and --map does not say so. A tree where most files land on no tracked path is a layout the
    # check cannot read, so it fails rather than passing blind.
    if files and sum(rp in tracked for rp in repo_of) < len(files) / 2:
        report("error", str(len(files)) + " file(s)", "most staged paths match no tracked repo path, "
               "so the ignore rules cannot be applied: pass --map STAGED=REPO for each renamed "
               "directory")
    extra = [f for rp, f in repo_of.items() if rp not in tracked and rp not in ignored]
    if extra:
        report("notice", str(len(extra)) + " file(s)", "not tracked in git, so generated or copied "
               "in while staging: " + ", ".join(extra[:20]) + (" …" if len(extra) > 20 else ""))


def check_names(root, files, allow_none):
    deny = load_denylist()
    if deny is None:
        if allow_none:
            report("notice", "names", "skipped: no deny-list configured")
        else:
            report("error", "names", "no deny-list configured, and --allow-no-denylist was not "
                   "given. The project runs without one (names are caught in review), so pass "
                   "--allow-no-denylist")
        return
    for f in files:
        data = (root / f).read_bytes()
        if b"\0" in data[:8192]:
            continue                                    # binary
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if LICENCE_FILE.search(f) and COPYRIGHT.search(line):
                continue
            if deny.search(line):
                report("error", f, "a personal name, username or email: say 'the project team' "
                       "or 'the maintainers'", line=n)


def check_sizes(root, files, maps):
    import fnmatch
    allowed = size_allowed()
    for f in files:
        size = (root / f).stat().st_size
        rp = to_repo(f, maps)
        if size > LIMIT and not any(fnmatch.fnmatch(rp, pat) for pat in allowed):
            report("error", f, f"{size / 1048576:.1f} MiB is over the {LIMIT // 1048576} MiB limit")


def check_exif(root, files):
    images = [f for f in files if f.lower().endswith(IMAGES)]
    if not images:
        return
    if not shutil.which("exiftool"):
        report("error", "tools/hygiene/check_tree.py", "exiftool is not on PATH")
        return
    out = subprocess.run(["exiftool", "-j", "-q", *(f"-{t}" for t in GPS_TAGS + CAMERA_TAGS),
                          *images], cwd=root, capture_output=True, text=True).stdout
    for rec in json.loads(out or "[]"):
        if any(t in rec for t in GPS_TAGS):
            report("error", rec["SourceFile"], "carries GPS coordinates")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=pathlib.Path, help="the staging directory")
    ap.add_argument("--map", action="append", default=[], metavar="STAGED=REPO",
                    help="a staged path prefix and the repo path it came from")
    ap.add_argument("--allow-no-denylist", action="store_true",
                    help="skip the name check when no deny-list is configured (the project default)")
    a = ap.parse_args()

    root = a.root.resolve()
    if not root.is_dir():
        sys.exit(f"--root {root} is not a directory")
    maps = []
    for m in a.map:
        staged, sep, repo = m.partition("=")
        if not sep:
            sys.exit(f"--map {m!r}: expected STAGED=REPO")
        maps.append((staged.strip("/"), repo.strip("/")))

    files = staged_files(root)
    check_secrets(root)
    check_ignored(files, maps)
    check_names(root, files, a.allow_no_denylist)
    check_sizes(root, files, maps)
    check_exif(root, files)
    print(f"bundle hygiene: {len(files)} file(s) under {root}; {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
