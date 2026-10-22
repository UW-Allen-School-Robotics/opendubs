#!/usr/bin/env python3
"""Personal-name check for the hygiene workflow.

    HYGIENE_NAME_DENYLIST="<one word per line>" \\
        python3 tools/hygiene/check_names.py --base <git ref> [--mode warn|block]

Project rule: committed files, commit messages and PRs say "the project team" or "the
maintainers", never a person's name (CLAUDE.md). This checks the lines a change ADDS, and the
messages of the commits it adds, against a deny-list of names, usernames and personal email
addresses.

**The deny-list is never committed.** It comes from the `HYGIENE_NAME_DENYLIST` environment
variable, which the workflow fills from a repository Actions secret of the same name, so the
check itself publishes no names. For the same reason a finding prints the file and line but
never the word that matched.

**The deny-list is not configured, by the maintainers' decision (2026-10-14).** Personal names are
caught by review and by the project's no-personal-names rule, not by CI, so this check prints
"skipped: no deny-list configured" and passes. Setting the secret turns it on with no other change.

Not flagged: `Signed-off-by:` and `Co-Authored-By:` trailers (commit metadata, which the rule
allows), copyright lines in licence files, which the licences require, and the root `AUTHORS`
file, the one place contributors are named on purpose.

Matching is case-insensitive and on whole words. Prefer distinctive entries (surnames,
usernames, email addresses) over common first names, which also occur in third-party text.
"""

import argparse
import os
import re
import subprocess
import sys

ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
TRAILER = re.compile(r"^\s*(signed-off-by|co-authored-by)\s*:", re.I)
LICENCE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE)[^/]*$", re.I)
COPYRIGHT = re.compile(r"copyright|spdx-filecopyrighttext|\(c\)|©", re.I)


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          errors="replace").stdout


def load_denylist():
    raw = os.environ.get("HYGIENE_NAME_DENYLIST", "")
    words = sorted({w.strip().lower() for w in raw.replace(",", "\n").splitlines() if w.strip()})
    if not words:
        return None
    return re.compile(r"(?<![\w.@-])(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w@-])",
                      re.I)


def added_lines(base):
    """Yield (path, line number, text) for every line the change adds."""
    path, lineno = None, 0
    for line in git("diff", "--unified=0", "--no-color", "--no-ext-diff", f"{base}...HEAD").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            lineno = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            yield path, lineno, line[1:]
            lineno += 1


def commit_messages(base):
    out = git("log", "--format=%H%x00%B%x01", f"{base}..HEAD")
    for rec in out.split("\x01"):
        if "\x00" in rec:
            sha, body = rec.strip("\n").split("\x00", 1)
            yield sha[:12], body


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="git ref the change is compared against")
    ap.add_argument("--mode", choices=("warn", "block"), default="warn")
    a = ap.parse_args()

    deny = load_denylist()
    if deny is None:
        msg = "personal names: skipped: no deny-list configured (HYGIENE_NAME_DENYLIST is unset)"
        print(f"::notice::{msg}" if ACTIONS else msg)
        return 0

    level = "error" if a.mode == "block" else "warning"
    hits = 0
    for path, n, text in added_lines(a.base):
        if path == "AUTHORS":                  # the one file that names contributors, on purpose
            continue
        if LICENCE_FILE.search(path) and COPYRIGHT.search(text):
            continue
        if deny.search(text):
            hits += 1
            msg = "adds a personal name, username or email: say 'the project team' or 'the maintainers'"
            print(f"::{level} file={path},line={n}::{msg}" if ACTIONS else f"{path}:{n}: {msg}")
    for sha, body in commit_messages(a.base):
        for i, line in enumerate(body.splitlines(), 1):
            if not TRAILER.match(line) and deny.search(line):
                hits += 1
                msg = f"commit {sha}, message line {i}, names a person"
                print(f"::{level}::{msg}" if ACTIONS else msg)

    print(f"hygiene names: {hits} finding(s)")
    return 1 if hits and a.mode == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
