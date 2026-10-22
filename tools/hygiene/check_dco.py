#!/usr/bin/env python3
"""Developer Certificate of Origin check for the hygiene workflow.

    DCO_SINCE=2026-10-23 python3 tools/hygiene/check_dco.py --base <git ref>

Every commit the change adds (`<base>..HEAD`) must carry a `Signed-off-by:` trailer whose email
matches the commit's author email. That is the contributor certifying DCO.txt for that commit, and
`git commit -s` adds it (CONTRIBUTING.md).

Exempt:
* commits authored before DCO_SINCE, the day the requirement started, so history is never
  rewritten and an old branch rebased later still passes for its old commits;
* commits whose committer is GitHub itself (`noreply@github.com`): the merge and squash commits
  GitHub creates when a PR merges. This also covers an edit made in GitHub's web editor, so sign
  off there with GitHub's "Sign off and commit" option.

A finding prints the commit's short hash and the reason, never a name or an email. Exit status 1
if any commit fails.
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys

ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"
SIGNOFF = re.compile(r"^Signed-off-by:.*<([^>]+)>\s*$", re.I | re.M)
GITHUB_COMMITTER = "noreply@github.com"


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          errors="replace").stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", required=True, help="git ref the change is compared against")
    a = ap.parse_args()

    since = os.environ.get("DCO_SINCE", "").strip()
    if not since:
        sys.exit("DCO_SINCE is not set: the date the sign-off requirement started (YYYY-MM-DD)")
    since_dt = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc) \
        if len(since) == 10 else dt.datetime.fromisoformat(since)

    log = git("log", "--format=%H%x00%aI%x00%ae%x00%ce%x00%B%x01", f"{a.base}..HEAD")
    checked = exempt = 0
    bad = []
    for rec in log.split("\x01"):
        if "\x00" not in rec:
            continue
        sha, adate, aemail, cemail, body = rec.strip("\n").split("\x00", 4)
        if cemail.lower() == GITHUB_COMMITTER or dt.datetime.fromisoformat(adate) < since_dt:
            exempt += 1
            continue
        checked += 1
        signed = {e.strip().lower() for e in SIGNOFF.findall(body)}
        if not signed:
            bad.append((sha[:12], "no Signed-off-by line"))
        elif aemail.lower() not in signed:
            bad.append((sha[:12], "Signed-off-by does not match the commit's author email"))

    for sha, why in bad:
        msg = f"commit {sha}: {why}. Fix: git rebase --signoff origin/main (see CONTRIBUTING.md)"
        print(f"::error::{msg}" if ACTIONS else msg)
    print(f"hygiene dco: {checked} commit(s) checked, {exempt} exempt, {len(bad)} unsigned")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
